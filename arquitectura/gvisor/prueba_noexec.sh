#!/bin/sh
# prueba_noexec.sh — verifica mmap(PROT_EXEC) en mounts con/sin noexec (dentro del sandbox).

echo "=== TEST noexec vs mmap(PROT_EXEC) ==="

echo "--- A. /tmp (ahora con noexec, tras el fix) ---"
/usr/local/bin/test_noexec /tmp/prob_a.txt

echo "--- B. /dev/shm (mount con noexec) ---"
/usr/local/bin/test_noexec /dev/shm/prob_b.txt

echo "--- C. rootfs (readonly: para referencia) ---"
/usr/local/bin/test_noexec /usr/prob_c.txt 2>&1 || echo "(esperado: rootfs ro, ni siquiera escribe)"

echo "NOEXEC_DONE"
