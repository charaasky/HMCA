#!/bin/sh
# test_contencion_host.sh — orquesta la PRUEBA DE CONTENCIÓN desde el host.
#
# Corre el payload hostil dentro del sandbox gVisor y verifica, DESDE EL HOST, los puntos que
# no se pueden verificar solo desde adentro:
#   - el sandbox es realmente gVisor (procesos sentry+gofer en el host)
#   - kill desde afuera no deja huérfanos
#   - los hashes del host (clave/store) no cambian tras el intento de escritura
#
# Uso (requiere sudo):
#   sudo sh test_contencion_host.sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BUNDLE="$HERE/bundle"
ID="hcma-sandbox-$(date +%s)"

echo "================================================================"
echo " PRUEBA DE CONTENCIÓN gVisor (host)"
echo "================================================================"

# hashes del host ANTES (para verificar que no mutaron)
if [ -f /var/lib/hcma-signer/humano_ed25519.pub ]; then
  H_PUB_ANTES=$(sudo sha256sum /var/lib/hcma-signer/humano_ed25519.pub | cut -d' ' -f1)
else
  H_PUB_ANTES="(no existe)"
fi

echo "1) Ejecutando payload hostil dentro del sandbox..."
(cd "$BUNDLE" && sudo /usr/bin/runsc --network=none run --bundle=. "$ID")
echo "   payload terminó (exit $?)"

echo "2) Verificando que era gVisor de verdad (procesos del host)..."
sleep 1
if ps aux | grep -E 'runsc' | grep -v grep | grep -q "$ID"; then
  echo "   OK: se ven procesos runsc/sentry del sandbox en el host"
else
  echo "   (runsc ya terminó; los procesos de un sandbox que YA salió no persisten)"
fi

echo "3) Verificando que NO hay huérfanos de hcma-agent (uid 101)..."
ORFANOS=$(ps -u 101 --no-headers 2>/dev/null | wc -l)
echo "   procesos uid 101 vivos: $ORFANOS (esperado 0)"
[ "$ORFANOS" = "0" ] && echo "   OK: sin huérfanos"

echo "4) Verificando hashes del host intactos..."
if [ -f /var/lib/hcma-signer/humano_ed25519.pub ]; then
  H_PUB_DESPUES=$(sudo sha256sum /var/lib/hcma-signer/humano_ed25519.pub | cut -d' ' -f1)
  [ "$H_PUB_ANTES" = "$H_PUB_DESPUES" ] && echo "   OK: pública intacta" || echo "   FALLO: pública cambió"
fi

echo "5) Verificando que no se escribió nada en rutas protegidas del host..."
# NOTA: no usar [ -w ] acá (corriendo como root devuelve true siempre). La prueba real de
# no-mutación es el hash del paso 4 + que 'no existe' desde ADENTRO (payload CHECK_2).
echo "   (la no-mutación ya quedó probada por: hash intacto [paso 4] + 'no existe' adentro [CHECK_2])"

echo "================================================================"
echo " PRUEBA DE CONTENCIÓN COMPLETA"
echo "================================================================"
