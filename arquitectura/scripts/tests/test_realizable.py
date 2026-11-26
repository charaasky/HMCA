#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_realizable.py — test del escalón 'realizable' (grado 4).

Invariantes:
  [1] BOM completo + ratings que envuelven -> realizable.
  [2] bloque sin pieza en el BOM -> NO realizable (BOM incompleto).
  [3] rating que NO envuelve el punto de operación -> NO realizable.
  [4] presupuesto de tolerancias que no cierra (worst case) -> NO realizable.
  [5] margen/derating hace rechazar una pieza que sin margen pasaría.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import realizable as R  # noqa: E402

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


def _grafo(ids):
    return {"bloques": [{"id": i, "tipo": "x", "puertos": []} for i in ids],
            "nets": [], "bindings": []}


print("[1] BOM completo + ratings envuelven -> realizable")
v = R.verificar_realizable(
    _grafo(["reg"]),
    {"reg": {"tension": 9.0, "corriente": 0.05}},
    {"reg": {"part_no": "L78L05", "ratings": {"tension": 20.0, "corriente": 0.1},
             "tolerancia": {"tension": 0.05}}})
check("1a. realizable", v["realizable"] is True, str(v))

print("\n[2] bloque sin pieza -> BOM incompleto")
v2 = R.verificar_realizable(_grafo(["reg"]), {"reg": {"tension": 9.0}}, {})
check("2a. NO realizable", v2["realizable"] is False)
check("2b. defecto 'sin pieza'", any("sin pieza" in d for d in v2["defectos"]), str(v2["defectos"]))

print("\n[3] rating no envuelve -> NO realizable")
v3 = R.verificar_realizable(
    _grafo(["reg"]),
    {"reg": {"corriente": 0.05}},
    {"reg": {"part_no": "x", "ratings": {"corriente": 0.01}}})
check("3a. NO realizable", v3["realizable"] is False)
check("3b. defecto 'excede rating'", any("excede rating" in d for d in v3["defectos"]), str(v3["defectos"]))

print("\n[4] tolerancia worst-case no cierra -> NO realizable")
# rating 10V con tol 50% -> peor caso 5V < 9V requerido
v4 = R.verificar_realizable(
    _grafo(["reg"]),
    {"reg": {"tension": 9.0}},
    {"reg": {"part_no": "x", "ratings": {"tension": 10.0}, "tolerancia": {"tension": 0.5}}})
check("4a. NO realizable", v4["realizable"] is False, str(v4))
check("4b. defecto 'tolerancia'", any("tolerancia" in d for d in v4["defectos"]), str(v4["defectos"]))

print("\n[5] margen/derating hace rechazar pieza que sin margen pasa")
puntos = {"reg": {"corriente": 0.08}}
bom = {"reg": {"part_no": "x", "ratings": {"corriente": 0.1}}}
v5_sin_margen = R.verificar_realizable(_grafo(["reg"]), puntos, bom)
v5_con_margen = R.verificar_realizable(_grafo(["reg"]), puntos, bom, margen={"corriente": 2.0})
check("5a. sin margen pasa", v5_sin_margen["realizable"] is True)
check("5b. con margen 2x rechaza", v5_con_margen["realizable"] is False, str(v5_con_margen))

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
