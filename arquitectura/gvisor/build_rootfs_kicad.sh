#!/bin/sh
# build_rootfs_kicad.sh — genera el rootfs de EDA (kicad + kicad-cli) SIN recommends.
#
# Dictamen de Fable: "kicad-cli va instalado en su propia imagen, sin recommends. Las bibliotecas
# y los modelos 3D pesan gigas. Bind-montar binario y libs del host es la opción C disfrazada.
# Fijá la misma versión que genera los archivos. Va a querer escribir fuera de /tmp: apuntá HOME
# y XDG a tmpfs."
#
# Imagen por herramienta: SOLO kicad para exportar esquemáticos/PDF. Sin python extra (kicad ya
# arrastra su python-wxgtk), sin recommends (NO descargar librerías de símbolos/modelos 3D).
#
# Uso (requiere sudo + mmdebstrap):
#   sudo sh build_rootfs_kicad.sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOTFS="$HERE/bundle-kicad/rootfs"
SUITE="${HCMA_DEBIAN_SUITE:-forky}"

echo "==> Generando rootfs de EDA en $ROOTFS (suite $SUITE) ..."
rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"

# --variant=minbase + kicad. Sin --include de recommends: mmdebstrap por defecto NO instala
# recommends salvo que se pida. kicad trae kicad-cli (versión 10).
mmdebstrap \
  --variant=minbase \
  --arch="$(dpkg --print-architecture)" \
  --include="kicad" \
  "$SUITE" "$ROOTFS"

echo "==> Verificando kicad-cli dentro del rootfs:"
chroot "$ROOTFS" kicad-cli version 2>&1 | head -5

echo "==> rootfs de EDA listo ($(du -sh "$ROOTFS" | cut -f1))"
