#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""grado_general.py — integrates all the steps into a single degree verdict.

Single entry point that walks the complete path:

  formula_suelta (0) → derivado_bajo_supuesto (1) → conectado (2) → funcional (3)
  → realizable (4) → fabricable (5)

Given a block graph + its data (human signature, mechanism models, operating points
and BOM), returns the highest REAL degree reached and the list of what is missing to go up.

Promotion rules (no shortcuts):
  - no valid graph              -> formula_suelta (0)
  - ASSUMED graph (no signature)     -> derivado_bajo_supuesto (1)
  - DECLARED graph (signature)       -> conectado (2)
  - + functional (mechanism covers) -> funcional (3)
  - + realizable (BOM + ratings)  -> realizable (4)
  - fabricable (5) requires PHYSICAL EVIDENCE (layout/assembly/measurement/regulation): it is NOT
    reachable by software; this module leaves it explicitly out and declares it.

It does NOT touch the core.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import topologia as T  # noqa: E402
import topologia_bloques as TB  # noqa: E402
import ratificacion_topo as RT  # noqa: E402
import mecanismo_funcional as MF  # noqa: E402
import realizable as RZ  # noqa: E402
import manifiesto as MT  # noqa: E402


_ORDEN = {
    "formula_suelta": 0,
    "derivado_bajo_supuesto": 1,
    "conectado": 2,
    "funcional": 3,
    "realizable": 4,
    "fabricable": 5,
}

# Unique canonical order: all modules (entregable, sin_dominio) import it from here,
# so that there are not two diverging degree algebras.
ORDEN_GRADO = _ORDEN


def grado_general(grafo, supuestos=None, firma=None, modelos=None,
                  puntos_operacion=None, bom=None, margen=None):
    """Returns {grado, escalon, falta_para_subir}. Maximum real degree reached."""
    falta = []

    # 0 -> 1: valid graph
    ok_estructural, defectos = TB.validar_grafo(grafo)
    if not ok_estructural:
        return {"grado": "formula_suelta", "escalon": 0, "falta_para_subir": defectos}

    # signature -> DECLARED (connected) or ASSUMED (derived_under_assumption)
    estado_topo = T.ASUMIDA
    if firma is not None and RT.verificar_ratificacion(grafo, supuestos, firma):
        estado_topo = T.DECLARADA

    if estado_topo == T.ASUMIDA:
        return {"grado": "derivado_bajo_supuesto", "escalon": 1,
                "falta_para_subir": ["firma humana para pasar a 'conectado'"]}

    # connected (2). To go up to functional, mechanism models are needed.
    if not modelos:
        return {"grado": "conectado", "escalon": 2,
                "falta_para_subir": ["modelos de mecanismo por bloque (para 'funcional')"]}

    # functional (3)
    vf = MF.verificar_funcional(grafo, modelos)
    if not vf["funcional"]:
        return {"grado": "conectado", "escalon": 2, "falta_para_subir": vf["defectos"]}

    # realizable (4)
    if not puntos_operacion or not bom:
        return {"grado": "funcional", "escalon": 3,
                "falta_para_subir": ["puntos de operación y BOM con ratings (para 'realizable')"]}

    vr = RZ.verificar_realizable(grafo, puntos_operacion, bom, margen)
    if not vr["realizable"]:
        return {"grado": "funcional", "escalon": 3, "falta_para_subir": vr["defectos"]}

    # realizable reached. fabricable requires physical evidence.
    return {"grado": "realizable", "escalon": 4,
            "falta_para_subir": [
                "fabricable exige EVIDENCIA FÍSICA: layout, montaje, calibración, "
                "medición del prototipo y cumplimiento normativo — no es software"]}


def grado_general_manifiesto(manifiesto, firma):
    """REAL degree from a SIGNED MANIFEST (covers spec+graph+mechanisms+bom).

    It is the CORRECT path for degrees >= 2: the signature endorses the COMPLETE manifest, so that
    editing any of its pieces invalidates the signature and degrades. The partial graph signature
    (grado_general with `firma`) is only for test compatibility, NOT for the real system.
    """
    if not MT.verificar_manifiesto(manifiesto, firma):
        grafo = manifiesto.get("grafo")
        if isinstance(grafo, dict) and grafo.get("bloques"):
            return {"grado": "derivado_bajo_supuesto", "escalon": 1,
                    "falta_para_subir": ["firma de manifiesto inválida o ausente"]}
        return {"grado": "formula_suelta", "escalon": 0,
                "falta_para_subir": ["grafo inválido o sin firma de manifiesto"]}

    grafo = manifiesto.get("grafo") or {}
    modelos = manifiesto.get("mecanismos")
    bom = manifiesto.get("bom")
    puntos_operacion = manifiesto.get("puntos_operacion")
    nivel_max = manifiesto.get("nivel_max", "conectado")
    return _recorrer_escalones(grafo, modelos, puntos_operacion, bom, nivel_max)


def _recorrer_escalones(grafo, modelos, puntos_operacion, bom, nivel_max):
    """Walks the steps with upper limit `nivel_max` (what the human authorized)."""
    ok, defectos = TB.validar_grafo(grafo)
    if not ok:
        return {"grado": "formula_suelta", "escalon": 0, "falta_para_subir": defectos}

    tope = ORDEN_GRADO.get(nivel_max, 2)
    if tope < 2:
        return {"grado": "derivado_bajo_supuesto", "escalon": 1,
                "falta_para_subir": [f"nivel máximo autorizado {nivel_max} < conectado"]}

    if modelos:
        vf = MF.verificar_funcional(grafo, modelos)
        if not vf["funcional"]:
            return {"grado": "conectado", "escalon": 2, "falta_para_subir": vf["defectos"]}
    else:
        return {"grado": "conectado", "escalon": 2,
                "falta_para_subir": ["modelos de mecanismo (para 'funcional')"]}

    if tope < 3:
        return {"grado": "conectado", "escalon": 2,
                "falta_para_subir": [f"nivel máximo autorizado {nivel_max} limita a conectado"]}

    if puntos_operacion and bom:
        vr = RZ.verificar_realizable(grafo, puntos_operacion, bom)
        if not vr["realizable"]:
            return {"grado": "funcional", "escalon": 3, "falta_para_subir": vr["defectos"]}
    else:
        return {"grado": "funcional", "escalon": 3,
                "falta_para_subir": ["puntos de operación y BOM (para 'realizable')"]}

    if tope < 4:
        return {"grado": "funcional", "escalon": 3,
                "falta_para_subir": [f"nivel máximo autorizado {nivel_max} limita a funcional"]}

    return {"grado": "realizable", "escalon": 4,
            "falta_para_subir": ["fabricable exige evidencia física — no software"]}


if __name__ == "__main__":
    grafo = {
        "bloques": [
            {"id": "bateria", "tipo": "fuente", "puertos": [{"id": "V+", "dominio": "potencia"}, {"id": "V-", "dominio": "tierra"}]},
            {"id": "reg", "tipo": "regulador", "puertos": [{"id": "in", "dominio": "potencia"}, {"id": "out", "dominio": "potencia"}, {"id": "gnd", "dominio": "tierra"}]},
        ],
        "nets": [
            {"id": "v", "conexiones": [{"bloque": "bateria", "puerto": "V+"}, {"bloque": "reg", "puerto": "in"}]},
            {"id": "g", "conexiones": [{"bloque": "bateria", "puerto": "V-"}, {"bloque": "reg", "puerto": "gnd"}]},
        ],
        "bindings": [],
    }
    import json
    print(json.dumps(grado_general(grafo), ensure_ascii=False, indent=1))
