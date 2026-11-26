#!/bin/sh
# prueba_python.sh — verifica python3+numpy+scipy+sympy dentro del sandbox.

echo "=== python3 + numpy + scipy + sympy en sandbox ==="
cd /tmp
python3 -c "
import sys, numpy, scipy, sympy
print('python', sys.version.split()[0])
print('numpy', numpy.__version__)
print('scipy', scipy.__version__)
print('sympy', sympy.__version__)
# smoke: cálculo real que usa numpy (lo que usa simulador.py)
import numpy as np
a = np.array([1.0, 2.0, 3.0])
print('numpy mean:', a.mean())
# sympy: integral simbólica (lo que usa el dominio de fórmulas)
x = sympy.symbols('x')
print('sympy integral x^2 =', sympy.integrate(x**2, x))
print('PYTHON_OK')
" 2>&1

echo "PY_DONE"
