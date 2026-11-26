# Migración gVisor → Firecracker (boundary de hardware)

`firecracker` (KVM) reemplaza al Sentry user-space de gVisor por un VMM mínimo que corre el guest
en modo invitado real (ring −1). Escapar exige un bug del VMM/KVM, no de un "kernel" en user-space.
Es el mayor salto de seguridad disponible sin hardware dedicado.

## Por qué esto acerca al 100%

| | gVisor (actual) | Firecracker (objetivo) |
|---|---|---|
| Boundary | user-space (Sentry) | hardware (KVM/ring −1) |
| Superficie | Sentry + gofer + syscall emulation | VMM mínimo (~50k LOC) |
| Escape requiere | bug del Sentry | bug de KVM/Firecracker |

## Qué hay en esta carpeta

| Archivo | Qué hace |
|---|---|
| `config.json` | Config estática del VMM: vsock (guest_cid único), sin red, rootfs read-only, 1 vCPU + 512 MiB. |
| `lanzar.py` | Entrypoint: verifica binario+config y arranca firecracker en foreground (systemd lo gestiona). |
| `hcma-firecracker.service` | Unidad systemd fail-closed (Restart=no, MemoryMax, TasksMax, BindsTo monitor). |
| `auditd.rules` | Señal fuera de banda: auditd vigila red/secretos/privs del sandbox → `observador_audit.py`. |
| `deploy.sh` | Despliegue root idempotente (firecracker + kernel + rootfs + grupo + permisos + auditd + unit). |

## Despliegue (root, una vez)

```bash
# kernel huésped mínimo (vmlinux) + rootfs ext4 mínimo con el agente + python.
# Se pasan por argumento/env; si ya están en /var/lib/hcma-firecracker, deploy.sh los conserva.
sudo HCMA_VMLINUX=/ruta/vmlinux HCMA_ROOTFS=/ruta/rootfs.ext4 \
  sh /home/charasky/Documentos/HMC/arquitectura/firecracker/deploy.sh
```

`deploy.sh` instala firecracker (apt), ubica kernel+rootfs en `/var/lib/hcma-firecracker/`, crea el
grupo `hcma-audit`, setea los permisos read-only del store, instala las reglas auditd y la unidad
systemd, y recarga el daemon. Es **idempotente** (se puede correr de nuevo sin romper nada).

## Comunicación agente ↔ monitor

Hoy el agente habla por un **socket heredado** (FD pass de runsc). Firecracker no tiene FD-pass:
usa **virtio-vsock**. El shim ya está implementado en el monitor:

```
agente (guest) ── vsock ──▶ firecracker ── uds (/run/hcma/vsock.sock) ──▶ monitor
```

- **Host (monitor)**: `hcma_monitor_daemon._crear_socket_agente` hace `bind` + `listen` + `0600`
  sobre `/run/hcma/vsock.sock` cuando `HCMA_VSOCK_UDS` está seteado. El monitor es el DUEÑO del
  UDS (su ciclo de vida muere con el proceso del monitor: fail-closed).
- **VMM (firecracker)**: se **conecta** a ese UDS cuando el guest inicia una conexión vsock
  (CID 2). `lanzar.py` NO crea ni deslinkea el UDS (eso lo hace el monitor).
- **Guest**: conecta a `vsock` CID=2 (host), puerto 5000, y habla el MISMO framing `monitor_ipc`.

El framing (`monitor_ipc`), el `session_id`, el reclaim, el saludo y la gobernanza **no cambian**:
solo cambia el transporte (vsock en vez de FD heredado). La blacklist (`vigilancia_gvisor`) y
`observador_audit.py` (señal fuera de banda) siguen igual.

## Config estática (`config.json`)

`vsock` con `guest_cid` único por agente; `network-interfaces` vacío (sin red, como hoy);
`drives` con rootfs read-only; `machine-config` con 1 vCPU + 512 MiB (matching el `MemoryMax`
de la unidad).

## Señal fuera de banda (auditd)

`auditd.rules` hace que el kernel emita eventos para las syscalls del sandbox (`runsc`/gofer o
`firecracker`) que indican escape, independientes del Sentry/VMM:

1. **Red** — el sandbox no tiene red; cualquier `connect`/`socket`/`sendto`/`recvfrom` desde el
   binario del sandbox es escape/exfiltración.
2. **Secretos** — watches de paths sobre la clave del firmante y el estado del monitor/proxy.
3. **Privilegios** — `setuid`/`setgid`/`capset` (se excluye `mount`/`unshare`/`ptrace` porque
   runsc los usa legítimamente al armar el sandbox).

`observador_audit.py` parsea el resultado (`avc: denied {...}` y `syscall ... success=no`) y
`observador_kernel.py --clasificador audit` lo reporta al monitor por la op `procesar_log_audit`.
El watcher corre como systemd (`hcma-audit-watcher.service`, instalado por `deploy.sh`) y hace
tail de `/var/log/audit/audit.log`, alimentando `incidentes.registrar` + bloqueo.

El monitor, en modo vsock, escucha en **dos** sockets de agente: el regular
(`/run/hcma/monitor.sock`, para los watchers del host) y el UDS del vsock
(`/run/hcma/vsock.sock`, para Firecracker). Así el watcher de auditd conserva su canal incluso
cuando el agente migra de gVisor a Firecracker.

## Cutover (decisión humana)

`deploy.sh` NO apaga gVisor ni arranca Firecracker automáticamente. El cutover es manual:

