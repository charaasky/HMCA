#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_adversarial_no_escalada.py — autor=modelo NUNCA llega a grado >= 2 sin firma.

P0 de Fable (fable_revision_integral, punto 6): el invariante central del claim defendible.

Por TODO punto de entrada, un contenido de autoría del modelo (sin firma humana) no puede
alcanzar 'conectado' (grado 2). Verifica:
  [1] grado_general: grafo sin firma -> derivado_bajo_supuesto (1), nunca conectado.
  [2] grado_general_manifiesto: firma inválida -> <= derivado_bajo_supuesto.
  [3] procedencia: canal llm_runtime -> techo UNVERIFIED (nunca canónico).
  [4] entregable: sin especificación sellada por humano -> procedencia lo topa en formula_suelta.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import grado_general as GG  # noqa: E402
import manifiesto as MT  # noqa: E402
import topologia as T  # noqa: E402
import procedencia as P  # noqa: E402
import entregable as E  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402

os.environ["HCMA_HUMANO_HMAC_KEY"] = "clave-adversarial"

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


def _grafo():
    return {
        "bloques": [
            {"id": "bat", "tipo": "fuente", "puertos": [{"id": "p", "dominio": "potencia"}, {"id": "n", "dominio": "tierra"}]},
            {"id": "reg", "tipo": "reg", "puertos": [{"id": "in", "dominio": "potencia"}, {"id": "gnd", "dominio": "tierra"}]},
        ],
        "nets": [
            {"id": "v", "conexiones": [{"bloque": "bat", "puerto": "p"}, {"bloque": "reg", "puerto": "in"}]},
            {"id": "g", "conexiones": [{"bloque": "bat", "puerto": "n"}, {"bloque": "reg", "puerto": "gnd"}]},
        ],
        "bindings": [],
    }


print("[1] grado_general: grafo sin firma -> nunca conectado")
r = GG.grado_general(_grafo())
check("1a. derivado_bajo_supuesto (1), no >=2", r["grado"] == "derivado_bajo_supuesto", r["grado"])

print("\n[2] grado_general_manifiesto: firma inválida -> nunca >=2")
man = MT.construir_manifiesto("dom", "pedido", {"formulas": ["x"]}, grafo=_grafo(), nivel_max="conectado")
r2 = GG.grado_general_manifiesto(man, "0" * 64)
check("2a. <= derivado_bajo_supuesto", r2["grado"] in ("derivado_bajo_supuesto", "formula_suelta"), r2["grado"])

print("\n[3] procedencia: canal llm_runtime -> techo UNVERIFIED")
m3 = HCMAMemoriaSQLite(":memory:")
spec = {"nombre": "x", "autor": "modelo", "inputs": [], "formulas": ["y=2x"]}
s = P.sellar_spec_llm(m3, "pedido", spec)
check("3a. canal llm_runtime", s.canal == "llm_runtime")
# registrar no produce spec canónica
P.registrar_spec_sellada(m3, "dominio_x", s)
check("3b. no hay spec canónica (unverified)", m3.current("skill:dominio_x:spec") is None)

print("\n[4] entregable: sin spec humana -> procedencia topa en formula_suelta")
m4 = HCMAMemoriaSQLite(":memory:")
# topología DECLARADA pero SIN especificación humana sellada
ent = E.crear_entregable(m4, "x", especificacion=None, topologia_estados=[T.DECLARADA], numeros=[])
check("4a. formula_suelta (procedencia sin humano)", ent.grado == "formula_suelta", ent.grado)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
