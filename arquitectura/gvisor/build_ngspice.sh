#!/bin/sh
# build_ngspice.sh — recompila ngspice SIN GUI/X/audio/readline (dictamen de Fable, opción B).
#
# Produce un binario mínimo para modo batch (-b), con superficie de dependencias reducida a
# libm/libstdc++/libgcc/libc (vs ~30 libs X11/audio/readline del binario con GUI).
#
# Dictamen de Fable (fable_gvisor_positiva_fable.md): "B es la correcta. ngspice compila en
# minutos. La toolchain vive en una etapa de build y nunca entra a la imagen de runtime.
# Fijá el tarball por hash."
#
# Uso:
#   sh build_ngspice.sh          # descarga, compila e instala en ./artifacts/ngspice
#
# Resultado: ./artifacts/ngspice/usr/bin/ngspice  (binario mínimo, no versionado en git)
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT="$HERE/artifacts/ngspice"
VERSION="47"
TARBALL="ngspice-${VERSION}.tar.gz"
# Hash fijado (sha256) del tarball oficial ngspice-47 de SourceForge. Verificado el 2026-11-15.
HASH="894e649651f1838a14095e5a5439e7d3aa63e87ede14d283173fda4fcdef675f"
URL="https://sourceforge.net/projects/ngspice/files/ng-spice-rework/${VERSION}/${TARBALL}/download"
WORK="/tmp/ngspice-build"

echo "==> Preparando directorio de trabajo $WORK ..."
rm -rf "$WORK"
mkdir -p "$WORK"

echo "==> Descargando $TARBALL ..."
wget -q "$URL" -O "$WORK/$TARBALL"

echo "==> Verificando hash sha256 ..."
echo "$HASH  $WORK/$TARBALL" | sha256sum -c -

echo "==> Extrayendo ..."
tar xzf "$WORK/$TARBALL" -C "$WORK"
SRC="$WORK/ngspice-${VERSION}"

echo "==> Configurando (sin X, sin readline, sin openmp, KLU activo) ..."
cd "$SRC"
./configure \
  --without-x \
  --with-readline=no \
  --disable-openmp \
  --prefix="$OUT" 2>&1 | tail -3

echo "==> Compilando con $([ -n "${NPROC:-}" ] && echo "$NPROC" || nproc) núcleos ..."
make -j"$([ -n "${NPROC:-}" ] && echo "$NPROC" || nproc)" 2>&1 | tail -3

echo "==> Instalando en $OUT ..."
make install-strip 2>&1 | tail -3

echo "==> Verificando dependencias del binario final (deben ser solo libm/libstdc++/libgcc/libc):"
ldd "$OUT/bin/ngspice" 2>&1 | grep -iE 'X11|Xaw|sndfile|readline|FLAC|vorbis|mpg123|fontconfig|freetype' \
  && echo "  ⚠️  HAY dependencias de GUI/audio inesperadas" \
  || echo "  ✅ sin dependencias de GUI/audio (mínimo correcto)"

echo "==> Smoke test (análisis .op):"
printf 'RC test\nV1 n1 0 DC 1\nR1 n1 n2 1000\nC1 n2 0 1u\n.op\n.end\n' > /tmp/ngspice_smoke.cir
"$OUT/bin/ngspice" -b /tmp/ngspice_smoke.cir 2>&1 | grep -iE 'done|error' | tail -2

echo "==> ngspice mínimo listo en $OUT"
