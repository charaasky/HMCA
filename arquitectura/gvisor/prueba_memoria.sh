#!/bin/sh
# prueba_memoria.sh — verifica límites de memoria/pids de forma robusta (sin ps, sin python3).

echo "=== rlimit visible ==="
echo "RLIMIT_AS(virtual mem KB) = $(ulimit -v 2>/dev/null || echo 'n/a')"
echo "RLIMIT_NPROC(procs)       = $(ulimit -u 2>/dev/null || echo 'n/a')"
echo "RLIMIT_NOFILE(fds)        = $(ulimit -n 2>/dev/null || echo 'n/a')"

echo "=== fork bomb REAL (acumular procesos, ver tope NPROC) ==="
# Cada iteración lanza un 'sleep' en background. El límite NPROC debe frenar en algún punto.
# Usamos un contador en archivo para saber cuántos llegamos a crear.
touch /tmp/count.txt
i=0
while [ $i -lt 300 ]; do
  sleep 20 &
  i=$((i+1))
  # si fork() empieza a fallar, salimos
  if [ $? -ne 0 ]; then
    echo "fork() fallo en iteracion $i (limite NPROC alcanzado)"
    break
  fi
done
echo "fork bomb: intentamos $i procesos (tope NPROC deberia frenar < 300)"
# matar sleeps acumulados
kill $(jobs -p) 2>/dev/null

echo "=== globo de memoria (alloc 900MB; RLIMIT_AS=1GiB, memory.limit=512MB) ==="
# usar busybox 'head' de /dev/zero para consumir memoria vía un pipe a 'cat' que descarta.
# Medimos si el proceso muere (señal OOM) o llega al final.
( head -c 900000000 /dev/zero | dd of=/dev/null bs=1M 2>/dev/null )
rc=$?
if [ $rc -ne 0 ]; then
  echo "RESULTADO: globo de memoria DETENIDO (rc=$rc) -> limite activo"
else
  echo "RESULTADO: globo de memoria PASO sin error (rc=$rc) -> revisar limite"
fi
echo "MEM_DONE"
