#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_mecanismo_funcional.py — test del escalón 'funcional' (grado 3).

Invariantes:
  [1] función LINEAL cubierta por modelo lineal -> funcional.
  [2] función NO-LINEAL (conmutación) con modelo lineal -> NO cubierta (se niega a promover).
  [3] función NO-LINEAL como caja negra (contrato datasheet) -> funcional CON techo (dato contingente).
  [4] bloque que no afirma función no cuenta para el global.
  [5] grafo con algún bloque no cubierto -> global NO funcional (grado conectado).
  [6] sin bloques que afirmen función -> NO funcional (no hay nada que verificar).
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import mecanismo_funcional as MF  # noqa: E402

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
    bloques = [{"id": i, "tipo": "x", "puertos": []} for i in ids]
    return {"bloques": bloques, "nets": [], "bindings": []}


print("[1] función lineal cubierta por modelo lineal")
v = MF.verificar_funcional(_grafo(["r1"]), {"r1": {"funcion": "Atenúa", "clase_modelo": "lineal"}})
check("1a. funcional", v["funcional"] is True, str(v))
check("1b. grado funcional", v["grado"] == "funcional", v["grado"])

print("\n[2] función no-lineal (conmutación) con modelo lineal se NIEGA")
v2 = MF.verificar_funcional(_grafo(["adc"]), {"adc": {"funcion": "convierte", "clase_modelo": "lineal", "mecanismo": "conmutacion"}})
check("2a. NO funcional", v2["funcional"] is False)
check("2b. defecto cita 'no cubierto'", any("NO cubierto" in d for d in v2["defectos"]), str(v2["defectos"]))
check("2c. grado conectado", v2["grado"] == "conectado", v2["grado"])

print("\n[3] función no-lineal como caja negra -> funcional CON techo")
v3 = MF.verificar_funcional(_grafo(["adc"]), {"adc": {"funcion": "convierte", "clase_modelo": "caja_negra", "mecanismo": "conmutacion"}})
check("3a. funcional", v3["funcional"] is True)
check("3b. motivo cita 'dato contingente'", any("dato contingente" in b["motivo"] for b in v3["bloques"]),
      str(v3["bloques"]))

print("\n[4] bloque que no afirma función no cuenta")
v4 = MF.verificar_funcional(_grafo(["r1"]), {"r1": {}})  # sin funcion ni clase
check("4a. NO funcional (nada que verificar)", v4["funcional"] is False)

print("\n[5] grafo con un bloque no cubierto -> global NO funcional")
modelos_mixto = {
    "r1": {"funcion": "Atenúa", "clase_modelo": "lineal"},
    "adc": {"funcion": "convierte", "clase_modelo": "lineal", "mecanismo": "conmutacion"},
}
v5 = MF.verificar_funcional(_grafo(["r1", "adc"]), modelos_mixto)
check("5a. NO funcional global", v5["funcional"] is False)
check("5b. grado conectado", v5["grado"] == "conectado", v5["grado"])

print("\n[6] sin bloques que afirmen función -> NO funcional")
v6 = MF.verificar_funcional(_grafo(["r1"]), {})
check("6a. NO funcional", v6["funcional"] is False)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
