#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_generar_dominio_candidato.py — test de falsación del invariante clave.

El invariante (Fable, fable_pedir_cualquier_cosa punto 6):

    "No existe camino de autor=LLM a un entregable AUTORITATIVO sin un evento de registro
     humano."

Verifica:
  [1] el agente LLM redacta una spec candidata con autor='modelo' (nunca 'ingeniero').
  [2] la candidata se inscribe en HCMA como UNVERIFIED (no canónica).
  [3] la candidata SOLA no genera un entregable autoritativo (invariante de falsación).
  [4] solo un humano (registrar_spec fuente ingeniero/usuario) vuelve canónico el dominio.
  [5] 'modelo' NO está en FUENTE_CONFIABLE (la raíz del guardrail).
  [6] la candidata malformada (autor != 'modelo') se rechaza.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import generar_dominio_candidato as G  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite, FUENTE_CONFIABLE  # noqa: E402
import skill_lib  # noqa: E402

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


def mem():
    return HCMAMemoriaSQLite(":memory:")


print("=" * 74)
print("[1] el agente LLM redacta candidata autor='modelo'")
print("=" * 74)
# spec candidata VÁLIDA propia (el fallback sin LLM ya no inventa fórmulas; esto es lo
# que el LLM produciría). Prueba la lógica de inscripción, no el fallback.
_spec = {
    "nombre": "parlante SEALED de 1 vía",
    "autor": "modelo",
    "inputs": [{"name": "potencia_W", "question": "potencia (W)", "unit": "W"}],
    "formulas": ["SPL = Sens + 10·log10(P) (ley de Ohm, deducible)"],
    "dimensiones": {"spl_1m_dB": "dB"},
    "ranges": {"spl_1m_dB": (60.0, 130.0)},
    "fuentes_citadas": [],
    "lookup_queries": [],
}
spec = G.generar_spec_candidata("diseñame un parlante de 15 W", usar_llm=False)
check("1a. autor = 'modelo'", spec["autor"] == "modelo", spec["autor"])
check("1b. no es 'ingeniero'", spec["autor"] != "ingeniero")
# el fallback sin LLM es GENÉRICO y honesto: no inventa fórmulas (las produce el LLM).
check("1c'. fallback sin-LLM NO inventa fórmulas (genérico)", spec["formulas"] == [],
      spec["formulas"])
# con una spec válida manual se prueba la lógica de inscripción
spec = _spec

print("\n" + "=" * 74)
print("[2] la candidata se inscribe UNVERIFIED (no canónica)")
print("=" * 74)
m = mem()
did, v = G.inscribir_candidata(m, spec)
cand = m.current(f"dominio:{did}:candidata")
check("2a. la candidata NO es canónica (current -> None para unverified)",
      cand is None, "" if cand is None else f"verified={cand['verified']}")
# lectura cruda por versión (primitiva I9) para confirmar que existe pero unverified
raw = m.get_version(f"dominio:{did}:candidata", v)
check("2b. existe por versión pero unverified", raw is not None and not raw["verified"])
check("2c. fuente = 'modelo'", raw["fuente"] == "modelo", raw["fuente"])

print("\n" + "=" * 74)
print("[3] la candidata SOLA no genera entregable autoritativo (invariante de falsación)")
print("=" * 74)
ok, motivo = G.invariante_sin_camino_modelo_a_autoritativo(m, did)
check("3a. no canónico: invariante se cumple", ok, motivo)
check("3b. es_canonico = False", G.es_canonico(m, did) is False)

print("\n" + "=" * 74)
print("[4] solo un humano vuelve canónico el dominio (registrar_spec)")
print("=" * 74)
# el invariante sigue OK mientras no haya spec humana
ok2, _ = G.invariante_sin_camino_modelo_a_autoritativo(m, did)
check("4a. sin spec humana, sigue NO autoritativo", ok2)
# intentar registrar spec con fuente 'modelo' DEBE fallar (skill_lib lo rechaza)
try:
    skill_lib.registrar_spec(m, did, {"nombre": "x", "tests_dorados": [{"id": "t1"}]},
                             fuente="modelo")
    check("4b. registrar_spec con fuente 'modelo' falla", False, "NO falló")
except ValueError:
    check("4b. registrar_spec con fuente 'modelo' falla", True)
# registrar spec con fuente humana sí lo vuelve canónico
spec_canonica = {
    "nombre": "parlante SEALED de 1 vía",
    "tests_dorados": [{"id": "t1", "inputs": {}, "esperado": {}}],
    "outputs": [{"nombre": "spl_1m_dB", "tolerancia": {"tipo": "abs", "valor": 0.01}}],
}
skill_lib.registrar_spec(m, did, spec_canonica, fuente="ingeniero")
check("4c. registrar_spec humano => es_canonico True", G.es_canonico(m, did) is True)

print("\n" + "=" * 74)
print("[5] 'modelo' NO está en FUENTE_CONFIABLE (raíz del guardrail)")
print("=" * 74)
check("5a. 'modelo' ausente de FUENTE_CONFIABLE", "modelo" not in FUENTE_CONFIABLE,
      sorted(FUENTE_CONFIABLE))

print("\n" + "=" * 74)
print("[6] candidata malformada se rechaza")
print("=" * 74)
try:
    G._validar_spec_candidata({"nombre": "x", "autor": "ingeniero", "inputs": [], "formulas": []})
    check("6a. autor != 'modelo' se rechaza", False, "NO rechazó")
except ValueError:
    check("6a. autor != 'modelo' se rechaza", True)
try:
    G._validar_spec_candidata({"nombre": "x", "autor": "modelo", "inputs": [], "formulas": []})
    check("6b. fórmulas vacía se rechaza", False, "NO rechazó")
except ValueError:
    check("6b. fórmulas vacía se rechaza", True)

print("\n" + "=" * 74)
print("[7] campos opcionales (mecanismos/puntos/bom) validan su FORMA (sin promo)")
print("=" * 74)
# bom malformado (sin ratings) se rechaza en la validación de candidata
try:
    G._validar_spec_candidata({"nombre": "x", "autor": "modelo",
                               "inputs": [{"name": "a", "unit": "1"}],
                               "formulas": ["x"],
                               "bom": {"reg": {"part_no": "cand", "tolerancia": {}}}})
    check("7a. bom sin ratings se rechaza", False, "NO rechazó")
except ValueError:
    check("7a. bom sin ratings se rechaza", True)
# mecanismos malformado (sin funcion/clase_modelo) se rechaza
try:
    G._validar_spec_candidata({"nombre": "x", "autor": "modelo",
                               "inputs": [{"name": "a", "unit": "1"}],
                               "formulas": ["x"],
                               "mecanismos": {"reg": {"clase_modelo": "lineal"}}})
    check("7b. mecanismos sin funcion se rechaza", False, "NO rechazó")
except ValueError:
    check("7b. mecanismos sin funcion se rechaza", True)
# espec vacía de campos opcionales es VALIDA (no son obligatorios)
G._validar_spec_candidata({"nombre": "x", "autor": "modelo",
                           "inputs": [{"name": "a", "unit": "1"}], "formulas": ["x"]})
check("7c. campos opcionales ausentes son válidos (no obligatorios)", True)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
