#!/bin/sh
# prueba_blas.sh — verifica comportamiento de OpenBLAS (hilos) y el control de OMP_NUM_THREADS.

echo "=== hilos OpenBLAS / numpy (sin control) ==="
cd /tmp
python3 -c "
import numpy as np
import os
# cuántos hilos ve OpenBLAS por defecto
try:
    import numpy._core._multiarray_umath as mu
except Exception:
    pass
# openblas num threads
try:
    import ctypes
    lib = ctypes.CDLL('libopenblas.so.0', use_errno=True) if os.path.exists('/usr/lib/x86_64-linux-gnu/libopenblas.so.0') else None
    if lib:
        lib.openblas_get_num_threads.restype = ctypes.c_int
        print('openblas_get_num_threads (default):', lib.openblas_get_num_threads())
except Exception as e:
    print('no pude leer openblas threads:', e)
print('OMP_NUM_THREADS env:', os.environ.get('OMP_NUM_THREADS', '(no seteado)'))
# cálculo que dispara BLAS (matmul grande)
a = np.random.rand(1000, 1000)
b = np.random.rand(1000, 1000)
c = a @ b
print('matmul OK, shape:', c.shape)
print('BLAS_DONE')
" 2>&1

echo "=== con OMP_NUM_THREADS=1 ==="
cd /tmp
OMP_NUM_THREADS=1 python3 -c "
import os, numpy as np
print('OMP_NUM_THREADS:', os.environ.get('OMP_NUM_THREADS'))
a = np.random.rand(1000,1000); b = np.random.rand(1000,1000)
print('matmul con 1 hilo OK')
" 2>&1

echo "BLAS_TEST_DONE"
