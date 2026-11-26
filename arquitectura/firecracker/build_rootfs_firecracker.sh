#!/bin/sh
# build_rootfs_firecracker.sh — rootfs.ext4 mínimo para la microVM Firecracker.
# SPDX-License-Identifier: Apache-2.0
#
# Genera un rootfs Debian (mmdebstrap minbase) con:
#   python3 + busybox-static   -> runtime del agente + herramientas mínimas
#   agente_vsock.py + monitor_ipc.py -> el agente que habla por vsock
#   /init                      -> PID 1 (monta pseudo-FS y arranca el agente)
#
# Requisitos: mmdebstrap, e2fsprogs (mkfs.ext4), mount (loop).
#
# Uso:
#   sudo sh build_rootfs_firecracker.sh          # rootfs.ext4 en ./
#   HCMA_DEBIAN_SUITE=trixie HCMA_ROOTFS_SIZE_MB=512 sudo sh build_rootfs_firecracker.sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Re-ejecutando con sudo (necesito root para mmdebstrap + mount)..."
  exec sudo sh "$0" "$@"
fi

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOTFS="$HERE/rootfs-firecracker"
IMG="$HERE/rootfs.ext4"
SUITE="${HCMA_DEBIAN_SUITE:-trixie}"
SIZE_MB="${HCMA_ROOTFS_SIZE_MB:-512}"
SCRIPTS="$HERE/../scripts"

command -v mmdebstrap >/dev/null 2>&1 || { echo "  falta mmdebstrap" >&2; exit 1; }
command -v mkfs.ext4 >/dev/null 2>&1 || { echo "  falta mkfs.ext4 (e2fsprogs)" >&2; exit 1; }

echo "==> generando rootfs Debian ($SUITE) con python3 + busybox-static ..."
rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"
mmdebstrap --variant=minbase --arch="$(dpkg --print-architecture)" \
  --include="python3,busybox-static" "$SUITE" "$ROOTFS"

echo "==> instalando agente (vsock) + init ..."
mkdir -p "$ROOTFS/usr/local/lib/hcma_agente"
cp "$SCRIPTS/monitor_ipc.py"   "$ROOTFS/usr/local/lib/hcma_agente/"
cp "$SCRIPTS/agente_vsock.py"  "$ROOTFS/usr/local/lib/hcma_agente/"
cp "$HERE/guest_init"          "$ROOTFS/init"
chmod 755 "$ROOTFS/init" "$ROOTFS/usr/local/lib/hcma_agente/agente_vsock.py"

echo "==> creando rootfs.ext4 (${SIZE_MB}M) ..."
rm -f "$IMG"
truncate -s "${SIZE_MB}M" "$IMG"
mkfs.ext4 -F "$IMG" >/dev/null 2>&1

TMP_MNT="$(mktemp -d)"
mount -o loop "$IMG" "$TMP_MNT"
cp -a "$ROOTFS/." "$TMP_MNT/"
umount "$TMP_MNT"
rmdir "$TMP_MNT"

echo "==> rootfs.ext4 listo: $IMG ($(du -sh "$IMG" | cut -f1))"
