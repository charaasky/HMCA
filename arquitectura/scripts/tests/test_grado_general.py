#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_grado_general.py — test del integrador de escalones.

Verifica que grado_general devuelve el escalón correcto según qué datos se proveen, SIN
saltarse niveles:
  [1] grafo inválido -> formula_suelta (0)
  [2] grafo válido sin firma -> derivado_bajo_supuesto (1)
  [3] grafo firmado (firma válida) -> conectado (2)
  [4] + modelos de mecanismo cubiertos -> funcional (3)
  [5] + BOM/ratings -> realizable (4)
  [6] fabricable (5) nunca se alcanza por software (lo declara)
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import grado_general as GG  # noqa: E402
import ratificacion_topo as RT  # noqa: E402

os.environ["HCMA_HUMANO_HMAC_KEY"] = "clave-grado-general"

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


def _grafo_valido():
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


_SUP = ["supuesto"]


print("[1] grafo inválido -> formula_suelta")
check("1a.", GG.grado_general({"bloques": [], "nets": [], "bindings": []})["grado"] == "formula_suelta")

print("\n[2] grafo válido sin firma -> derivado_bajo_supuesto")
g = _grafo_valido()
check("2a.", GG.grado_general(g)["grado"] == "derivado_bajo_supuesto")

print("\n[3] grafo firmado -> conectado")
firma = RT.firmar_topologia(g, _SUP)
check("3a.", GG.grado_general(g, _SUP, firma)["grado"] == "conectado")

print("\n[4] + mecanismo cubierto -> funcional")
modelos = {"reg": {"funcion": "regula", "clase_modelo": "lineal"}}
check("4a.", GG.grado_general(g, _SUP, firma, modelos=modelos)["grado"] == "funcional")

print("\n[5] + BOM/ratings -> realizable")
puntos = {"reg": {"tension": 9.0}}
bom = {"reg": {"part_no": "x", "ratings": {"tension": 20.0}}}
check("5a.", GG.grado_general(g, _SUP, firma, modelos=modelos,
      puntos_operacion=puntos, bom=bom)["grado"] == "realizable")

print("\n[6] fabricable (5) declarado como NO software")
r6 = GG.grado_general(g, _SUP, firma, modelos=modelos, puntos_operacion=puntos, bom=bom)
check("6a. tope es realizable (4), no fabricable", r6["grado"] == "realizable")
check("6b. declara evidencia física", any(k in " ".join(r6["falta_para_subir"]).lower()
      for k in ("evidencia", "fisica", "física", "layout", "medicion", "medición")),
      str(r6["falta_para_subir"]))

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
