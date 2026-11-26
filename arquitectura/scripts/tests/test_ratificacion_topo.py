#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_ratificacion_topo.py — test de la hoja de ratificación humana.

Invariantes del escalón P1 (firma para pasar de ASUMIDA a DECLARADA):

  [1] firmar_topologia produce una firma verificable (grado conectado).
  [2] editar el grafo tras firmar invalida la firma (degrade).
  [3] firma inválida/formato incorrecto -> ASUMIDA (no DECLARADA).
  [4] la hoja de ratificación es determinista y lista bloques/nets/bindings/supuestos.
  [5] grafo que no valida estructuralmente -> AUSENTE (aunque haya firma).
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import ratificacion_topo as R  # noqa: E402
import topologia as T  # noqa: E402

os.environ["HCMA_HUMANO_HMAC_KEY"] = "clave-de-prueba-ratificacion"

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
            {"id": "bat", "tipo": "fuente", "puertos": [{"id": "V+", "dominio": "potencia"}, {"id": "V-", "dominio": "tierra"}]},
            {"id": "adc", "tipo": "adc", "puertos": [{"id": "V+", "dominio": "potencia"}, {"id": "V-", "dominio": "tierra"}]},
        ],
        "nets": [
            {"id": "vbus", "conexiones": [{"bloque": "bat", "puerto": "V+"}, {"bloque": "adc", "puerto": "V+"}]},
            {"id": "gnd", "conexiones": [{"bloque": "bat", "puerto": "V-"}, {"bloque": "adc", "puerto": "V-"}]},
        ],
        "bindings": [],
    }


_SUP = ["supuesto 1", "supuesto 2"]


print("[1] firma válida -> grado conectado")
grafo = _grafo()
firma = R.firmar_topologia(grafo, _SUP)
check("1a. firma verificable", R.verificar_ratificacion(grafo, _SUP, firma))
grado, falt = R.grado_ratificado(grafo, _SUP, firma)
check("1b. grado conectado", grado == "conectado", grado)

print("\n[2] editar el grafo tras firmar invalida")
grafo_edit = _grafo()
grafo_edit["bloques"].append({"id": "extra", "tipo": "x", "puertos": []})
check("2a. firma inválida tras editar", not R.verificar_ratificacion(grafo_edit, _SUP, firma))
grado2, _ = R.grado_ratificado(grafo_edit, _SUP, firma)
check("2b. grado no conectado", grado2 != "conectado", grado2)

print("\n[3] firma inválida -> ASUMIDA")
estado, _ = R.topologia_ratificada(grafo, _SUP, "0" * 64)
check("3a. ASUMIDA (no DECLARADA)", estado == T.ASUMIDA, estado)

print("\n[4] hoja de ratificación determinista y completa")
h1 = R.hoja_de_ratificacion(grafo, _SUP)
h2 = R.hoja_de_ratificacion(grafo, _SUP)
check("4a. determinista", h1 == h2)
check("4b. lista bloques", "bat" in h1 and "adc" in h1)
check("4c. lista supuestos", "supuesto 1" in h1)

print("\n[5] grafo estructuralmente inválido -> AUSENTE aunque haya firma")
grafo_mal = _grafo()
grafo_mal["bloques"] = []  # sin bloques
estado5, _ = R.topologia_ratificada(grafo_mal, _SUP, firma)
check("5a. AUSENTE", estado5 == T.AUSENTE, estado5)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
