# ERRORES.md — Requisitos y troubleshooting HCMA

Guía de referencia para el instalador de punta a punta (`sudo sh arquitectura/instalar.sh`).
Si algo falla, buscá el síntoma acá. Cada entrada tiene **síntoma → causa → solución**.

Diagnóstico rápido:

```bash
sudo sh arquitectura/montaje/verificar.sh               # salud completa
journalctl -u hcma-monitor     -n 50 --no-pager          # monitor + socket vsock
journalctl -u hcma-firecracker -n 50 --no-pager          # microVM (boot + vsock)
journalctl -u hcma-px-motor    -n 50 --no-pager          # detector eBPF
journalctl -u hcma-dashboard   -n 50 --no-pager          # dashboard / TLS
```

---

## 1. Requisitos (lo que valida `instalar.sh`)

El preflight de `instalar.sh` comprueba e instala automáticamente (en distros soportadas)
estas categorías:

| Categoría | Qué chequea | Paquete(s) |
|---|---|---|
| Runtime (servicios) | `python3 git go clang bpftool ss useradd groupadd usermod nft curl systemctl` | por distro (`distro.sh`) |
| BTF del kernel | `/sys/kernel/btf/vmlinux` | kernel con `CONFIG_DEBUG_INFO_BTF=y` (no es paquete) |
| Build kernel huésped (`vmlinux`) | `gcc make bc bison flex tar xz` + headers `libelf` | `BUILD_PKGS` / `LIBELF_PKG` |
| Build rootfs (`rootfs.ext4`) | `mmdebstrap mkfs.ext4` (solo Debian/Ubuntu) | `ROOTFS_PKGS` |
| KVM / vsock | `/dev/kvm`, `/dev/vhost-vsock`, flag `vmx`/`svm` | host/BIOS (no es paquete) |
| Artefactos microVM | `vmlinux` + `rootfs.ext4` (repo o `/var/lib/hcma-firecracker`) | `build_vmlinux.sh` / `build_rootfs_firecracker.sh` |

Si una distro no está en `distro.sh`, el instalador **no** auto-instala nada y lo dice:
instalá los paquetes a mano (los nombres están en `distro.sh`).

---

## 2. Entorno / paquetes

### `E: El repositorio «https://…» no está firmado` / `Missing key … needed to verify signature`
- **Causa**: un repo apt de terceros (Fortinet, etc.) sin firma quedó en
  `/etc/apt/sources.list.d/` y bloquea `apt-get update`.
- **Solución**: desactivá o remové ese repo y volvé a actualizar.
  ```bash
  ls /etc/apt/sources.list.d/
  sudo rm /etc/apt/sources.list.d/forticlient*.list      # o el repo que corresponda
  sudo apt-get update
  ```

### `distro no soportada` (el instalador no auto-instala paquetes)
- **Causa**: la distro no está mapeada en `distro.sh`.
- **Solución**: instalá a mano los paquetes de `distro.sh` para tu familia
  (mirá la columna correspondiente) y volvé a correr el instalador.

### Firecracker: `curl: (23) … ERROR on write` / `install: no se puede efectuar 'stat'`
- **Causa**: descarga parcial del binario de GitHub (o un `.tgz` viejo en `/tmp`).
- **Solución**: `deploy.sh` ya descarga de forma atómica con reintentos. Si insistís a mano:
  ```bash
  sudo rm -f /tmp/firecracker-*.tgz /tmp/fc-extract-* -r
  sudo sh arquitectura/firecracker/deploy.sh
  ```

---

## 3. KVM / Firecracker (boundary de hardware)

### `/dev/kvm ausente` o `sin vmx/svm en /proc/cpuinfo`
- **Causa**: virtualización por hardware deshabilitada en BIOS/UEFI, o (en un host de VM)
  sin *nested virt*.
- **Solución**: habilitá VT-x/AMD-V en BIOS. Para correr Firecracker *dentro* de una VM,
  activá nested virt:
  ```bash
  # host de QEMU: -cpu host  (run_vm_test.sh ya lo usa)
  # verificar dentro de la VM: grep -m1 -oE 'vmx|svm' /proc/cpuinfo
  ```

### `/dev/vhost-vsock ausente`
- **Causa**: módulo `vhost_vsock` no cargado / no disponible.
- **Solución**:
  ```bash
  sudo modprobe vhost_vsock
  ls -l /dev/vhost-vsock
  ```

