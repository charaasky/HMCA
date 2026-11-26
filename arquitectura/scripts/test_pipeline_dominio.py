#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_pipeline_dominio.py — test del pipeline genérico de creación de dominio.

Verifica el flujo unificado (candidata -> humano -> impl -> evaluar -> activo) para UN dominio
de ejemplo mínimo (doblar), sin hardcode de dominio: toda spec candidata/canónica/impl se
inyecta. Invariantes:

  [1] Candidata autor=modelo queda UNVERIFIED (no canónica).
  [2] Solo un humano (registrar_spec fuente ingeniero) vuelve canónica la spec.
  [3] La impl se propone SIEMPRE unverified y compila en sandbox.
  [4] evaluar con tests dorados + fuente registrada => ok canónico (grado 'canonico').
  [5] SIN fuente registrada => el ok degrada a 'conforme'.
  [6] Una impl mentirosa NO pasa evaluar; no se activa.
  [7] 'modelo' nunca aparece como fuente autoritativa.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import pipeline_dominio as PD  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402

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


# ── dominio de ejemplo genérico "doble" (sin dominio físico específico) ──────
def _spec_candidata():
    return {
        "nombre": "doble",
        "autor": "modelo",
        "inputs": [{"name": "x", "question": "número", "unit": "1"}],
        "formulas": ["y = 2*x (álgebra, deducible)"],
        "dimensiones": {"y": "1"},
        "ranges": {"y": (-100.0, 100.0)},
        "fuentes_citadas": [],
        "lookup_queries": [],
    }


def _spec_canonica():
    return {
        "nombre": "doble",
        "outputs": [{"nombre": "y", "tolerancia": {"tipo": "abs", "valor": 1e-6}}],
        "tests_dorados": [
            {"id": "cero", "inputs": {"x": 0}, "esperado": {"y": 0},
             "fuente": ["fuente:algebra_doble"]},
            {"id": "tres", "inputs": {"x": 3}, "esperado": {"y": 6},
             "fuente": ["fuente:algebra_doble"]},
        ],
    }


def _impl_buena():
    return "def calcular(inputs):\n    return {'y': inputs['x'] * 2}\n"


def _impl_mentirosa():
    return "def calcular(inputs):\n    return {'y': inputs['x'] * 3}\n"


print("[1] pipeline genérico: candidata -> humano -> impl -> evaluar -> activo")
m = mem()
r = PD.crear_dominio(m, pedido="doblar un número", spec_candidata=_spec_candidata(),
                     spec_canonica=_spec_canonica(), codigo_impl=_impl_buena(),
                     fuente_tests={"algebra_doble": ("tabla_fabricante", "álgebra elemental")})
check("1a. did normalizado", r["did"] == "doble", r["did"])
check("1b. candidata autor=modelo", r["candidata"]["autor"] == "modelo")
check("1c. candidata unverified", m.current(f"dominio:{r['did']}:candidata") is None)
check("1d. spec canónica (humano)", m.current(f"skill:{r['did']}:spec") is not None)
check("1e. evaluar todos_ok", r["pasos"]["evaluar"].get("todos_ok") is True)
check("1f. activo", r["activo"] is True)

print("\n[2] grado 'canonico' con fuente; 'conforme' sin fuente (el oráculo manda)")
check("2a. con fuente => canonico", r["grado"] == "canonico", r["grado"])
m2 = mem()
# spec canónica SIN fuentes en ningún test => ok queda conforme
spec_sin_fuente = {
    "nombre": "doble",
    "outputs": [{"nombre": "y", "tolerancia": {"tipo": "abs", "valor": 1e-6}}],
    "tests_dorados": [
        {"id": "cero", "inputs": {"x": 0}, "esperado": {"y": 0}},
        {"id": "tres", "inputs": {"x": 3}, "esperado": {"y": 6}},
    ],
}
r2 = PD.crear_dominio(m2, pedido="doblar", spec_candidata=_spec_candidata(),
                      spec_canonica=spec_sin_fuente, codigo_impl=_impl_buena(),
                      fuente_tests=None)
check("2b. sin fuente => conforme", r2["grado"] == "conforme", r2["grado"])

print("\n[3] impl mentirosa NO pasa evaluar; no se activa")
m3 = mem()
r3 = PD.crear_dominio(m3, pedido="doblar", spec_candidata=_spec_candidata(),
                      spec_canonica=_spec_canonica(), codigo_impl=_impl_mentirosa(),
                      fuente_tests={"algebra_doble": ("tabla_fabricante", "álgebra")})
check("3a. todos_ok False", r3["pasos"]["evaluar"].get("todos_ok") is False)
check("3b. no se activa", r3["activo"] is False)

print("\n[4] 'modelo' nunca autoritativo")
m4 = mem()
PD.crear_dominio(m4, pedido="doblar", spec_candidata=_spec_candidata(),
                 spec_canonica=_spec_canonica(), codigo_impl=_impl_buena(),
                 fuente_tests={"algebra_doble": ("tabla_fabricante", "álgebra")})
n_modelo = sum(1 for k in m4.keys("") if (f := m4.current(k)) and f["verified"]
               and f["fuente"] == "modelo")
check("4a. cero hechos verified fuente=modelo", n_modelo == 0, f"{n_modelo}")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
