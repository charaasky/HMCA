#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_suite_adversarial.py — valida la suite adversarial contra A1 (0 ataques exitosos).

P0 de Fable (MODELO_AMENAZA.md §4/§5): la suite a medida SOLO es evidencia si viene con MATRIZ
DE ABLACIÓN — apagar cada mecanismo debe hacer que SU ataque pase. Acá verificamos:

  1. Contra A1 (mecanismos prendidos): 0 ataques exitosos.
  2. Determinismo: dos corridas dan el mismo resultado.
  3. (ablación conceptual) cada ataque tiene un `mecanismo` identificable en el código
     defensivo correspondiente, de modo que se pueda apagar en un experimento de ablación.

Nota: la ablación por-monitor completo (apagar el CAS, el nonce, etc.) se hace por FLAGS en un
experimento, no por mutar el source. Acá fijamos el invariante que la ablación debe mostrar.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import suite_adversarial as SA  # noqa: E402

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


print("=" * 74)
print("[1] contra A1 (mecanismos prendidos): 0 ataques exitosos")
print("=" * 74)
suite = SA.correr_suite()
exitosos = [n for n, r in suite.items() if r["exitoso"]]
check("1a. 0 ataques exitosos", len(exitosos) == 0,
      f"exitosos={exitosos}")
check("1b. cubre los 8 ataques del modelo", len(suite) == 8, len(suite))

print("\n[2] cada ataque declara su MECANISMO de defensa (para la ablación)")
mecanismos = {r["mecanismo"] for r in suite.values()}
# 8 ataques, 7 mecanismos: replay y consumo_concurrente comparten consumir_autorizacion_atomica
# (el CAS del flag un-solo-uso).
check("2a. 7 mecanismos para 8 ataques (uno compartido)",
      len(mecanismos) == 7 and len(suite) == 8, (len(mecanismos), len(suite)))

print("\n[3] determinismo: dos corridas dan el mismo veredicto")
suite2 = SA.correr_suite()
iguales = all(suite[n]["exitoso"] == suite2[n]["exitoso"] for n in suite)
check("3a. mismo resultado en dos corridas", iguales)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
