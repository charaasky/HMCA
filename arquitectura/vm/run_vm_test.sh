#!/usr/bin/env bash
# run_vm_test.sh — arranca una VM Ubuntu 24.04 virgen (QEMU/KVM) y ejecuta la
# instalación HCMA de punta a punta, de forma automática.
#
# Requisitos:
#   - KVM en el host (/dev/kvm) y nested virt habilitado (para Firecracker dentro de la VM).
#   - ~40 GB de disco libre y ~8 GB de RAM libres.
#
# Uso:
#   sh run_vm_test.sh
#
# Qué hace:
#   1) instala qemu + cloud-image-utils (si faltan)
#   2) descarga la imagen cloud de Ubuntu 24.04
#   3) genera un seed cloud-init (usuario `ubuntu` + tu clave SSH)
#   4) arranca la VM con -cpu host (nested virt) y forward de SSH al puerto 2222
#   5) espera a que la VM esté arriba, copia provision.sh y lo ejecuta
#   6) imprime el resultado
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VM_DIR="$HERE/.vm"
IMAGE="$VM_DIR/ubuntu-24.04.img"
SEED="$VM_DIR/seed.iso"
USER_DATA="$VM_DIR/user-data"
META_DATA="$VM_DIR/meta-data"
SSH_PUB="$HOME/.ssh/id_ed25519.pub"

IMAGE_URL="https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img"
RAM_MB=8192
CPUS=4
DISK_GB=40
SSH_PORT=2222

. "$HERE/../scripts/distro.sh" 2>/dev/null || true
echo "==> verificando dependencias (qemu + cloud-image-utils)"
if ! command -v qemu-system-x86_64 >/dev/null 2>&1 || ! command -v cloud-localds >/dev/null 2>&1; then
  if [ "${distro_ok:-0}" -ne 0 ]; then
    echo "    distro no soportada (${DISTRO_ID:-unknown}); instalá qemu + cloud-image-utils a mano" >&2
    exit 1
  fi
  echo "    instalando ${QEMU_PKGS} (${DISTRO_ID}) ..."
  sudo ${PKG_UPDATE} || true
  sudo ${PKG_INSTALL} ${QEMU_PKGS}
fi

[ -e /dev/kvm ] || { echo "ERROR: /dev/kvm no disponible"; exit 1; }

echo "==> clave SSH para cloud-init"
if [ ! -f "$SSH_PUB" ]; then
  echo "    generando ~/.ssh/id_ed25519 ..."
  ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519" -q
fi
KEY="$(cat "$SSH_PUB")"

mkdir -p "$VM_DIR"

echo "==> descargando imagen Ubuntu 24.04 (solo la primera vez)"
if [ ! -f "$IMAGE" ]; then
  wget -O "$IMAGE" "$IMAGE_URL"
fi

echo "==> apagando VM previa del test si existe (libera el lock de la imagen)"
pkill -f "qemu-system-x86_64.*${VM_DIR}" 2>/dev/null || true
sleep 1

echo "==> redimensionando imagen a ${DISK_GB}G"
qemu-img resize "$IMAGE" "${DISK_GB}G" >/dev/null

echo "==> generando seed cloud-init (usuario ubuntu + clave SSH)"
cat > "$USER_DATA" <<EOF
#cloud-config
hostname: hcma-vm-test
users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - $KEY
EOF
printf 'instance-id: hcma-vm-test\nlocal-hostname: hcma-vm-test\n' > "$META_DATA"
cloud-localds "$SEED" "$USER_DATA" "$META_DATA"

echo "==> arrancando VM (QEMU/KVM, -cpu host para nested virt)"
qemu-system-x86_64 \
  -enable-kvm -cpu host -smp "$CPUS" -m "$RAM_MB" \
  -drive file="$IMAGE",if=virtio \
  -drive file="$SEED",if=virtio,format=raw \
  -netdev user,id=net0,hostfwd=tcp::${SSH_PORT}-:22,hostfwd=tcp::8787-:8787 \
  -device virtio-net-pci,netdev=net0 \
  -display none -daemonize

echo "==> esperando a que la VM esté arriba (SSH :$SSH_PORT)"
SSH_OPTS="-p $SSH_PORT -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
# scp usa -P (mayúscula) para el puerto; ssh usa -p (minúscula).
SCP_OPTS="-P $SSH_PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
for i in $(seq 1 60); do
  if ssh $SSH_OPTS ubuntu@localhost true 2>/dev/null; then
    echo "    VM lista (intento $i)"
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "ERROR: la VM no respondió SSH en 5 min" >&2
    exit 1
  fi
  sleep 5
done

echo "==> copiando provision.sh y ejecutando la instalación (puede tardar varios minutos)"
scp $SCP_OPTS "$HERE/provision.sh" ubuntu@localhost:/tmp/provision.sh
ssh $SSH_OPTS ubuntu@localhost 'bash /tmp/provision.sh'

echo ""
echo ""
echo "==> TEST COMPLETO =="
echo "    Dashboard:   https://localhost:8787/   (aceptá el certificado autofirmado)"
echo "    SSH a la VM: ssh -p $SSH_PORT ubuntu@localhost"
echo "    log:         tail -f /var/log/hcma-install.log   (dentro de la VM)"
echo ""
echo "    Para apagar la VM:  sh teardown.sh"
