#!/bin/sh
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
#
# setup_usuarios.sh — crea los 3 UIDs de la frontera de privilegios (P0 Fable).
#
# Modelo de 3 UIDs (dictamen de Fable 5ta revision):
#   humano   : el usuario interactivo actual (charasky) — revisa y confirma en su TTY.
#   agente   : hcma-agent  — corre el runtime del agente. SIN acceso a la privada ni sudo al firmante.
#   firmante : hcma-signer — posee la clave privada Ed25519. Solo firma; sin shell de login.
#
# Invariante: el AGENTE no tiene ninguna ruta sudo hacia el FIRMANTE. La clave privada vive en
# el home del firmante (modo 600, dueño hcma-signer). El humano confirma en su TTY (que el agente
# no controla) y es el ÚNICO que puede invocar al firmante (via sudo con password, que el agente
# no sabe, o via un comando dedicado).
#
# ESTE SCRIPT NO SE AUTO-EJECUTA como root: imprime los comandos que el HUMANO corre manualmente
# con sudo (el agente no debe poder crear usuarios ni auto-elevarse).
# Para ejecutar de verdad, el humano corre:  sh setup_usuarios.sh --aplicar
set -u

SIGNER_USER="hcma-signer"
AGENT_USER="hcma-agent"
MONITOR_USER="hcma-monitor"
SIGNER_HOME="/var/lib/${SIGNER_USER}"
AGENT_HOME="/var/lib/${AGENT_USER}"
MONITOR_HOME="/var/lib/${MONITOR_USER}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

emitir() {
  echo "================================================================================"
  echo " Frontera de 3 UIDs (agente / humano / firmante)"
  echo "================================================================================"
  echo ""
  echo "# 1) Crear el usuario FIRMANTE (solo firma, sin shell de login):"
  echo "sudo /usr/sbin/useradd --system --create-home --home ${SIGNER_HOME} --shell /usr/sbin/nologin ${SIGNER_USER}"
  echo ""
  echo "# 2) Crear el usuario AGENTE (corre el runtime, sin shell de login):"
  echo "sudo /usr/sbin/useradd --system --create-home --home ${AGENT_HOME} --shell /usr/sbin/nologin ${AGENT_USER}"
  echo ""
  echo "# 2bis) Crear el usuario MONITOR (proceso separado dueño del store/nonces/época):"
  echo "sudo /usr/sbin/useradd --system --create-home --home ${MONITOR_HOME} --shell /usr/sbin/nologin ${MONITOR_USER}"
  echo "sudo chmod 0700 ${MONITOR_HOME}"
  echo ""
  echo "# 3) Generar el par de claves (como root, luego chown al firmante):"
  echo "sudo env HCMA_SIGNER_USER=${SIGNER_USER} PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 ${SCRIPT_DIR}/cli_firma_humana.py generar-claves"
  echo "sudo chown -R ${SIGNER_USER}:${SIGNER_USER} ${SIGNER_HOME}"
  echo ""
  echo "# 4) Ajustar permisos (privada 600 solo del firmante; publica 644 legible por el agente):"
  echo "sudo chmod 600 ${SIGNER_HOME}/humano_ed25519.key"
  echo "sudo chmod 644 ${SIGNER_HOME}/humano_ed25519.pub"
  echo ""
  echo "# 5) Publica (opcional, para que el runtime la lea sin asumir ruta):"
  echo "cat ${SIGNER_HOME}/humano_ed25519.pub"
  echo ""
  echo "# 6) VERIFICAR que el agente NO puede leer la privada:"
  echo "sudo -u ${AGENT_USER} cat ${SIGNER_HOME}/humano_ed25519.key 2>&1 | head -1"
  echo "   # debe dar 'Permission denied'"
  echo ""
  echo "# 7) Verificar que no hay ruta sudo del agente al firmante (debe estar vacío):"
  echo "sudo grep -R '${AGENT_USER}' /etc/sudoers /etc/sudoers.d 2>/dev/null || echo 'sin regla sudo para el agente (OK)'"
  echo ""
  echo "================================================================================"
  echo " NOTA: el AGENTE no debe tener sudo hacia el FIRMANTE. Si el paso 6 no da"
  echo " 'Permission denied', o el paso 7 muestra una regla, la frontera está ROTA."
  echo "================================================================================"
}

aplicar() {
  echo "Aplicando configuración de 3 UIDs (requiere sudo con password)..."
  set -e
  if ! id "${SIGNER_USER}" >/dev/null 2>&1; then
    sudo /usr/sbin/useradd --system --create-home --home "${SIGNER_HOME}" --shell /usr/sbin/nologin "${SIGNER_USER}"
    echo "creado ${SIGNER_USER}"
  else
    echo "${SIGNER_USER} ya existe"
  fi
  if ! id "${AGENT_USER}" >/dev/null 2>&1; then
    sudo /usr/sbin/useradd --system --create-home --home "${AGENT_HOME}" --shell /usr/sbin/nologin "${AGENT_USER}"
    echo "creado ${AGENT_USER}"
  else
    echo "${AGENT_USER} ya existe"
  fi
  if ! id "${MONITOR_USER}" >/dev/null 2>&1; then
    sudo /usr/sbin/useradd --system --create-home --home "${MONITOR_HOME}" --shell /usr/sbin/nologin "${MONITOR_USER}"
    echo "creado ${MONITOR_USER}"
  else
    echo "${MONITOR_USER} ya existe"
  fi
  sudo chmod 0700 "${MONITOR_HOME}"
  echo "home del monitor: ${MONITOR_HOME} (0700)"
  # generar claves como ROOT (para no depender del PATH/PYTHONPATH de charasky), y luego
  # chown al firmante. La privada queda en el home del firmante, dueño correcto.
  if [ ! -f "${SIGNER_HOME}/humano_ed25519.key" ]; then
    sudo env HCMA_SIGNER_USER="${SIGNER_USER}" PYTHONPATH="/home/charasky/Documentos/HMC/.deps" \
      python3 "${SCRIPT_DIR}/cli_firma_humana.py" generar-claves \
      && sudo chown -R "${SIGNER_USER}:${SIGNER_USER}" "${SIGNER_HOME}"
  else
    echo "claves ya generadas en ${SIGNER_HOME}"
  fi
  sudo chmod 600 "${SIGNER_HOME}/humano_ed25519.key"
  sudo chmod 644 "${SIGNER_HOME}/humano_ed25519.pub"
  # ANCLA DE CONFIANZA: archivo root-owned con el pin (hash) de la pública, fuera del
  # alcance de escritura del agente. El agente podria reemplazar la publica; este pin la fija.
  if [ -z "${HCMA_PUBLICA_PIN:-}" ]; then
    PIN="$(sudo cat "${SIGNER_HOME}/humano_ed25519.pub" | tr -d '\n' | sha256sum | cut -d' ' -f1)"
  else
    PIN="${HCMA_PUBLICA_PIN}"
  fi
  sudo install -d -m 0755 -o root -g root /etc/hcma
  printf '%s\n' "${PIN}" | sudo install -m 0644 -o root -g root /dev/stdin /etc/hcma/publica.pin
  echo "ancla publica.pin = ${PIN}"
  echo "done."
}

case "${1:-}" in
  --aplicar)
    aplicar
    ;;
  *)
    emitir
    ;;
esac