### Firecracker: `The kernel file cannot be opened: No such file or directory (os error 2)`
- **Causa**: falta el kernel huésped en `/var/lib/hcma-firecracker/vmlinux`.
- **Solución**: generá o pasá el `vmlinux`:
  ```bash
  cd arquitectura/firecracker
  sudo sh build_vmlinux.sh                                   # ~10-15 min
  sudo sh deploy.sh "$PWD/vmlinux" "$PWD/rootfs.ext4"
  # o, al instalar:
  sudo HCMA_VMLINUX=/ruta/vmlinux HCMA_ROOTFS=/ruta/rootfs.ext4 sh arquitectura/instalar.sh
  ```

### Kernel panic: `VFS: Unable to mount root fs on unknown-block(0,0)`
- **Causa**: el kernel huésped no trae `virtio-blk` + `ext4` **built-in** (Firecracker expone
  los dispositivos por **virtio-MMIO**, no PCI).
- **Solución**: `build_vmlinux.sh` ya habilita `VIRTIO_MMIO`, `VIRTIO_BLK`, `EXT4_FS`,
  `VSOCKETS`, `SERIAL_8250`. Si compilás a mano, asegurate de tener esos `=y` (no módulos).

### Firecracker vsock: `Read-only file system (os error 30)`
- **Causa**: `ProtectSystem=strict` deja `/run/hcma` read-only y Firecracker no puede crear el
  socket del vsock.
- **Solución**: la unidad ya tiene `ReadWritePaths=/run/hcma`. Verificá:
  ```bash
  grep -n 'ReadWritePaths' arquitectura/firecracker/hcma-firecracker.service
  sudo systemctl daemon-reload && sudo systemctl restart hcma-firecracker
  ```

### Firecracker vsock: `Permission denied (os error 13)`
- **Causa**: `/run/hcma` no es transitable/escribible por el usuario no-root
  `hcma-firecracker` (grupo `hcma-vsock`).
- **Solución**: el monitor crea `/run/hcma` (`RuntimeDirectory=hcma`, `0711`) y chownea el
  socket `vsock.sock_5000` a `hcma-vsock` (`0660`). Asegurate de:
  ```bash
  ls -ld /run/hcma                        # drwx--x--x (0711)
  ls -l /run/hcma/vsock.sock_5000         # srw-rw---- hcma-monitor hcma-vsock
  id hcma-firecracker                     # ... grupos: kvm, hcma-vsock
  ```

---

## 4. Compilación del kernel huésped (`build_vmlinux.sh`)

### `error: cannot use keyword 'false' as enumeration constant` / `'bool' cannot be defined via 'typedef'`
- **Causa**: **gcc ≥ 15 usa C23 por defecto**; `bool`/`false`/`true` pasan a ser keywords y
  rompen `realmode` y el EFI stub (que sobreescriben `KBUILD_CFLAGS` sin `-std=gnu11`).
- **Solución**: `build_vmlinux.sh` ya fuerza `-std=gnu11` en `arch/x86/Makefile` (realmode) y
  `drivers/firmware/efi/libstub/Makefile`. Si compilás a mano:
  ```bash
  sed -i -E 's/^(REALMODE_CFLAGS[[:space:]]*:= )/\1-std=gnu11 /' arch/x86/Makefile
  sed -i 's/-D__DISABLE_EXPORTS$/-D__DISABLE_EXPORTS -std=gnu11/' drivers/firmware/efi/libstub/Makefile
  ```

### `fatal error: openssl/bio.h: No existe el fichero o el directorio` (en `certs/extract-cert.c`)
- **Causa**: el subsistema de `certs` (keyrings) exige headers OpenSSL.
- **Solución**: `build_vmlinux.sh` deshabilita `SYSTEM_TRUSTED_KEYRING`,
  `SECONDARY_TRUSTED_KEYRING` y `SYSTEM_REVOCATION_LIST` (no los necesitás para la microVM).
  Alternativa: `sudo apt-get install -y libssl-dev`.

### `make: *** [arch/x86] Error 2` sin mensaje claro
- **Causa**: falta `bc`/`bison`/`flex`/`libelf-dev` (el build de kernel los necesita).
- **Solución**: `sudo apt-get install -y gcc make bc bison flex libelf-dev xz-utils` (o el
  equivalente de tu distro; ver `distro.sh` → `BUILD_PKGS`).

