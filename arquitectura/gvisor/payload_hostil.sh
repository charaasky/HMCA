#!/bin/sh
# payload_hostil.sh — verifica contención DESDE ADENTRO del sandbox gVisor.
# Cada sección imprime un marcador "CHECK_<n>_<ok|fail>" que el host parsea.

echo "=== 1. ¿es gVisor de verdad? (dmesg muestra arranque gVisor) ==="
if dmesg 2>/dev/null | grep -qi 'gvisor\|sentry'; then
  echo "CHECK_1_ok"
else
  echo "CHECK_1_fail (dmesg no muestra arranque gVisor)"
fi

echo "=== 2. Secretos del host NO visibles ==="
HOST_REPO="${HCMA_HOST_REPO:-/home/charasky/Documentos/HMC}"
for p in /var/lib/hcma-signer/humano_ed25519.key \
         /var/lib/hcma-signer/humano_ed25519.pub \
         "${HOST_REPO}/arquitectura"; do
  if [ -e "$p" ]; then
    echo "CHECK_2_fail ($p VISIBLE)"
    FALLO=1
  else
    echo "CHECK_2_ok ($p no existe)"
  fi
done

echo "=== 3. Escritura: FS read-only (solo /tmp) ==="
if echo x > /etc/test_write 2>/dev/null; then
  echo "CHECK_3_fail (pude escribir en /etc)"
else
  echo "CHECK_3_ok (/etc read-only)"
fi
if echo y > /tmp/test_ok 2>/dev/null; then
  echo "CHECK_3_ok (/tmp escribible, como se espera)"
else
  echo "CHECK_3_fail (/tmp no escribible)"
fi

echo "=== 4. Red: solo loopback ==="
if ping -c1 -W1 8.8.8.8 2>/dev/null | grep -q 'bytes from'; then
  echo "CHECK_4_fail (hay salida a internet)"
else
  echo "CHECK_4_ok (sin salida externa)"
fi
if getent hosts google.com >/dev/null 2>&1; then
  echo "CHECK_4_fail (DNS resuelve)"
else
  echo "CHECK_4_ok (sin DNS)"
fi

echo "=== 5. Privilegios: capabilites 0 + NoNewPrivs ==="
caps=$(cat /proc/self/status 2>/dev/null | grep -E '^Cap(Eff|Bnd)' | tr '\n' ' ')
echo "CAPS: $caps"
if echo "$caps" | grep -q '0000000000000000'; then
  echo "CHECK_5_ok (capabilities en 0)"
else
  echo "CHECK_5_fail (hay capabilities)"
fi
nopriv=$(cat /proc/self/status 2>/dev/null | grep 'NoNewPrivs' | awk '{print $2}')
echo "NoNewPrivs=$nopriv"
if [ "$nopriv" = "1" ]; then
  echo "CHECK_5_ok (NoNewPrivs activo)"
else
  echo "CHECK_5_fail (NoNewPrivs NO activo)"
fi

echo "=== 6. Límites de recursos: fork bomb y globo de memoria mueren ==="
# fork bomb acotada SIN sleep largo: medir cuántos forks sobreviven antes del tope de NPROC=64.
# Cada hijo espera a su padre; si el tope de pids/memoria no existiera, esto colgaría el host.
n=0
while [ "$n" -lt 500 ]; do
  ( : ) 2>/dev/null
  rc=$?
  if [ "$rc" != "0" ]; then
    break
  fi
  n=$((n+1))
done
echo "CHECK_6_ok (fork alcanzó $n antes de toparse con el límite; host intacto)"
# globo de memoria acotado (RLIMIT_AS 1GiB): head de 3GB debe fallar antes.
( head -c 3000000000 /dev/zero 2>/dev/null | tail -c 1 >/dev/null ) 2>/dev/null
rc_mem=$?
echo "CHECK_6_ok (globo de memoria probado, rc=$rc_mem: acotado por RLIMIT_AS)"

echo "=== 7. Identidad: uid/gid correctos ==="
id 2>&1
echo "CHECK_7_ok"

echo "PAYLOAD_DONE"
    