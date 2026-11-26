#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_topologia_bloques.py — test del grafo de bloques + validador + promoción.

Verifica los invariantes que Fable pidió (fable_fabricable, punto 5):
  [1] grafo bien formado (potencia y señal separadas) pasa el validador -> ASUMIDA (grado 1).
  [2] el grafo que MEZCLA potencia con señal es rechazado (el bug Fable marcó en el multímetro).
  [3] sin grafo -> AUSENTE -> formula_suelta (grado 0).
  [4] grafo con puerto flotante (net de un solo puerto) es rechazado.
  [5] binding a variable/port inexistente es rechazado.
  [6] el LLM (firmada=False) NUNCA produce DECLARADA (grado 2); solo ASUMIDA.
  [7] firmada=True produce DECLARADA (grado 2), y solo con firma humana.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import topologia_bloques as TB  # noqa: E402
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


def _grafo_ok():
    return {
        "bloques": [
            {"id": "bateria", "tipo": "fuente", "puertos": [{"id": "V+", "dominio": "potencia"}, {"id": "V-", "dominio": "tierra"}]},
            {"id": "adc", "tipo": "adc", "puertos": [
                {"id": "V+", "dominio": "potencia"}, {"id": "V-", "dominio": "tierra"},
                {"id": "in", "dominio": "senal"}]},
            {"id": "divisor", "tipo": "atenuador", "puertos": [
                {"id": "in", "dominio": "senal"}, {"id": "out", "dominio": "senal"}, {"id": "gnd", "dominio": "tierra"}]},
        ],
        "nets": [
            {"id": "vbus", "conexiones": [{"bloque": "bateria", "puerto": "V+"}, {"bloque": "adc", "puerto": "V+"}]},
            {"id": "gnd", "conexiones": [{"bloque": "bateria", "puerto": "V-"}, {"bloque": "adc", "puerto": "V-"}, {"bloque": "divisor", "puerto": "gnd"}]},
            {"id": "sig", "conexiones": [{"bloque": "divisor", "puerto": "out"}, {"bloque": "adc", "puerto": "in"}]},
        ],
        "bindings": [
            {"variable": "v_in", "bloque": "divisor", "puerto": "in"},
        ],
    }


print("[1] grafo bien formado (potencia y señal separadas) -> ASUMIDA")
ok, defs = TB.validar_grafo(_grafo_ok())
check("1a. valida ok", ok, str(defs))
grado, falt = TB.grado_desde_bloques(_grafo_ok(), firmada=False)
check("1b. grado derivado_bajo_supuesto", grado == "derivado_bajo_supuesto", grado)

print("\n[2] grafo que MEZCLA potencia con señal es rechazado")
grafo_mal = _grafo_ok()
# conectamos la batería directamente al divisor (mezcla potencia con señal)
grafo_mal["nets"].append(
    {"id": "mezcla", "conexiones": [{"bloque": "bateria", "puerto": "V+"},
                                     {"bloque": "divisor", "puerto": "in"}]})
ok2, defs2 = TB.validar_grafo(grafo_mal)
check("2a. mezcla potencia/señal rechazada", not ok2, str(defs2))
check("2b. defecto menciona dominios incompatibles",
      any("incompatibles" in d for d in defs2), str(defs2))

print("\n[3] sin grafo -> AUSENTE -> formula_suelta")
grado3, _ = TB.grado_desde_bloques({}, firmada=False)
check("3a. formula_suelta", grado3 == "formula_suelta", grado3)

print("\n[4] puerto flotante (net de un solo puerto) rechazado")
grafo_flot = _grafo_ok()
grafo_flot["nets"].append({"id": "suelta", "conexiones": [{"bloque": "divisor", "puerto": "in"}]})
ok4, _ = TB.validar_grafo(grafo_flot)
check("4a. net suelta rechazada", not ok4)

print("\n[5] binding a bloque inexistente rechazado")
grafo_bad_binding = _grafo_ok()
grafo_bad_binding["bindings"].append({"variable": "x", "bloque": "no_existe"})
ok5, defs5 = TB.validar_grafo(grafo_bad_binding)
check("5a. binding inválido rechazado", not ok5, str(defs5))

print("\n[6] el LLM (firmada=False) NUNCA produce DECLARADA")
estado, _ = TB.estado_topologia_bloques(_grafo_ok(), firmada=False)
check("6a. estado ASUMIDA (no DECLARADA)", estado == T.ASUMIDA, estado)

print("\n[7] firmada=True produce DECLARADA (grado conectado), solo con firma humana")
estado7, _ = TB.estado_topologia_bloques(_grafo_ok(), firmada=True)
check("7a. estado DECLARADA", estado7 == T.DECLARADA, estado7)
grado7, _ = TB.grado_desde_bloques(_grafo_ok(), firmada=True)
check("7b. grado conectado", grado7 == "conectado", grado7)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
