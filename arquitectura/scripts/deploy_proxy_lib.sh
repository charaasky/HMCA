#!/bin/sh
# deploy_proxy_lib.sh — copia los MÓDULOS DEL PROXY a /var/lib/hcma-proxy/lib/.
#
# Diseño A (DISENO_RED_MCP.md): el proxy-red (UID hcma-proxy) es un proceso SEPARADO del monitor
# y del agente, dueño de la red de egreso. Su código vive en su propio home (legible por él),
# separado del monitor y del humano. El proxy NO importa memoria_sqlite ni nada del monitor.
#
# Uso:  sudo sh deploy_proxy_lib.sh
set -eu

PROXY_HOME="/var/lib/hcma-proxy"
LIB_DIR="${PROXY_HOME}/lib"
SRC="$(cd "$(dirname "$0")" && pwd)"

# Cierre de dependencias del proxy: SOLO lo necesario para validar intent + egreso + framing.
# NO memoria_sqlite, NO gobernanza, NO objetivo (eso es del monitor).
MODULOS="
hcma_proxy.py
hcma_proxy_daemon.py
intent_canonico.py
monitor_ipc.py
ledger_ejecucion.py
"

echo "==> Desplegando módulos del proxy a ${LIB_DIR} ..."
install -d -m 0700 -o hcma-proxy -g hcma-proxy "${LIB_DIR}"
for m in ${MODULOS}; do
  if [ -f "${SRC}/${m}" ]; then
    install -m 0600 -o hcma-proxy -g hcma-proxy "${SRC}/${m}" "${LIB_DIR}/${m}"
    echo "  copiado ${m}"
  else
    echo "  AVISO: no existe ${SRC}/${m}"
  fi
done
echo "==> módulos del proxy desplegados en ${LIB_DIR}"
ls -la "${LIB_DIR}"
