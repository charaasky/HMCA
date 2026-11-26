#!/bin/sh
# build_rootfs_lean.sh — genera el rootfs del VERIFICADOR FORMAL (Lean 4), copiando el toolchain
# ya instalado por elan + las libs del sistema que el binario `lean` necesita.
#
# NO usa mmdebstrap (Lean no está en apt con versión pineada): copia el toolchain de ~/.elan y las
# libs de sistema (libc/ld) para que gVisor arranque el binario. La contención REAL la da gVisor
# (root.readonly, network=none, capabilities vacías) + la unidad systemd (MemoryMax/CPUQuota).
#
# Uso (no requiere sudo: solo copia al bundle local):
#   sh build_rootfs_lean.sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOTFS="$HERE/bundle-lean/rootfs"

# Resolver el toolchain real (no el proxy de elan).
if ! command -v elan >/dev/null 2>&1 && [ ! -x "$HOME/.elan/bin/elan" ]; then
  echo "ERROR: elan no está instalado. Instalá Lean primero:" >&2
  echo "  curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh" >&2
  exit 1
fi
ELAN=$(command -v elan 2>/dev/null || echo "$HOME/.elan/bin/elan")
LEAN_BIN=$("$ELAN" which lean)
TOOLCHAIN_ROOT=$(CDPATH= cd -- "$(dirname -- "$LEAN_BIN")/.." && pwd)

echo "==> Toolchain: $TOOLCHAIN_ROOT"
echo "==> Generando rootfs en $ROOTFS ..."

rm -rf "$ROOTFS"
mkdir -p "$ROOTFS/opt/lean"
mkdir -p "$ROOTFS/lib/x86_64-linux-gnu"
mkdir -p "$ROOTFS/lib64"
# directorios estándar (mountpoints para gVisor/bwrap: /proc /dev /sys /tmp /work /etc /usr)
mkdir -p "$ROOTFS/proc" "$ROOTFS/dev" "$ROOTFS/sys" "$ROOTFS/tmp" "$ROOTFS/work" "$ROOTFS/etc" "$ROOTFS/usr"

# 1) Toolchain: bin + lib (runtime del kernel + oleans de Init) + share. Sin src/include (no runtime).
echo "==> Copiando toolchain (bin, lib, share) ..."
cp -a "$TOOLCHAIN_ROOT/bin" "$ROOTFS/opt/lean/bin"
cp -a "$TOOLCHAIN_ROOT/lib" "$ROOTFS/opt/lean/lib"
[ -d "$TOOLCHAIN_ROOT/share" ] && cp -a "$TOOLCHAIN_ROOT/share" "$ROOTFS/opt/lean/share"

# 2) Libs del sistema que `lean` linkea (ver `ldd "$LEAN_BIN"`). Se desreferencian (cp -L) para
#    copiar el archivo real, no el symlink.
echo "==> Copiando libs de sistema ..."
for lib in libc.so.6 libpthread.so.0 libdl.so.2 libm.so.6 librt.so.1; do
  src="/usr/lib/x86_64-linux-gnu/$lib"
  [ -e "$src" ] || src="/lib/x86_64-linux-gnu/$lib"
  cp -L "$src" "$ROOTFS/lib/x86_64-linux-gnu/$lib"
done
cp -L /lib64/ld-linux-x86-64.so.2 "$ROOTFS/lib64/ld-linux-x86-64.so.2"

echo "==> Verificando dentro del rootfs:"
echo "  lean:      $([ -x "$ROOTFS/opt/lean/bin/lean" ] && echo OK || echo FALTA)"
echo "  liblean:   $([ -d "$ROOTFS/opt/lean/lib/lean" ] && echo OK || echo FALTA)"
echo "  ld-linux:  $([ -e "$ROOTFS/lib64/ld-linux-x86-64.so.2" ] && echo OK || echo FALTA)"
echo "  libc:      $([ -e "$ROOTFS/lib/x86_64-linux-gnu/libc.so.6" ] && echo OK || echo FALTA)"

echo "==> rootfs de Lean listo ($(du -sh "$ROOTFS" | cut -f1))"
echo "   Para probar con gVisor (requiere root):"
echo "   sudo install -d -o 101 -g 981 /var/lib/hcma-lean/work"
echo "   sudo runsc --network=none run --bundle=$HERE/bundle-lean hcma-lean"
