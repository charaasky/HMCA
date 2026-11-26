#!/bin/sh
# prueba_positiva.sh — verifica que las herramientas REALES corren dentro del sandbox.
# (prueba positiva de compatibilidad que pidió Fable: "si falla vas a terminar aflojando flags")
#
# ngspice: binario RECOMPILADO sin GUI/X/audio/readline (build_ngspice.sh), copiado al rootfs.
# kicad-cli: aún pendiente de imagen propia (requiere kicad en rootfs, fase siguiente).
# python3: aún pendiente de imagen propia (intérprete+stdlib+numpy+scipy+sympy, fase siguiente).

echo "=== A. ngspice (recompilado sin GUI, en rootfs) ==="
if [ -x /usr/local/bin/ngspice ]; then
  VER=$(/usr/local/bin/ngspice --version 2>&1 | sed -n '2p')
  echo "binario presente: $VER"
  # smoke test real: análisis .op + .ac con write out.raw (lo que usa simulador.py)
  printf 'RC low-pass\nV1 n1 0 AC 1\nR1 n1 n2 1000\nC1 n2 0 1u\n.ac dec 100 10 1e6\n.control\nset filetype=ascii\nrun\nwrite out.raw\nquit\n.endc\n.end\n' > /tmp/rc_test.cir
  cd /tmp && /usr/local/bin/ngspice -b /tmp/rc_test.cir 2>&1 | grep -iE 'done|error' | tail -2
  if [ -f /tmp/out.raw ]; then
    echo "CHECK_POS_ngspice_ok (out.raw generado)"
  else
    echo "CHECK_POS_ngspice_fail (no generó out.raw)"
  fi
else
  echo "CHECK_POS_ngspice_fail (binario ngspice no está en rootfs)"
fi

echo "=== B. kicad-cli (pendiente de imagen propia) ==="
if command -v kicad-cli >/dev/null 2>&1; then
  kicad-cli version 2>&1 | head -1
  echo "CHECK_POS_kicad_ok"
else
  echo "CHECK_POS_kicad_pendiente (kicad-cli requiere imagen propia, fase siguiente)"
fi

echo "=== C. python3 (pendiente de imagen propia) ==="
if command -v python3 >/dev/null 2>&1; then
  python3 -c "import sys; print('python', sys.version.split()[0])" 2>&1
  echo "CHECK_POS_python_ok"
else
  echo "CHECK_POS_python_pendiente (python3 requiere imagen propia, fase siguiente)"
fi

echo "POS_DONE"
