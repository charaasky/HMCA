#!/bin/sh
# montar.sh — orquestador de montaje de la arquitectura HCMA (idempotente).
# SPDX-License-Identifier: Apache-2.0
#
# Monta la arquitectura de punta a punta, de forma reproducible:
#   1) frontera de UIDs (hcma-agent 101 / hcma-signer 102 / hcma-monitor 999 / hcma-proxy 997
#      + grupo hcma-red 1001)
#   2) claves Ed25519 + ancla de confianza (pin root-owned)
#   3) despliegue de los módulos del monitor y del proxy a sus homes
#   4) unidades systemd (monitor, proxy, dashboard)
#   5) egreso de red (nftables default-deny SOLO para el UID del proxy)
#   6) manifiesto congelado + verificación
#
# La ceremonia de CORTE de estado (checkpoint firmado + cutover del store al monitor) es un paso
# humano separado — ver README.md. Este script la VERIFICA pero no la ejecuta destructivamente.
#
# Uso:  sudo sh montar.sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Re-ejecutando con sudo (necesito root)..."
  exec sudo sh "$0" "$@"
fi

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DASH_USER="${HCMA_DASH_USER:-${SUDO_USER:-$(logname 2>/dev/null || true)}}"
[ -n "${DASH_USER}" ] || DASH_USER="root"
SCRIPTS="${REPO}/arquitectura/scripts"
DEPS="${REPO}/.deps"
SIGNER_HOME="/var/lib/hcma-signer"

paso() { echo ""; echo "=== $1 ==="; }

# Resuelve el placeholder __HCMA_REPO__ con la ruta real del repo al instalar una unidad
# systemd (portabilidad: los .service no hardcodean /home/charasky).
instalar_servicio() {
  sed -e "s|__HCMA_REPO__|${REPO}|g" -e "s|__HCMA_DASH_USER__|${DASH_USER:-}|g" "$1" > "$2"
  chmod 0644 "$2"
  echo "  ok: $2"
}


crear_usuario() {
  u="$1"; uid="$2"; home="$3"
  if id "$u" >/dev/null 2>&1; then
    echo "  ok: $u ya existe (uid $(id -u "$u"))"
  elif getent passwd "$uid" >/dev/null 2>&1; then
    # UID ya ocupado por otro usuario del sistema (p.ej. systemd en Ubuntu): auto-asignar.
    useradd --system --user-group --create-home --home "$home" \
      --shell /usr/sbin/nologin "$u"
    echo "  creado: $u (uid $(id -u "$u"); el $uid estaba ocupado)"
  else
    useradd --system --user-group --uid "$uid" --create-home --home "$home" \
      --shell /usr/sbin/nologin "$u"
    echo "  creado: $u (uid $uid)"
  fi
}

paso "[1/6] Frontera de UIDs"
crear_usuario hcma-agent   101 /var/lib/hcma-agent
crear_usuario hcma-signer  102 /var/lib/hcma-signer
crear_usuario hcma-monitor 999 /var/lib/hcma-monitor
crear_usuario hcma-proxy   997 /var/lib/hcma-proxy
if getent group hcma-red >/dev/null 2>&1; then
  echo "  ok: grupo hcma-red ya existe (gid $(getent group hcma-red | cut -d: -f3))"
else
  groupadd --system --gid 1001 hcma-red
  echo "  creado: grupo hcma-red (gid 1001)"
fi

# hcma-vsock: lo usa el monitor (SupplementaryGroups) en modo vsock. Crearlo acá (y no
# solo en firecracker/deploy.sh) para que el monitor arranque aunque Firecracker no esté
# instalado todavía.
if getent group hcma-vsock >/dev/null 2>&1; then
  echo "  ok: grupo hcma-vsock ya existe"
else
  groupadd --system hcma-vsock
  echo "  creado: grupo hcma-vsock"
fi
for u in hcma-proxy hcma-monitor; do
  if ! id -nG "$u" 2>/dev/null | tr ' ' '\n' | grep -qx hcma-red; then
    usermod -a -G hcma-red "$u"
    echo "  $u añadido al grupo hcma-red"
  fi
done
chmod 0700 /var/lib/hcma-monitor

# Registrar los UIDs reales (por si colisionaron y se auto-asignaron) para que el
# firewall y el proxy usen el UID correcto, no un número hardcodeado.
AGENT_UID="$(id -u hcma-agent)"
SIGNER_UID="$(id -u hcma-signer)"
MONITOR_UID="$(id -u hcma-monitor)"
PROXY_UID="$(id -u hcma-proxy)"
install -d -m 0755 /etc/hcma
cat > /etc/hcma/uids.env <<EOF
HCMA_AGENT_UID=${AGENT_UID}
HCMA_SIGNER_UID=${SIGNER_UID}
HCMA_MONITOR_UID=${MONITOR_UID}
HCMA_PROXY_UID=${PROXY_UID}
EOF
echo "  uids: agent=${AGENT_UID} signer=${SIGNER_UID} monitor=${MONITOR_UID} proxy=${PROXY_UID}"

paso "[2/6] Claves Ed25519 + ancla de confianza (pin)"
if [ -f "${SIGNER_HOME}/humano_ed25519.key" ]; then
  echo "  ok: claves ya existen en ${SIGNER_HOME}"
else
  HCMA_SIGNER_USER=hcma-signer PYTHONPATH="${DEPS}" \
    python3 "${SCRIPTS}/cli_firma_humana.py" generar-claves
  echo "  claves generadas"
