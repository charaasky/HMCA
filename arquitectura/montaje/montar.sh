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

REPO="/home/charasky/Documentos/HMC"
SCRIPTS="${REPO}/arquitectura/scripts"
DEPS="${REPO}/.deps"
SIGNER_HOME="/var/lib/hcma-signer"

paso() { echo ""; echo "=== $1 ==="; }

crear_usuario() {
  u="$1"; uid="$2"; home="$3"
  if id "$u" >/dev/null 2>&1; then
    echo "  ok: $u ya existe (uid $(id -u "$u"))"
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
for u in hcma-proxy hcma-monitor; do
  if ! id -nG "$u" 2>/dev/null | tr ' ' '\n' | grep -qx hcma-red; then
    usermod -a -G hcma-red "$u"
    echo "  $u añadido al grupo hcma-red"
  fi
done
chmod 0700 /var/lib/hcma-monitor

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
usermod -a -G hcma-audit charasky 2>/dev/null || echo "  AVISO: no se pudo agregar charasky a hcma-audit"
# Ledger de ejecución del proxy: escritura SOLO hcma-proxy (997); lectura del grupo auditoría.
# El archivo se crea 0644 (umask) por el proxy => ya es legible por el grupo.
install -d -m 0750 -o hcma-proxy -g hcma-audit /var/lib/hcma-proxy/ledger
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
# Antes de desplegar el dashboard: verificar que el puerto 8787 no esté ocupado por
# un proceso ajeno/stale (evita conflicto de bind al arrancar la unidad).
if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE ':8787$'; then
  PROC="$(ss -ltnp 2>/dev/null | grep ':8787 ' | tail -1 | sed -n 's/.*users:((\"\([^\"]*\)\",pid=\([0-9]*\).*/\1 (pid \2)/p')"
  echo "  ⚠️  8787 ya en uso (${PROC:-proceso desconocido}); si no es hcma-dashboard, revisá antes de continuar."
else
  echo "  ok: 8787 libre"
fi
install -m 0644 "${SCRIPTS}/hcma-monitor.service"  /etc/systemd/system/hcma-monitor.service
install -m 0644 "${SCRIPTS}/hcma-proxy.service"    /etc/systemd/system/hcma-proxy.service
install -m 0644 "${REPO}/arquitectura/dashboard/hcma-dashboard.service" \
  /etc/systemd/system/hcma-dashboard.service
systemctl daemon-reload
systemctl enable hcma-monitor.service hcma-proxy.service hcma-dashboard.service
echo "  unidades habilitadas (monitor, proxy, dashboard)"

paso "[5/6] Egreso de red (nftables default-deny SOLO UID 997)"
sh "${SCRIPTS}/firewall_hcma_proxy.sh" apply

paso "[6/6] Manifiesto congelado + verificación"
python3 "${SCRIPTS}/manifiesto_sistema.py"
sh "${REPO}/arquitectura/montaje/verificar.sh"

echo ""
echo "================================================================================"
echo " Montaje completo. Si la verificación marcó un fallo, revisá el paso correspondiente."
echo " La ceremonia de CORTE de estado (checkpoint + store al monitor) es manual: ver README.md."
echo "================================================================================"
