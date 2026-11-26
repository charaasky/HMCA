#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_verificador_forma.py — test del control de forma en el egreso (dictamen Fable).

Chequea que el verificador:
  - aprueba un netlist con símbolos resueltos y conexiones a pines válidos;
  - rechaza un componente de tipo NO resuelto (fidelidad => no fallback silencioso);
  - rechaza una conexión a un pin inexistente;
  - es ASIMÉTRICO (nunca dice 'útil', solo 'sin defectos' vs 'defectuoso').
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

import verificador_forma as VF  # noqa: E402

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + ((" " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


netlist_ok = {
    "componentes": [
        {"ref": "fuente_12V", "nombre": "Fuente", "tipo": "fuente_tension",
         "pines": [{"num": 1, "nombre": "V+"}, {"num": 2, "nombre": "GND"}]},
        {"ref": "R1", "nombre": "Resistor", "tipo": "resistor",
         "pines": [{"num": 1}, {"num": 2}]},
    ],
    "conexiones": [
        {"de": {"ref": "fuente_12V", "pin": 1}, "a": {"ref": "R1", "pin": 1}},
    ],
}

print("=== [1] netlist válido aprueba ===")
v = VF.verificar_forma(netlist_ok)
check("1a. aprueba True", v["aprueba"] is True, str(v["defectos"]))

print("=== [2] símbolo no resuelto => defecto (fidelidad) ===")
netlist_mal = {
    "componentes": [
        {"ref": "fuente_12V", "nombre": "Fuente", "tipo": "fuente_tension",
         "pines": [{"num": 1}, {"num": 2}]},
        {"ref": "X9", "nombre": "coso", "tipo": "widget_desconocido",
         "pines": [{"num": 1}, {"num": 2}]},
    ],
    "conexiones": [],
}
v = VF.verificar_forma(netlist_mal)
check("2a. aprueba False", v["aprueba"] is False)
check("2b. defecto menciona símbolo no resuelto",
      any("símbolo NO resuelto" in d for d in v["defectos"]))

print("=== [3] conexión a pin inexistente => defecto ===")
netlist_pin = {
    "componentes": [
        {"ref": "R1", "nombre": "R", "tipo": "resistor", "pines": [{"num": 1}, {"num": 2}]},
    ],
    "conexiones": [
        {"de": {"ref": "R1", "pin": 1}, "a": {"ref": "R1", "pin": 99}},  # pin 99 no existe
    ],
}
v = VF.verificar_forma(netlist_pin)
check("3a. aprueba False", v["aprueba"] is False)
check("3b. defecto menciona pin inexistente",
      any("pin inexistente" in d for d in v["defectos"]))

print("=== [4] asimetría: nunca dice 'útil', solo 'sin defectos' ===")
v = VF.verificar_forma(netlist_ok)
check("4a. no hay clave 'util' en el veredicto", "util" not in v and "útil" not in v)
check("4b. solo tiene aprueba/defectos/reporte", set(v.keys()) <= {"aprueba", "defectos", "reporte"})

print("=== [5] layout generoso maneja nombres largos SIN desbordar ===")
# netlist con muchos componentes y nombres de pin largos: el layout con margen generoso
# debe aprobar (no desbordar). Antes esto desbordaba; ahora el verificador lo aprueba.
netlist_grande = {
    "componentes": [
        {"ref": f"R{i}", "nombre": "Resistor con nombre muy largo", "tipo": "resistor",
         "pines": [{"num": 1, "nombre": "terminal_largo_1"}, {"num": 2, "nombre": "terminal_largo_2"}]}
        for i in range(8)
    ],
    "conexiones": [],
}
v = VF.verificar_forma(netlist_grande)
check("5a. layout generoso no desborda con 8 componentes y nombres largos",
      v["aprueba"] is True, str(v["defectos"][:2]))

print("=== [6] font_size por debajo del umbral => defecto ===")
v2 = VF.verificar_forma(netlist_ok, umbral={"font_size_minimo": 999.0})
check("6a. umbral de font mínimo exagerado => defecto", v2["aprueba"] is False)
check("6b. defecto menciona font_size", any("font_size" in d for d in v2["defectos"]))

print("\nRESULTADO:", len(FAIL), "FAIL")
sys.exit(1 if FAIL else 0)
