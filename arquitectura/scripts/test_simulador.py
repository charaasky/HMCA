#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de calibración del simulador I-SIM (paso 6 de la lista de Fable §7decies).

Verifica el pipeline del simulador como DERIVADOR (oráculo numérico offline):
  1. Determinismo: dos corridas idénticas -> mismo resultado (extract_hash no aplica acá,
     pero sí el valor escalar).
  2. Concordancia numérica: RC pasa-bajos, frecuencia de corte simulada vs analítica
     dentro de tolerancia.
  3. Control negativo: un R/C "mutante" (factor 10) -> frecuencia de corte se desplaza
     ~1 década (el simulador detecta el cambio; no está fiteando).

NO es un test de HCMA (no toca memoria): es el test del MOTOR de simulación.
Uso:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps \
      python3 arquitectura/scripts/test_simulador.py
"""

import math
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import simulador  # noqa: E402


PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")
    return cond


# 1. determinismo
fc_a = simulador.rc_frecuencia_corte(1000.0, 1.0)
fc_b = simulador.rc_frecuencia_corte(1000.0, 1.0)
check("1. determinismo: dos corridas idénticas dan el mismo valor", fc_a == fc_b,
      f"{fc_a} vs {fc_b}")

# 2. concordancia con la analítica
fc_analitico = 1.0 / (2 * math.pi * 1000.0 * 1e-6)
err_pct = abs(fc_a - fc_analitico) / fc_analitico * 100
check("2. concordancia: fc simulada ≈ 1/(2πRC) dentro de 1%",
      err_pct < 1.0, f"sim={fc_a} analit={fc_analitico:.2f} err={err_pct:.2f}%")

# 3. control negativo: R x10 => fc /10 (el simulador detecta el desplazamiento)
fc_mutante = simulador.rc_frecuencia_corte(10000.0, 1.0)  # R 1k -> 10k
ratio = fc_a / fc_mutante
check("3. control negativo: R x10 desplaza fc ~1 década",
      9.0 < ratio < 11.0, f"ratio {ratio:.2f} (esperado ~10)")

# 4. THD: el tanque resonante LC ATENÚA armónicos (ventaja del CVT frente a electrónicos)
t_in, t_out = simulador.thd_tanque_resonante(30.81, 328.8)
check("4. el LC reduce los armónicos (THD_salida << THD_entrada)",
      t_out < t_in * 0.2, f"entrada={t_in}% salida={t_out}%")
check("4b. THD de salida queda bajo (<5%, senoidal limpia)",
      t_out < 5.0, f"salida={t_out}%")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
print(f"  (ngspice {simulador.version_ngspice()})")
sys.exit(1 if FAIL else 0)
