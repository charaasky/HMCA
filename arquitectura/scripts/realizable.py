#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""realizable.py — 'realizable' rung (grade 4): BOM + ratings + tolerances.

Design from Fable 5.2 (fable_fabricable):

  `realizable` = each block has a real part whose RATINGS wrap the operating
  point, the tolerance budget closes, and the BOM is complete.

The idea: 'conectado' says it connects; 'funcional' says it does what it claims; 'realizable'
says a physical part EXISTS that can be mounted without exceeding its limits.

Each block with its operating point declares which magnitudes it supports (voltage, current,
power, etc.) and with what safety margin. The BOM lists real parts (with their manufacturer
ratings). The verifier checks:

  1. EVERY block that requires a part has ONE in the BOM (complete BOM).
  2. the part's rating WRAPS the block's operating point (rating >= operation ×
     safety factor / derating).
  3. the tolerance budget closes: the worst combination of part tolerances
     does not take the device out of the claimed operating point.

Does NOT touch the core. Reuses topologia_bloques (graph) and produces a verdict.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


class RealizableInvalido(ValueError):
    pass


def verificar_realizable(grafo, puntos_operacion, bom, margen=None):
    """Verifies that the graph is 'realizable' given operating points and a BOM.

    Args:
      grafo: block graph (from topologia_bloques).
      puntos_operacion: {bloque_id: {magnitud: valor}} with the magnitudes that block
                        supports (e.g. {'tension': 9.0} in volts). POSITIVE values = worst case.
      bom: {bloque_id: {part_no, ratings: {magnitud: valor_rating}, tolerancia: {magnitud: pct}}}
           ratings are the manufacturer's MAXIMUM limits.
      margen: {magnitud: factor} optional (e.g. derating). Default with no extra margin.

    Returns {bloques: [...], realizable: bool, defectos: [str]}.
    """
    margen = margen or {}
    defectos = []
    veredictos = []

    ids_grafo = {b.get("id") for b in (grafo.get("bloques", []) or []) if isinstance(b, dict)}

    # 1. Complete BOM: every block with an operating point must have a part
    for bid, oper in (puntos_operacion or {}).items():
        if bid not in ids_grafo:
            defectos.append(f"punto de operación para bloque inexistente: {bid}")
            continue
        if bid not in bom:
            defectos.append(f"bloque {bid}: sin pieza en el BOM")
            veredictos.append({"bloque": bid, "ok": False, "motivo": "sin pieza"})
            continue
        pieza = bom[bid]
        ratings = pieza.get("ratings", {})
        tolerancias = pieza.get("tolerancia", {})
        ok_bloque = True
        motivos = []
        for mag, valor in oper.items():
            rating = ratings.get(mag)
            if rating is None:
                defectos.append(f"bloque {bid}: pieza sin rating para '{mag}'")
                ok_bloque = False
                motivos.append(f"sin rating de {mag}")
                continue
            factor = margen.get(mag, 1.0)
            # derating: the operation must fit within the rating after discounting the margin
            limite = rating / float(factor) if factor else rating
            if float(valor) > limite:
                defectos.append(
                    f"bloque {bid}: {mag}={valor} excede rating {rating}"
                    f" (con margen {factor} o derating)")
                ok_bloque = False
                motivos.append(f"{mag} excede rating")
            # tolerance: worst-case of the rating must not drop below the operating point
            tol = tolerancias.get(mag)
            if tol is not None:
                # rating_peor = rating * (1 - tol); must still cover valor
                rating_peor = rating * (1.0 - float(tol))
                if float(valor) > rating_peor:
                    defectos.append(
                        f"bloque {bid}: {mag}={valor} no cubierto en peor caso de tolerancia"
                        f" ({tol*100:.1f}% -> rating {rating_peor:.3f})")
                    ok_bloque = False
                    motivos.append(f"tolerancia de {mag} no cierra")
        veredictos.append({"bloque": bid, "ok": ok_bloque, "motivo": "; ".join(motivos) or "ok"})

    realizable = len(puntos_operacion or {}) > 0 and not defectos
    return {"bloques": veredictos, "realizable": realizable, "defectos": defectos}


if __name__ == "__main__":
    grafo = {
        "bloques": [
            {"id": "bateria", "tipo": "fuente", "puertos": []},
            {"id": "reg", "tipo": "regulador", "puertos": []},
        ],
        "nets": [],
        "bindings": [],
    }
    puntos = {"reg": {"tension": 9.0, "corriente": 0.05}}
    bom = {
        "reg": {"part_no": "L78L05", "ratings": {"tension": 20.0, "corriente": 0.1},
                "tolerancia": {"tension": 0.05}},
    }
    import json
    print("realizable (ok):", json.dumps(verificar_realizable(grafo, puntos, bom), ensure_ascii=False, indent=1))

    # reg that exceeds current rating
    bom_mal = {
        "reg": {"part_no": "L78L05", "ratings": {"tension": 20.0, "corriente": 0.01}},
    }
    print("realizable (excede):", json.dumps(verificar_realizable(grafo, puntos, bom_mal), ensure_ascii=False, indent=1))