```bash
# 1. monitor en modo vsock (drop-in, no se edita el .service original)
sudo mkdir -p /etc/systemd/system/hcma-monitor.service.d
printf '[Service]\nEnvironment=HCMA_VSOCK_UDS=/run/hcma/vsock.sock\n' | \
  sudo tee /etc/systemd/system/hcma-monitor.service.d/vsock.conf

# 2. apagar gVisor y recargar el monitor con vsock
sudo systemctl stop hcma-agent-sandbox.service
sudo systemctl restart hcma-monitor.service

# 3. arrancar la microVM
sudo systemctl start hcma-firecracker.service
sudo systemctl enable hcma-firecracker.service   # opcional, para que quede fijo
```

Mientras Firecracker no esté desplegado, el sandbox gVisor + `observador_audit.py` (señal fuera
de banda) es la defensa vigente.

## Honestidad (no existe el 100%)

Firecracker sube la barrera de escape de "bug del Sentry" a "bug del VMM/KVM", el máximo
alcanzable sin hardware dedicado. **No elimina** la posibilidad de escape: sigue habiendo una
superficie (el VMM ~50k LOC + KVM). La afirmación honesta es "reduce la probabilidad a 'requiere
un bug del hypervisor'", no "es imposible". Ver `consultas/modelo_amenazas.md`.

## Construcción de la imagen huésped (vmlinux + rootfs.ext4)

La microVM necesita dos artefactos que **no se versionan** (se regeneran con los scripts):

| Script | Produce | Requisitos |
|---|---|---|
| `build_vmlinux.sh` | `vmlinux` (kernel ELF mínimo: virtio-blk/vsock/ext4/serial built-in) | gcc make bc bison flex libelf-dev |
| `build_rootfs_firecracker.sh` | `rootfs.ext4` (Debian minbase + python3 + agente + /init) | mmdebstrap, e2fsprogs |

```bash
# 1) kernel huésped
sudo sh build_vmlinux.sh              # → ./vmlinux

# 2) rootfs
sudo sh build_rootfs_firecracker.sh   # → ./rootfs.ext4

# 3) desplegar pasando los artefactos
sudo HCMA_VMLINUX=$PWD/vmlinux HCMA_ROOTFS=$PWD/rootfs.ext4 sh deploy.sh
```

El `config.json` ya incluye el dispositivo `vsock` (`guest_cid=3`,
`uds_path=/run/hcma/vsock.sock`) y `init=/init` en `boot_args`. El `guest_init` monta
`/proc` `/sys` `/dev` y arranca `agente_vsock.py`, que conecta a `CID_HOST=2` puerto `5000`.

### Transporte vsock (guest → host)

Firecracker mapea cada puerto vsock del guest a un UDS **por puerto**: `<uds_path>_<puerto>`.
El agente conecta a `CID=2` puerto `5000`, así que Firecracker reenvía a
`/run/hcma/vsock.sock_5000` (passthrough crudo, sin encabezado binario). El monitor escucha en
esa ruta (`HCMA_VSOCK_UDS=/run/hcma/vsock.sock_5000`, seteado por `instalar.sh`); `config.json`
solo aporta la base `/run/hcma/vsock.sock`.

## Estado: validado end-to-end (2026-11-26)

El camino completo quedó verificado en un host Debian 14 (gcc 16, kernel 7.2):

```
agente (guest Debian) ── virtio-vsock (CID=2:5000) ──▶ firecracker ──▶ /run/hcma/vsock.sock_5000 ──▶ monitor
```

- `hcma-firecracker` → `active`: la microVM bootea Debian, monta el rootfs ext4 read-only (`vda`) y ejecuta `/init`.
- `hcma-px-motor` → `active`: eBPF cargado y política firmada verificada.
- El agente conecta por vsock y el monitor responde (`estado` + `propuesta_write` → `ok: True`).

### Ajustes del kernel huésped (gcc >= 15 y Firecracker)

`build_vmlinux.sh` los aplica solo:

- `-std=gnu11` forzado en `realmode` y `EFI stub` (gcc 15+ usa C23 y rompe `bool`/`false`/`true`).
- `VIRTIO_MMIO` (no `VIRTIO_PCI`): Firecracker expone los dispositivos por MMIO.
- `VSOCKETS` + `VIRTIO_VSOCKETS` + `VIRTIO_VSOCKETS_COMMON` built-in.
- `SYSTEM_TRUSTED_KEYRING` / `SYSTEM_REVOCATION_LIST` / `SECONDARY_TRUSTED_KEYRING` off (evita OpenSSL).
- `DEBUG_INFO` / `DEBUG_INFO_BTF` off (evita `pahole`, compila más rápido).
- `WERROR` off (los warnings nuevos de gcc 16 no se vuelven error).
- `MODULES` off (todo built-in).

### Ajustes del host (socket vsock)

- `hcma-firecracker.service`: `ReadWritePaths=/run/hcma` para que `ProtectSystem=strict` no deje el socket base read-only.
- El monitor chown `/run/hcma` → grupo `hcma-vsock` + `chmod 0771` **al inicio** de `main()`, para evitar la carrera con el arranque de Firecracker.

### Pendientes (no bloquean el core)

- `agente_vsock.py` es un demo (conecta → propone → sale); `guest_init` lo relanza cada 5s. El loop de servicio real del agente es el siguiente paso.
- La ceremonia de CORTE (checkpoint firmado + cutover del store real) sigue siendo manual, por diseño.
