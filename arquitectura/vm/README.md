# VM test — instalación HCMA de punta a punta

Harness para probar que `sudo sh arquitectura/instalar.sh` funciona en una máquina
**virgen**, sin tocar tu host real: levanta una VM Ubuntu 24.04 con QEMU/KVM,
clona el repo, corre el instalador (preflight de dependencias + pasos 1..6) y
verifica la salud de los servicios.

## Requisitos

- **KVM** en el host (`/dev/kvm`) y **nested virt habilitado** (para que Firecracker
  corra *dentro* de la VM).
- ~40 GB de disco y ~8 GB de RAM libres.
- `wget`, `ssh`, `scp` (estándar).

El script instala solo lo que falta de QEMU (`qemu-system-x86 qemu-utils
cloud-image-utils`).

## Uso

```bash
cd arquitectura/vm
sh run_vm_test.sh
```

Qué hace:

1. Instala qemu + cloud-image-utils (si faltan).
2. Descarga la imagen cloud de Ubuntu 24.04 (solo la primera vez).
3. Genera un seed cloud-init con el usuario `ubuntu` + tu clave SSH.
4. Arranca la VM con `-cpu host` (nested virt) y forward de SSH al puerto `2222`.
5. Espera a que la VM esté arriba, copia `provision.sh` y lo ejecuta.
6. Imprime el resultado.

## Ver manualmente

Si preferís controlar el proceso:

```bash
# entrar a la VM
ssh -p 2222 ubuntu@localhost

# correr la instalación
bash /tmp/provision.sh        # (o copiar provision.sh de nuevo)

# seguir el log
tail -f /var/log/hcma-install.log
```

## Apagar la VM

```bash
sh teardown.sh
```

Apaga el qemu del test y borra `.vm/` (imagen + seed), liberando el disco.
La desinstalación de HCMA (dentro de la VM o en cualquier host) es:
`sudo sh arquitectura/desinstalar.sh`.

## Notas

- El instalador crea usuarios/grupos systemd, instala paquetes, compila las sondas
  eBPF y arranca Firecracker/KVM. Por eso la VM necesita **nested virt**; sin eso,
  el paso de Firecracker falla (es esperable).
- La instalación puede tardar varios minutos (apt + build de Go/eBPF).
