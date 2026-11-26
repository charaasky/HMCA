#!/bin/sh
# build_rootfs_python.sh — genera el rootfs de CÁLCULO (python3 + numpy + scipy + sympy).
#
# Dictamen de Fable: "Python va en la imagen, no como bind-mount de .deps. Lo mínimo honesto es
# intérprete, stdlib, numpy, scipy, sympy y BLAS. Sin pip, sin compilador y sin headers."
#
# Imagen por herramienta: este rootfs es SOLO para cálculo numérico. Sin shell interactivo de
# más, sin busybox innecesario más allá de lo que minbase ya trae, sin pip/compilador.
#
# Uso (requiere sudo + mmdebstrap):
#   sudo sh build_rootfs_python.sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOTFS="$HERE/bundle-python/rootfs"
SUITE="${HCMA_DEBIAN_SUITE:-forky}"

echo "==> Generando rootfs de cálculo en $ROOTFS (suite $SUITE) ..."
rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"

mmdebstrap \
  --variant=minbase \
  --arch="$(dpkg --print-architecture)" \
  --include="python3,python3-numpy,python3-scipy,python3-sympy" \
  "$SUITE" "$ROOTFS"

echo "==> Verificando versiones dentro del rootfs:"
chroot "$ROOTFS" python3 -c "import sys, numpy, scipy, sympy; print('python', sys.version.split()[0]); print('numpy', numpy.__version__); print('scipy', scipy.__version__); print('sympy', sympy.__version__)" 2>&1

echo "==> rootfs de cálculo listo ($(du -sh "$ROOTFS" | cut -f1))"
