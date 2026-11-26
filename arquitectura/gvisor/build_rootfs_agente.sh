#!/bin/sh
# build_rootfs_agente.sh — genera el rootfs del AGENTE productivo (python + herramientas + pipeline).
#
# Hueco de integración que Fable marcó (cierre 2): los rootfs estaban fragmentados
# (bundle/ = contención con ngspice sin python; bundle-python/ = cálculo sin ngspice).
# Este rootfs combina lo necesario para el CAMINO BENIGNO COMPLETO:
#   python3 + numpy + scipy + sympy   (de bundle-python)
#   ngspice recompilado sin GUI        (de bundle/artifacts)
#   módulos del pipeline del agente    (simulador, conectores, monitor_ipc, agente_cliente)
#
# No incluye kiCad ni openfoam (imágenes por herramienta separadas). El agente productivo
# final puede ser un rootfs por herramienta (dictamen Fable), pero para el arnés de camino
# benigno ngspice basta como derivador de seguridad alta.
#
# Uso:  sudo sh build_rootfs_agente.sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOTFS="$HERE/bundle-agente/rootfs"
PYTHON_RF="$HERE/bundle-python/rootfs"
NGSPICE_BIN="$HERE/artifacts/ngspice/bin/ngspice"
SCRIPTS="/home/charasky/Documentos/HMC/arquitectura/scripts"

echo "==> Generando rootfs del agente en $ROOTFS ..."
rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"

# 1. copiar el rootfs python (cálculo) como base
echo "   copiando base python ..."
cp -a "$PYTHON_RF/." "$ROOTFS/"

# 2. ngspice recompilado sin GUI
echo "   instalando ngspice mínimo ..."
mkdir -p "$ROOTFS/usr/local/bin"
cp "$NGSPICE_BIN" "$ROOTFS/usr/local/bin/ngspice"
chmod 755 "$ROOTFS/usr/local/bin/ngspice"

# 3. módulos del pipeline del agente (solo lo que necesita en runtime)
echo "   instalando módulos del pipeline ..."
mkdir -p "$ROOTFS/usr/local/lib/hcma_agente"
AGENT_MODS="monitor_ipc.py agente_cliente.py memoria_sqlite_cliente.py"
# copiamos los módulos que el agente usa (cliente IPC + entrypoint). NO memoria_sqlite completo
# (eso es del monitor): el agente solo habla por IPC. simulador/conectores quedan del lado del
# orquestador o se montan aparte en el rootfs final por herramienta.
cp "$SCRIPTS/monitor_ipc.py" "$ROOTFS/usr/local/lib/hcma_agente/" 2>/dev/null || true
cp "$SCRIPTS/agente_cliente.py" "$ROOTFS/usr/local/lib/hcma_agente/" 2>/dev/null || true
chmod 644 "$ROOTFS/usr/local/lib/hcma_agente/"*.py 2>/dev/null || true

echo "==> Verificando dentro del rootfs:"
chroot "$ROOTFS" /usr/bin/python3 -c "import sys, numpy, scipy, sympy; print('python', sys.version.split()[0], 'numpy', numpy.__version__)" 2>&1
chroot "$ROOTFS" /usr/local/bin/ngspice --version 2>&1 | sed -n '2p'

echo "==> rootfs del agente listo ($(du -sh "$ROOTFS" | cut -f1))"
