#!/bin/sh
# build_vmlinux.sh — compila un vmlinux (ELF) mínimo para Firecracker.
# SPDX-License-Identifier: Apache-2.0
#
# Produce el kernel huésped de la microVM con todo lo necesario BUILT-IN (sin módulos):
#   virtio-blk + ext4   -> montar el rootfs
#   virtio-vsock        -> transporte agente <-> monitor (CID 2)
#   serial 8250         -> consola ttyS0
#
# Requisitos (instalá con el gestor de tu distro):
#   gcc make bc bison flex libelf-dev curl xz-utils
#
# Uso:
#   sudo sh build_vmlinux.sh                 # vmlinux en ./vmlinux
#   HCMA_KERNEL_VERSION=6.6.62 sudo sh build_vmlinux.sh
#
# El vmlinux se versiona NO (ver .gitignore); se regenera con este script.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Re-ejecutando con sudo (necesito root para instalar/compilar)..."
  exec sudo sh "$0" "$@"
fi

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KVER="${HCMA_KERNEL_VERSION:-6.6.62}"
SRC="$HERE/linux-$KVER"
OUT="${HCMA_VMLINUX_OUT:-$HERE/vmlinux}"
URL="https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-${KVER}.tar.xz"

for t in gcc make bc bison flex curl tar xz; do
  command -v "$t" >/dev/null 2>&1 || { echo "  falta '$t' (requerido)" >&2; exit 1; }
done
[ -f /usr/include/libelf.h ] || { echo "  falta libelf-dev (requerido)" >&2; exit 1; }

echo "==> descargando kernel $KVER (solo la primera vez) ..."
if [ ! -d "$SRC" ]; then
  curl -fsSL -o /tmp/linux-${KVER}.tar.xz "$URL"
  tar -xJf /tmp/linux-${KVER}.tar.xz -C "$HERE"
fi

cd "$SRC"
echo "==> configurando (defconfig, sin módulos, virtio/vsock/ext4 built-in) ..."
make defconfig
scripts/config \
  --disable MODULES \
  --disable DEBUG_INFO \
  --disable DEBUG_INFO_BTF \
  --enable VIRTIO \
  --enable VIRTIO_PCI \
  --enable VIRTIO_BLK \
  --enable VIRTIO_VSOCKETS \
  --enable VIRTIO_VSOCKETS_COMMON \
  --enable EXT4_FS \
  --enable SERIAL_8250 \
  --enable SERIAL_8250_CONSOLE \
  --enable DEVTMPFS \
  --enable DEVTMPFS_MOUNT \
  --enable BINFMT_ELF \
  --enable PROC_FS \
  --enable SYSFS \
  --enable TTY \
  --enable PRINTK
make olddefconfig

echo "==> compilando vmlinux (puede tardar varios minutos) ..."
make -j"$(nproc)" vmlinux

install -m 0644 "$SRC/vmlinux" "$OUT"
echo "==> vmlinux listo: $OUT"