fi
chown -R hcma-signer:hcma-signer "${SIGNER_HOME}"
chmod 600 "${SIGNER_HOME}/humano_ed25519.key"
chmod 644 "${SIGNER_HOME}/humano_ed25519.pub"
PIN="$(tr -d '\n' < "${SIGNER_HOME}/humano_ed25519.pub" | sha256sum | cut -d' ' -f1)"
install -d -m 0755 /etc/hcma
printf '%s\n' "$PIN" | install -m 0644 -o root -g root /dev/stdin /etc/hcma/publica.pin
echo "  pin fijado: ${PIN}"

paso "[3/6] Despliegue + ledgers de ejecución + grupo de auditoría (solo lectura)"
sh "${SCRIPTS}/deploy_monitor_lib.sh"
sh "${SCRIPTS}/deploy_proxy_lib.sh"
# Grupo hcma-audit (gid 1002): read-only para el dashboard (charasky). El agente (101) NO entra.
# Solo puede LEER los ledgers (append-only); la escritura sigue restringida al UID dueño.
if getent group hcma-audit >/dev/null 2>&1; then
  echo "  ok: grupo hcma-audit ya existe"
else
  groupadd --system --gid 1002 hcma-audit
  echo "  creado: grupo hcma-audit (gid 1002)"
fi
usermod -a -G hcma-audit "$DASH_USER" 2>/dev/null || echo "  AVISO: no se pudo agregar $DASH_USER a hcma-audit"
# Ledger de ejecución del proxy: escritura SOLO hcma-proxy (997); lectura del grupo auditoría.
# El archivo se crea 0644 (umask) por el proxy => ya es legible por el grupo.
install -d -m 0750 -o hcma-proxy -g hcma-audit /var/lib/hcma-proxy/ledger
# Estado de runtime del monitor en instalación limpia: sin store/, contador de época
# y nonces.log el monitor no arranca. La ceremonia de CORTE (checkpoint firmado +
# cutover del store real) sigue siendo manual; esto solo deja un estado mínimo
# arrancable y es idempotente (no pisa estado ya existente).
install -d -m 0750 -o hcma-monitor -g hcma-audit /var/lib/hcma-monitor/store
[ -f /var/lib/hcma-monitor/epoca ] || printf '0\n' | install -m 0640 -o hcma-monitor -g hcma-monitor /dev/stdin /var/lib/hcma-monitor/epoca
[ -f /var/lib/hcma-monitor/nonces.log ] || install -m 0640 -o hcma-monitor -g hcma-audit /dev/null /var/lib/hcma-monitor/nonces.log
# nonces.log (autorizaciones) vive en el home del monitor: damos traverse+lectura SOLO al grupo
# auditoría. El store (memoria.db 0600) y lib/ (0600) siguen inaccesibles para el grupo.
chmod 0710 /var/lib/hcma-monitor
chgrp hcma-audit /var/lib/hcma-monitor
if [ -f /var/lib/hcma-monitor/nonces.log ]; then
  chmod 0640 /var/lib/hcma-monitor/nonces.log
  chgrp hcma-audit /var/lib/hcma-monitor/nonces.log
fi
echo "  grupo hcma-audit configurado (lectura de ledgers, escritura restringida)"

paso "[4/6] Unidades systemd"
# Elegir un puerto libre para el dashboard (default 8787; si está ocupado, el siguiente).
DASH_PORT="${HCMA_DASH_PORT:-8787}"
while ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE ":${DASH_PORT}$"; do
  echo "  puerto ${DASH_PORT} ocupado; probando el siguiente..."
  DASH_PORT=$((DASH_PORT + 1))
done
install -d -m 0755 /etc/hcma
cat > /etc/hcma/dash.env <<EOF
HCMA_DASH_PORT=${DASH_PORT}
EOF
echo "  dashboard usará el puerto ${DASH_PORT}"
install -m 0644 "${SCRIPTS}/hcma-monitor.service"  /etc/systemd/system/hcma-monitor.service
install -m 0644 "${SCRIPTS}/hcma-proxy.service"    /etc/systemd/system/hcma-proxy.service
instalar_servicio "${REPO}/arquitectura/dashboard/hcma-dashboard.service" \
  /etc/systemd/system/hcma-dashboard.service
# El dashboard escribe su estado (telemetry.db, auth.db, certs, llm_config.json) en su
# directorio; se lo damos al usuario del dashboard para que pueda escribir (el repo puede
# estar clonado como root, p.ej. en el test de VM).
chown -R "$DASH_USER":"$DASH_USER" "${REPO}/arquitectura/dashboard"
systemctl daemon-reload
systemctl enable hcma-monitor.service hcma-proxy.service hcma-dashboard.service
echo "  unidades habilitadas (monitor, proxy, dashboard)"

paso "[5/6] Egreso de red (nftables default-deny SOLO UID 997)"
sh "${SCRIPTS}/firewall_hcma_proxy.sh" apply

paso "[6/6] Manifiesto congelado + verificación"
python3 "${SCRIPTS}/manifiesto_sistema.py"
# La verificación de salud COMPLETA (store, "dashboard responde") corre después de
# arrancar los servicios (instalar.sh paso 5 / provision.sh). Acá los servicios todavía
# no arrancan y runsc/gVisor está reemplazado por Firecracker, así que es normal ver
# checks en rojo: NO debe abortar el montaje.
sh "${REPO}/arquitectura/montaje/verificar.sh" || echo "  (verificación informativa: rojos esperados antes del arranque)"

echo ""
echo "================================================================================"
echo " Montaje completo. Si la verificación marcó un fallo, revisá el paso correspondiente."
echo " La ceremonia de CORTE de estado (checkpoint + store al monitor) es manual: ver README.md."
echo "================================================================================"