---

## 5. px-motor (detector eBPF)

### `Unknown key 'StartLimitIntervalSec' in section [Service]`
- **Causa**: en systemd moderno, `StartLimitIntervalSec`/`StartLimitBurst` van en `[Unit]`, no
  en `[Service]`.
- **Solución**: ya está corregido en `arquitectura/px-motor/hcma-px-motor.service` (van en
  `[Unit]`). Reinstalá la unidad y recargá:
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl restart hcma-px-motor
  ```

### `bpftool btf dump … No such file or directory` (sin BTF)
- **Causa**: el kernel en ejecución no tiene `CONFIG_DEBUG_INFO_BTF=y`, así que no hay
  `/sys/kernel/btf/vmlinux` para generar `vmlinux.h`.
- **Solución**: usá un kernel con BTF (los kernels stock de Ubuntu/Debian lo traen) o habilitá
  BTF y reiniciá. Verificá:
  ```bash
  ls -l /sys/kernel/btf/vmlinux
  ```

### Fallo al compilar las sondas `.bpf.c`
- **Causa**: `clang` sin target `bpf`, o `vmlinux.h` ausente/corrupto.
- **Solución**: `sudo apt-get install -y clang bpftool linux-headers-amd64` (o equivalente) y
  asegurate de tener BTF; luego re-ejecutá `px-motor/deploy.sh`.

---

## 6. Rootfs (`build_rootfs_firecracker.sh`)

### `falta mmdebstrap`
- **Causa**: `mmdebstrap` solo existe en distros basadas en apt (Debian/Ubuntu y derivadas).
- **Solución**: en una distro apt: `sudo apt-get install -y mmdebstrap e2fsprogs`. En otras
  distros el build de rootfs no está soportado; usá un `rootfs.ext4` precompilado y pasalo con
  `HCMA_ROOTFS=/ruta/rootfs.ext4`.

---

## 7. Dashboard / red

### `curl: (7) Failed to connect … port 8787` / dashboard no responde
- **Causa**: el puerto es **dinámico** (si 8787 está ocupado, se usa el siguiente) y queda en
  `/etc/hcma/dash.env`.
- **Solución**:
  ```bash
  . /etc/hcma/dash.env; echo "puerto: $HCMA_DASH_PORT"
  sudo systemctl status hcma-dashboard --no-pager
  curl -skS https://localhost:$HCMA_DASH_PORT/api/components
  ```

### El health check marca `gVisor legacy no instalado`
- **Causa**: tras el cutover a Firecracker, `runsc` ya no es la frontera (es legacy).
- **Solución**: **esperable**; el check de gVisor es informativo. La frontera vigente es
  Firecracker (`firecracker instalado` + `vmlinux` + `rootfs`).

---

## 8. Monitor / socket vsock

### `MONITOR MUERE: no se pudo crear los sockets`
- **Causa**: `/run/hcma` no se puede crear/escribir, o falta el grupo `hcma-vsock`/`hcma-audit`.
- **Solución**: reinstalá el core (`sudo sh arquitectura/montaje/montar.sh`) para recrear los
  grupos, y verificá la unidad del monitor:
  ```bash
  grep -E 'RuntimeDirectory|SupplementaryGroups' arquitectura/scripts/hcma-monitor.service
  sudo systemctl restart hcma-monitor
  ```

---

## 10. El agente no hace lo que dice (desviación de gobernanza)

Si el LLM **reporta una cosa y hace otra** (inventa una regla, borra un feature sin medir, o
escribe código distinto al acordado), eso NO es una alucinación de salida — es una **desviación de
gobernanza**. La respuesta no está en el sandbox ni en la perplejidad, sino en: no confiar en el
auto-reporte y verificar el *work product* con un verificador determinista, y exigir autorización
humana para las decisiones con efecto. Ver `arquitectura/scripts/verificacion_trabajo.md`.

## 9. Ceremonia de CORTE (checkpoint + store) — manual, por diseño

El store real (`/var/lib/hcma-monitor/store/memoria.db`) se instala con una **ceremonia humana**
de una sola vez, no automatizable de forma segura. Si el health check marca
`store del monitor presente (¿falta ceremonia de cutover?)`, seguí `arquitectura/montaje/README.md`
→ “Paso manual: ceremonia de corte de estado”.
