#!/bin/sh
# verificar.sh — verificación de salud post-montaje. Exit 0 = OK, 1 = hay fallos.
# SPDX-License-Identifier: Apache-2.0
# Uso:  sudo sh verificar.sh   (o corre dentro de montar.sh)
set -u

if [ "$(id -u)" -ne 0 ]; then
  exec sudo sh "$0" "$@"
fi

FAIL=0
ok()  { echo "  ✅ $1"; }
bad() { echo "  ❌ $1"; FAIL=1; }

echo "=== Verificación de salud HCMA ==="

id hcma-agent   >/dev/null 2>&1 && ok "hcma-agent   (uid $(id -u hcma-agent))"   || bad "hcma-agent"
id hcma-signer  >/dev/null 2>&1 && ok "hcma-signer  (uid $(id -u hcma-signer))"  || bad "hcma-signer"
id hcma-monitor >/dev/null 2>&1 && ok "hcma-monitor (uid $(id -u hcma-monitor))" || bad "hcma-monitor"
id hcma-proxy   >/dev/null 2>&1 && ok "hcma-proxy   (uid $(id -u hcma-proxy))"   || bad "hcma-proxy"
getent group hcma-red >/dev/null 2>&1 && ok "grupo hcma-red presente" || bad "grupo hcma-red presente"

runsc --version >/dev/null 2>&1 && ok "runsc (gVisor) instalado" || bad "runsc (gVisor) instalado"

systemctl is-enabled --quiet hcma-monitor.service  && ok "hcma-monitor  enabled" || bad "hcma-monitor  enabled"
systemctl is-enabled --quiet hcma-proxy.service    && ok "hcma-proxy    enabled" || bad "hcma-proxy    enabled"
systemctl is-enabled --quiet hcma-dashboard.service && ok "hcma-dashboard enabled" || bad "hcma-dashboard enabled"

[ -f /etc/hcma/publica.pin ] && ok "pin (ancla de confianza) presente" || bad "pin presente"
[ -f /var/lib/hcma-monitor/store/memoria.db ] \
  && ok "store del monitor presente" \
  || bad "store del monitor presente (¿falta ceremonia de cutover?)"

# Frontera UID: el AGENTE no debe poder leer la privada del signer.
if su -s /bin/sh hcma-agent -c 'cat /var/lib/hcma-signer/humano_ed25519.key' >/dev/null 2>&1; then
  bad "agente lee la privada del signer (frontera ROTA)"
else
  ok "agente NO lee la privada del signer (frontera activa)"
fi

curl -skS -o /dev/null --max-time 5 https://localhost:8787/ \
  && ok "dashboard responde en :8787" || bad "dashboard no responde en :8787"

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "VERIFICACIÓN: OK"
else
  echo "VERIFICACIÓN: HAY FALLOS"
fi
exit "$FAIL"
