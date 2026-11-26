#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_pedir_cualquier_cosa.py — test del flujo 'pedir cualquier cosa' (post-autorizar).

Verifica el invariante de Fable:
  (1) un pedido desconocido DETECTA hueco (no miente ni matchea un dominio a la fuerza).
  (2) la candidata propuesta es autor=modelo, NO canónica.
  (3) el cálculo es correcto por deducción (P=Φ/η con η de catálogo).
  (4) el gate la deja asumida y lista qué falta (ratificación humana).
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import pedir_cualquier_cosa as P  # noqa: E402
from dominio_luminaria_led import calcular  # noqa: E402


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


print("[1] pedido desconocido detecta hueco")
r = P.resolver("diseñame una luminaria LED de 100 lúmenes")
check("1a. hueco_detectado", r["estado"] == "hueco_detectado", r["estado"])
check("1b. lo declara sin mentir", "declaro" in r.get("mensaje", "").lower() or
      "no tengo" in r.get("mensaje", "").lower())

print("\n[2] candidata autor=modelo, NO canónica")
check("2a. hay candidata", "candidata" in r)
check("2b. autor = modelo", r["candidata"]["autor"] == "modelo", r["candidata"]["autor"])
check("2c. gate: es_canonico = False", r["gate"]["es_canonico"] is False)

print("\n[3] cálculo correcto por deducción (P=Φ/η, I=P/V)")
res = r["candidata"]["resultado"]
check("3a. potencia 100lm/100lm/W = 1.0 W", abs(res["potencia_W"] - 1.0) < 1e-6,
      f"P={res['potencia_W']}")
check("3b. corriente 1W/12V = 0.0833 A", abs(res["corriente_A"] - 0.0833) < 1e-3,
      f"I={res['corriente_A']}")

print("\n[4] pedido a dominio existente NO detecta hueco")
r2 = P.resolver("dimensionar un motor eléctrico de 10 N·m")
check("4a. matchea dominio existente", r2["estado"] == "dominio_existente", r2["estado"])

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
