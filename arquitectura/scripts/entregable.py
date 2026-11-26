#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""entregable.py — DELIVERABLE factory with combined degree (topology + provenance).

Fable 5.2 design (fable_recurrencia_hardcode). Fixes the P0 of "unsealed egress":

  BEFORE: any script could call the PDF library with a raw dict of numbers, without
  going through the topology gate or the lineage. Result: a PDF of "loose formulas" that
  looked like a deliverable.

  NOW: `generar_pdf` ONLY accepts an `Entregable` (object sealed by the factory). The degree
  final is the MINIMUM between topology and provenance. A number is only printed if it has `node_id`
  (the version of the fact in the HCMA that backs it). No `node_id`, no printing.

Lists the degrees in increasing order, coherent with topologia._ORDEN_GRADO:
  formula_suelta (0) < derivado_bajo_supuesto (1) < conectado (2) < funcional (3)
  < realizable (4) < fabricable (5)

The factory `crear_entregable` is the ONLY one that can build a valid `Entregable` (it uses a
private seal). `generar_pdf(entregable, salida)` rejects with TypeError anything that is not
an `Entregable`.
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
from grado_general import ORDEN_GRADO  # noqa: E402  (single canonical order)

# The degree order is no longer duplicated here: it is ORDEN_GRADO from grado_general.py, the only
# source of truth. `entregable` only expresses up to 'conectado' (degree 2); degrees 3-4
# are computed by `grado_general` from the graph+mechanism+BOM.
_ORDEN_GRADO = ORDEN_GRADO

_SELLO = object()


class EntregableInvalido(TypeError):
    pass


class Entregable:
    """Sealed deliverable: the ONLY object that `generar_pdf` accepts."""

    __slots__ = ("pedido", "grado", "faltantes", "numeros", "gate", "_sello")

    def __init__(self, pedido, grado, faltantes, numeros, gate, _sello):
        if _sello is not _SELLO:
            raise EntregableInvalido("Entregable solo puede construirse por crear_entregable()")
        self.pedido = pedido
        self.grado = grado
        self.faltantes = list(faltantes)
        # numeros: list of {clave, valor, unidad, node_id}. Without node_id it is not printed.
        self.numeros = [n for n in numeros if n.get("node_id") is not None]
        self.gate = dict(gate or {})
        self._sello = _sello


def crear_entregable(m, pedido, especificacion=None, topologia_estados=None,
                     numeros=None, gate=None, clase_pedida="fabricable"):
    """Builds a sealed deliverable with degree = min(topology, provenance).

    - `topologia_estados`: iterable of states (AUSENTE/ASUMIDA/DECLARADA); if None, it is treated
      as AUSENTE (no declared topology => formula_suelta).
    - `numeros`: list of {clave, valor, unidad, node_id}. Each number requires node_id (version
      of the fact in HCMA). Those without it are discarded.
    - Returns the Entregable. If `clase_pedida` is stronger than the real degree, `degradar_afirmacion`
      already degrades it; here we also reflect it in `gate`.
    """
    # ── degree by topology ──
    estados = list(topologia_estados) if topologia_estados is not None else [T.AUSENTE]
    if not estados:
        estados = [T.AUSENTE]
    grado_topo, faltantes_topo = T.grado_entregable(estados)

    # ── degree by provenance ──
    # If the spec did not come sealed via a human channel, the provenance cannot exceed
    # 'formula_suelta' (the weakest): there is no authority backing more than loose numbers.
    from procedencia import SpecSellada
    procedencia_ok = especificacion is not None and isinstance(especificacion, SpecSellada) \
        and especificacion.canal == "humano"
    faltantes_proc = [] if procedencia_ok else ["procedencia no verificada por humano (sin canal humano sellado)"]

    # final degree = minimum
    orden_topo = _ORDEN_GRADO[grado_topo]
    orden_proc = _ORDEN_GRADO["fabricable" if procedencia_ok else "formula_suelta"]
    if orden_topo <= orden_proc:
        grado_final = grado_topo
    else:
        grado_final = "formula_suelta"

    faltantes = faltantes_topo + faltantes_proc

    # gate: which assertion is lawful given the real degree
    permitido, motivo = T.afirmacion_permitida(grado_final, clase_pedida)
    gate_out = dict(gate or {})
    gate_out["grado_real"] = grado_final
    gate_out["clase_pedida"] = clase_pedida
    gate_out["afirmacion_permitida"] = permitido
    gate_out["motivo"] = motivo

    return Entregable(pedido, grado_final, faltantes, numeros or [], gate_out, _SELLO)


def generar_pdf(entregable, salida):
    """Renders ONLY an Entregable (rejects a raw dict). Returns the PDF path or None."""
    if not isinstance(entregable, Entregable):
        raise EntregableInvalido(
            "generar_pdf() solo acepta un Entregable (de crear_entregable()); "
            "un dict crudo no pasa (anti-hardcode / sin linaje)")
    # reuse the sin_dominio backend for the LaTeX, but with the numbers already filtered by node_id
    import sin_dominio as SD
    # build the payload in the form SD.generar_pdf expects, with degree and missing items visible
    r = {
        "pedido": entregable.pedido,
        "candidata": {},  # no raw candidate: we do not want to dump hardcoded specs
        "fuentes": [],
        "gate": {
            "es_canonico": entregable.gate.get("afirmacion_permitida", False),
            "invariante_ok": True,
            "sin_fuente_humana": True,
        },
        "calculos": {n["clave"]: n["valor"] for n in entregable.numeros},
        "frontera_no_derivable": entregable.faltantes,
    }
    salida = os.path.abspath(salida)
    os.makedirs(os.path.dirname(salida), exist_ok=True)
    return SD.generar_pdf(r, salida)


if __name__ == "__main__":
    from memoria_sqlite import HCMAMemoriaSQLite
    m = HCMAMemoriaSQLite(":memory:")
    # AUSENTE topology => degree formula_suelta, and a raw dict does NOT pass to generar_pdf
    e = crear_entregable(m, "memoria usb", topologia_estados=[T.AUSENTE], numeros=[])
    print("grado:", e.grado, "| faltantes:", e.faltantes)
    try:
        generar_pdf({"pedido": "crudo"}, "/tmp/x.tex")
    except EntregableInvalido as err:
        print("OK bloqueado dict crudo:", err)
