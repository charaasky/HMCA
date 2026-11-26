#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_procedencia_entregable.py — test del sello de procedencia y del gate de entregable.

Cubre los bugs que Fable 5.2 confirmó (fable_bug_procedencia):

  [1] dict crudo (sin SpecSellada) NO se registra.
  [2] canal llm_runtime: techo UNVERIFIED (no promueve a canónica).
  [3] canal humano_simulado: techo UNVERIFIED (nunca ratifica de verdad).
  [4] BUG-A: forjar el carrier no alcanza — la firma humana no re-verificable se rechaza.
  [5] BUG-A/BUG-E: canal humano con firma válida (did, pedido, spec) SÍ registra canónica.
  [6] BUG-B: spec sin fórmulas => SinCandidata (antes de escribir evento).
  [7] BUG-D: hash estricto rechaza NaN/inf (no stringifica silencioso).
  [8] entregable: generar_pdf rechaza dict crudo.
  [9] entregable: topología AUSENTE => formula_suelta + advertencia.
  [10] entregable: número sin node_id no se imprime.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import procedencia as P  # noqa: E402
import entregable as E  # noqa: E402
import topologia as T  # noqa: E402

# clave HMAC de prueba: le damos al canal humano una clave conocida para el test
os.environ["HCMA_HUMANO_HMAC_KEY"] = "clave-de-prueba-para-los-tests"

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


SPEC = {
    "nombre": "dominio ejemplo",
    "autor": "modelo",
    "inputs": [{"name": "x", "unit": "1"}],
    "formulas": ["y = 2*x"],
    "dimensiones": {"y": "1"},
    "ranges": {"y": (-100, 100)},
    "fuentes_citadas": [],
    "lookup_queries": [],
}

# spec canónica válida (para skill_lib.registrar_spec): exige tests_dorados + outputs
SPEC_CANONICA = dict(SPEC, tests_dorados=[
    {"id": "t1", "inputs": {"x": 2}, "esperado": {"y": 4}}],
    outputs=[{"nombre": "y", "tolerancia": {"tipo": "abs", "valor": 1e-6}}])


def _firma(did, pedido, spec):
    return P._firmar_humano(did, pedido, spec)


print("[1] dict crudo (sin SpecSellada) NO se registra")
m = mem()
try:
    P.registrar_spec_sellada(m, "ejemplo", SPEC)
    check("1a. rechaza", False, "NO rechazó")
except P.ProcedenciaInvalida:
    check("1a. rechaza", True)

print("\n[2] canal llm_runtime: techo UNVERIFIED")
m2 = mem()
s_llm = P.sellar_spec_llm(m2, "pedido", SPEC)
P.registrar_spec_sellada(m2, "dominio_ejemplo", s_llm)
# el did real lo deriva inscribir_candidata del nombre normalizado: 'dominio_ejemplo'
check("2a. no hay spec canónica", m2.current("skill:dominio_ejemplo:spec") is None)
check("2b. candidata unverified", m2.current("dominio:dominio_ejemplo:candidata") is None)
check("2c. canal = llm_runtime", s_llm.canal == "llm_runtime")

print("\n[3] canal humano_simulado: techo UNVERIFIED")
m3 = mem()
s_sim = P.sellar_humano_simulado(m3, "pedido", SPEC)
P.registrar_spec_sellada(m3, "dominio_ejemplo", s_sim)
check("3a. no hay spec canónica", m3.current("skill:dominio_ejemplo:spec") is None)

print("\n[4] BUG-A: firma humana inválida (forjada) NO ratifica")
m4 = mem()
try:
    P.sellar_ratificacion_humana(m4, "pedido", "ejemplo", SPEC_CANONICA, firma="0" * 64)
    check("4a. firma forjada se rechaza", False, "NO rechazó")
except P.ProcedenciaInvalida:
    check("4a. firma forjada se rechaza", True)

print("\n[5] BUG-E: firma humana válida liga (did, pedido, spec) y registra canónica")
m5 = mem()
firma_ok = _firma("ejemplo", "pedido", SPEC_CANONICA)
s_hum = P.sellar_ratificacion_humana(m5, "pedido", "ejemplo", SPEC_CANONICA, firma_ok)
P.registrar_spec_sellada(m5, "ejemplo", s_hum)
check("5a. spec canónica registrada", m5.current("skill:ejemplo:spec") is not None)
# la misma firma NO sirve para otro did (BUG-E: el did va firmado)
m5b = mem()
firma_ok = _firma("ejemplo", "pedido", SPEC_CANONICA)
s_hum2 = P.sellar_ratificacion_humana(m5b, "pedido", "ejemplo", SPEC_CANONICA, firma_ok)
try:
    P.registrar_spec_sellada(m5b, "OTRO_ID", s_hum2)
    check("5b. firma no re-verificable en otro did se rechaza", False, "NO rechazó")
except P.ProcedenciaInvalida:
    check("5b. firma no re-verificable en otro did se rechaza", True)

print("\n[6] BUG-B: spec sin fórmulas => SinCandidata (antes de evento)")
m6 = mem()
spec_sin = dict(SPEC, formulas=[])
try:
    P.sellar_spec_llm(m6, "pedido", spec_sin)
    check("6a. SinCandidata", False, "NO lanzó")
except P.SinCandidata:
    check("6a. SinCandidata", True)
check("6b. no se escribió evento", m6.keys("evento:") == [])

print("\n[7] BUG-D: hash estricto rechaza NaN")
import math
try:
    P.normalizar({"x": float("nan")})
    check("7a. NaN rechazado", False, "NO rechazó")
except (ValueError, TypeError):
    check("7a. NaN rechazado", True)

print("\n[8] entregable: generar_pdf rechaza dict crudo")
m8 = mem()
try:
    E.generar_pdf({"pedido": "crudo"}, "/tmp/x.tex")
    check("8a. rechaza", False, "NO rechazó")
except E.EntregableInvalido:
    check("8a. rechaza", True)

print("\n[9] entregable: topología AUSENTE => formula_suelta + advertencia")
m9 = mem()
ent = E.crear_entregable(m9, "memoria usb", topologia_estados=[T.AUSENTE], numeros=[])
check("9a. grado formula_suelta", ent.grado == "formula_suelta", ent.grado)
check("9b. advertencia presente", any("topología" in f for f in ent.faltantes))

print("\n[10] entregable: número sin node_id no se imprime")
m10 = mem()
numeros = [
    {"clave": "con_node", "valor": 1.0, "unidad": "u", "node_id": 123},
    {"clave": "sin_node", "valor": 2.0, "unidad": "u", "node_id": None},
]
ent10 = E.crear_entregable(m10, "x", topologia_estados=[T.DECLARADA], numeros=numeros)
check("10a. solo el número con node_id se conserva", len(ent10.numeros) == 1 and
      ent10.numeros[0]["clave"] == "con_node", str(ent10.numeros))

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
