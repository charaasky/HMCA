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

[ "$(id -u hcma-agent 2>/dev/null)"   = "101" ] && ok "hcma-agent  (uid 101)" || bad "hcma-agent  (uid 101)"
[ "$(id -u hcma-signer 2>/dev/null)"  = "102" ] && ok "hcma-signer (uid 102)" || bad "hcma-signer (uid 102)"
[ "$(id -u hcma-monitor 2>/dev/null)" = "999" ] && ok "hcma-monitor(uid 999)" || bad "hcma-monitor(uid 999)"
[ "$(id -u hcma-proxy 2>/dev/null)"   = "997" ] && ok "hcma-proxy  (uid 997)" || bad "hcma-proxy  (uid 997)"
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
