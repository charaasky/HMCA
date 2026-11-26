#!/bin/sh
# build_rootfs.sh — genera el rootfs mínimo Debian con mmdebstrap (reproducible, root-owned).
#
# Uso (requiere sudo, mmdebstrap instalado):
#   sudo sh build_rootfs.sh
#
# El rootfs NO se versiona (ver .gitignore). Se regenera desde este script.
# Para el rootfs REAL de producción (con ngspice/kicad/openfoam + Python + .deps), se agregan
# los paquetes aquí debajo de la línea "--include". Por ahora es el rootfs de CONTENCIÓN:
# busybox-static (shell robusto) + coreutils (dmesg/getent/head/id) para el payload hostil.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOTFS="$HERE/bundle/rootfs"
SUITE="${HCMA_DEBIAN_SUITE:-forky}"

echo "==> Generando rootfs en $ROOTFS (suite $SUITE) ..."
rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"

mmdebstrap \
  --variant=minbase \
  --arch="$(dpkg --print-architecture)" \
  --include="busybox-static,coreutils,bash,dash" \
  "$SUITE" "$ROOTFS"

echo "==> rootfs generado: $(du -sh "$ROOTFS" | cut -f1)"
echo "==> lista de binarios esenciales:"
ls "$ROOTFS/bin/busybox" "$ROOTFS/bin/dash" "$ROOTFS/bin/bash" 2>&1
