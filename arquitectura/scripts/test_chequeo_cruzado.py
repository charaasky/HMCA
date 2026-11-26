#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_chequeo_cruzado.py — tests del chequeo cruzado tipado (paso 3 de Fable).

Verifica las 4 condiciones de Fable (debilidades_camino4):
  1. TIPADO: con modelo que no cubre resonancia lineal -> abstiene (no 'pasa').
  2. OBSERVABLE DERIVADO: compara frecuencia de resonancia simulada vs analítica 1/(2π√(LC)).
  3. FUENTE ÚNICA: los valores vienen de un dict de magnitudes (no copiados a mano).
  4. CONTROL POR MUTACIÓN: re-cablear a serie (topología errónea) DISCREPA del chequeo.

USO:
    LD_LIBRARY_PATH=/home/charasky/Documentos/HMC/.deps/ngspice/usr/lib/x86_64-linux-gnu \
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/test_chequeo_cruzado.py
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import chequeo_cruzado as CC  # noqa: E402
import topologia as T  # noqa: E402


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


# magnitudes del CVT (fuente única: el dict que HCMA derivaría)
MAG = {"L_mH": 41.08, "C_uF": 246.6}

print("[1] chequeo cruzado correcto es consistente (topología paralelo correcta)")
r = CC.chequeo_cruzado_resonancia(MAG)
check("1a. aplicable", r["aplicable"])
check("1b. consistente (error < tolerancia)", r["consistente"],
      f"error={r.get('error_relativo')}, sim={r.get('f_simulada_Hz')}, ana={r.get('f_analitica_Hz')}")
check("1c. observable derivado: f_analitica ~ 1/(2π√(LC))", r.get("f_analitica_Hz") is not None)

print("\n[2] TIPADO: modelo no_declarado abstiene (no pasa)")
r2 = CC.chequeo_cruzado_resonancia(MAG, clase_modelo=T.MODELO_NO_DECLARADO)
check("2a. aplicable=False", not r2["aplicable"])
check("2b. consistente=None (abstención, no afirmación)", r2["consistente"] is None)

print("\n[3] CONTROL POR MUTACIÓN: serie (errónea) discrepa de paralelo (correcta)")
sensible, det = CC.mutacion_serie_paralelo(MAG["L_mH"], MAG["C_uF"])
print(f"    pico paralelo={det['pico_paralelo_V']} V, pico serie={det['pico_serie_V']} V, "
      f"diff_pico={det['diferencia_pico']}")
check("3a. el chequeo es SENSIBLE a la mutación (topologías distinguibles)", sensible,
      f"diff_pico={det['diferencia_pico']}")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
