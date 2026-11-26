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

# Puerto real del dashboard (puede no ser 8787 si estaba ocupado al instalar).
if [ -f /etc/hcma/dash.env ]; then
  . /etc/hcma/dash.env
fi
DASH_PORT="${HCMA_DASH_PORT:-8787}"

id hcma-agent   >/dev/null 2>&1 && ok "hcma-agent   (uid $(id -u hcma-agent))"   || bad "hcma-agent"
id hcma-signer  >/dev/null 2>&1 && ok "hcma-signer  (uid $(id -u hcma-signer))"  || bad "hcma-signer"
id hcma-monitor >/dev/null 2>&1 && ok "hcma-monitor (uid $(id -u hcma-monitor))" || bad "hcma-monitor"
id hcma-proxy   >/dev/null 2>&1 && ok "hcma-proxy   (uid $(id -u hcma-proxy))"   || bad "hcma-proxy"
getent group hcma-red >/dev/null 2>&1 && ok "grupo hcma-red presente" || bad "grupo hcma-red presente"

# Boundary de hardware: Firecracker reemplaza a gVisor. gVisor queda como legacy (informativo).
[ -x /usr/bin/firecracker ] \
  && ok "firecracker instalado (/usr/bin/firecracker)" || bad "firecracker instalado (/usr/bin/firecracker)"
[ -f /var/lib/hcma-firecracker/vmlinux ] \
  && ok "vmlinux huésped presente" || bad "vmlinux huésped presente (/var/lib/hcma-firecracker/vmlinux)"
[ -f /var/lib/hcma-firecracker/rootfs.ext4 ] \
  && ok "rootfs huésped presente" || bad "rootfs huésped presente (/var/lib/hcma-firecracker/rootfs.ext4)"
runsc --version >/dev/null 2>&1 \
  && ok "runsc (gVisor) presente (legacy)" \
  || echo "  · gVisor legacy no instalado (esperable tras cutover a Firecracker)"

systemctl is-enabled --quiet hcma-monitor.service  && ok "hcma-monitor  enabled" || bad "hcma-monitor  enabled"
systemctl is-enabled --quiet hcma-proxy.service    && ok "hcma-proxy    enabled" || bad "hcma-proxy    enabled"
systemctl is-enabled --quiet hcma-dashboard.service && ok "hcma-dashboard enabled" || bad "hcma-dashboard enabled"
systemctl is-enabled --quiet hcma-firecracker.service && ok "hcma-firecracker enabled" || bad "hcma-firecracker enabled"
systemctl is-enabled --quiet hcma-px-motor.service && ok "hcma-px-motor enabled" || bad "hcma-px-motor enabled"

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

# El dashboard puede tardar unos segundos en bindear tras el arranque (Python + TLS).
# Reintentamos hasta ~12s antes de marcar fallo, para no dar falso negativo.
dash_ok=0
for _ in 1 2 3 4 5 6; do
  if curl -skS -o /dev/null --max-time 3 https://localhost:${DASH_PORT}/; then
    dash_ok=1
    break
  fi
  sleep 2
done
[ "$dash_ok" -eq 1 ] \
  && ok "dashboard responde en :${DASH_PORT}" || bad "dashboard no responde en :${DASH_PORT}"

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "VERIFICACIÓN: OK"
else
  echo "VERIFICACIÓN: HAY FALLOS"
fi
exit "$FAIL"
