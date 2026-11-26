#!/bin/sh
# deploy_monitor_lib.sh — copia los MÓDULOS DEL MONITOR a /var/lib/hcma-monitor/lib/.
#
# Fase 2 (despliegue): el monitor (UID 999) no puede atravesar /home/charasky (modo 700), así
# que su CÓDIGO vive en su propio home (legible), separado del código del agente/humano. Esto
# materializa la separación que Fable pidió: el monitor solo importa sus módulos, desde una
# ubicación que él controla.
#
# Esto es un "despliegue" (como copiar un binario ya compilado), no duplicación de fuente: la
# fuente canónica sigue en arquitectura/scripts/, y este script produce la copia de runtime.
# El monitor NO importa prototipo.py ni nada del agente/orquestador.
#
# Uso:  sudo sh deploy_monitor_lib.sh
set -eu

MONITOR_HOME="/var/lib/hcma-monitor"
LIB_DIR="${MONITOR_HOME}/lib"
SRC="$(cd "$(dirname "$0")" && pwd)"

# Cierre de dependencias del monitor (sin prototipo.py ni módulos del agente).
MODULOS="
memoria_sqlite.py
slug_canon.py
nonces_monitor.py
gobernanza_accion.py
objetivo.py
firma_asimetrica.py
epoca.py
monitor_fachada.py
monitor_ipc.py
monitor_multiplex.py
hcma_monitor_daemon.py
firmante_uid.py
ablacion.py
procedencia.py
conectores.py
intent_canonico.py
proxy_cliente.py
ledger_ejecucion.py
firmas_multifirma.py
incidentes.py
herramientas.py
escaneo_seguridad.py
vigilancia_gvisor.py
observador_audit.py
sesiones.py
tareas.py
"

echo "==> Desplegando módulos del monitor a ${LIB_DIR} ..."
install -d -m 0700 -o hcma-monitor -g hcma-monitor "${LIB_DIR}"
for m in ${MODULOS}; do
  if [ -f "${SRC}/${m}" ]; then
    install -m 0600 -o hcma-monitor -g hcma-monitor "${SRC}/${m}" "${LIB_DIR}/${m}"
    echo "  copiado ${m}"
  else
    echo "  AVISO: no existe ${SRC}/${m}"
  fi
done
echo "==> módulos del monitor desplegados en ${LIB_DIR}"
ls -la "${LIB_DIR}"
