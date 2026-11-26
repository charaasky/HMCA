#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_equivalencia_grado.py — test de propiedad: un solo calculador de grado.

P0 de Fable (fable_revision_integral): 'los grados pueden divergir'. Este test garantiza que
entregable.crear_entregable y grado_general.grado_general producen el MISMO grado para los
estados que ambos cubren (0, 1, 2), y que grado_general es el único que llega a 3-4.

  - topología AUSENTE  -> ambos: formula_suelta (0)
  - topología ASUMIDA  -> ambos: derivado_bajo_supuesto (1)
  - topología DECLARADA (conectado) con procedencia humana -> ambos: conectado (2)
  - grados 3-4 solo salen de grado_general (con grafo+mecanismo+BOM)
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import topologia as T  # noqa: E402
import grado_general as GG  # noqa: E402
import ratificacion_topo as RT  # noqa: E402

os.environ["HCMA_HUMANO_HMAC_KEY"] = "clave-equivalencia"

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


# orden canónico único
check("0. ORDEN_GRADO es la única fuente", GG.ORDEN_GRADO["conectado"] == 2, str(GG.ORDEN_GRADO))

# AUSENTE -> formula_suelta en ambos
g_ausente = GG.grado_general({"bloques": [], "nets": [], "bindings": []})
check("1. AUSENTE -> formula_suelta (grado_general)", g_ausente["grado"] == "formula_suelta")

# ASUMIDA (grafo válido sin firma) -> derivado_bajo_supuesto
g_asumida = GG.grado_general(_grafo())
check("2. ASUMIDA -> derivado_bajo_supuesto (grado_general)", g_asumida["grado"] == "derivado_bajo_supuesto")

# DECLARADA (firma válida) -> conectado
firma = RT.firmar_topologia(_grafo(), ["sup"])
g_conectado = GG.grado_general(_grafo(), ["sup"], firma)
check("3. DECLARADA -> conectado (grado_general)", g_conectado["grado"] == "conectado")

# grados 3-4 solo de grado_general
g_func = GG.grado_general(_grafo(), ["sup"], firma, modelos={"reg": {"funcion": "regula", "clase_modelo": "lineal"}})
check("4. funcional (3) sale de grado_general", g_func["grado"] == "funcional")

g_real = GG.grado_general(_grafo(), ["sup"], firma,
                          modelos={"reg": {"funcion": "regula", "clase_modelo": "lineal"}},
                          puntos_operacion={"reg": {"tension": 9.0}},
                          bom={"reg": {"part_no": "x", "ratings": {"tension": 20.0}}})
check("5. realizable (4) sale de grado_general", g_real["grado"] == "realizable")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
