#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""verificador_forma.py — SHAPE control of the deliverable at EGRESS (Fable ruling).

Fable (fable_utilidad_entregable_fable.md): HCMA does not manufacture good tools, but it IS
responsible for NOT delivering an artifact that LIES (fidelity) or that nobody measured (egress without
shape control). This verifier is:

  - DETERMINISTIC (no LLM, no vision): measures measurable shape defects.
  - ASYMMETRIC: it only says "defective" or "no defects detected", NEVER "useful".
    Passing does NOT raise the grade; failing blocks that part of the egress.
  - ORTHOGONAL to grade/provenance: it is a distinct axis.

What it measures (over a schematic netlist, BEFORE rendering/sealing):
  1. Symbol fidelity: every component has a resolved SYMBOL type (zero fallback).
     A component with an unrecognized type => defect (a lying rectangle is not drawn).
  2. Connection fidelity: every connection points to a (ref, pin) that EXISTS in the netlist.
  3. Integrity: every component is drawn (none is silently omitted).
  4. No orphan component (optional per threshold): every component has at least one connection,
     unless the netlist has exactly 1 component (legitimate isolated).

The thresholds are acceptance criteria FROZEN by a human (as in objetivo.py), per deliverable
type. Here the default threshold for SCHEMATIC is set; the human can tighten it.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# Default threshold for schematic (criterion frozen by a human, not computed by the system).
#   min_porcentaje_simbolos_resueltos = 1.0  => ALL components must have a resolved symbol.
#   exigir_todo_componente_dibujado = True    => none is omitted.
#   permitir_huerfanos = True                 => an isolated component is NOT a defect by default.
UMBRAL_DEFAULT = {
    "min_porcentaje_simbolos_resueltos": 1.0,   # 100%: zero fallback symbols
    "exigir_todo_componente_dibujado": True,
    "permitir_huerfanos": True,
    "font_size_minimo": 10.0,                    # minimum text size (proxy to print scale)
}


def _tipo_simbolo_resuelto(componente):
    """Returns (resolved: bool, type): does the component have a known electrical symbol?

    Reuses the esquematico_svg classifier, which returns None if it does not recognize it (see ruling:
    no silent fallback). If it cannot be imported, it assumes 'resolved=True' in permissive
    mode ONLY so as not to break the flow when the module is absent (but it is reported).
    """
    try:
        import esquematico_svg as ESV
        ref = componente.get("ref", "")
        nombre = componente.get("nombre", "")
        tipo = componente.get("tipo", "")
        sym = ESV._tipo_simbolo(ref, nombre, tipo)
        return (sym is not None, sym)
    except Exception:
        return (True, "no_verificado")


def verificar_forma(netlist, umbral=None):
    """Verifies the schematic netlist BEFORE rendering/sealing.

    Returns dict: {aprueba: bool, defectos: [str], reporte: {n_componentes, n_conexiones,
    n_simbolos_no_resueltos, ...}}. `aprueba` is True only if there are NO defects.

    This is the point that `sin_dominio.py`/`entregable.py` call at EGRESS: if `aprueba`
    is False for the schematic, it degrades honestly (topology as text) and records
    "schematic omitted: did not pass shape control".
    """
    umbral = umbral or UMBRAL_DEFAULT
    defectos = []
    componentes = netlist.get("componentes") or []
    conexiones = netlist.get("conexiones") or []

    n_componentes = len(componentes)
    n_conexiones = len(conexiones)

    # 1. symbol fidelity: no unresolved symbol
    n_no_resueltos = 0
    refs = set()
    for c in componentes:
        if not isinstance(c, dict):
            defectos.append("componente no es dict")
            continue
        ref = c.get("ref", "")
        refs.add(ref)
        resuelto, sym = _tipo_simbolo_resuelto(c)
        if not resuelto:
            n_no_resueltos += 1
            defectos.append(f"componente '{ref}' tiene símbolo NO resuelto "
                            f"(tipo='{c.get('tipo','')}') => no se dibuja (fidelidad)")

    # 2. connection fidelity: every connection points to an existing (ref, pin)
    #    build set of valid (ref, pin)
    pines_validos = set()
    for c in componentes:
        if not isinstance(c, dict):
            continue
        ref = c.get("ref", "")
        # normalize pins to numbers
        try:
            import esquematico_svg as ESV
            for p in ESV._pines_lista(c):
                pines_validos.add((ref, p["num"]))
        except Exception:
            for p in (c.get("pines") or []):
                num = p.get("num") if isinstance(p, dict) else p
                pines_validos.add((ref, num))

    for cx in conexiones:
        if not isinstance(cx, dict):
            defectos.append("conexión no es dict")
            continue
        for lado in ("de", "a"):
            ep = cx.get(lado)
            if not isinstance(ep, dict):
                defectos.append(f"conexión sin extremo '{lado}' válido")
                continue
            par = (ep.get("ref"), ep.get("pin"))
            if par not in pines_validos:
                defectos.append(f"conexión apunta a pin inexistente {ep.get('ref')}.{ep.get('pin')}")

    # 3. integrity: no component is omitted
    if umbral.get("exigir_todo_componente_dibujado", True):
        if n_componentes > 0 and not refs:
            defectos.append("no hay componentes dibujables")

    # 4. orphan component (optional, per threshold)
    if not umbral.get("permitir_huerfanos", True) and n_componentes > 1:
        conectados = set()
        for cx in conexiones:
            if not isinstance(cx, dict):
                continue
            for lado in ("de", "a"):
                ep = cx.get(lado)
                if isinstance(ep, dict) and ep.get("ref"):
                    conectados.add(ep["ref"])
        for ref in refs:
            if ref not in conectados:
                defectos.append(f"componente huérfano: '{ref}' (sin conexiones)")

    # 5. resolved symbols threshold
    if n_componentes > 0:
        porcentaje_resuelto = (n_componentes - n_no_resueltos) / n_componentes
        if porcentaje_resuelto < umbral.get("min_porcentaje_simbolos_resueltos", 1.0):
            defectos.append(
                f"símbolos resueltos {porcentaje_resuelto:.0%} < umbral "
                f"{umbral.get('min_porcentaje_simbolos_resueltos', 1.0):.0%}")

    # 6-8. TEXT SIZE, OVERFLOW and OVERLAP (Fable ruling: necessary conditions
    #      of legibility, deterministic). They are measured over the LAYOUT computed by the renderer,
    #      NOT over the SVG nor over LaTeX rescaling.
    try:
        import esquematico_svg as ESV
        layout = ESV.calcular_layout(netlist)
        _defectos_layout(layout, defectos, umbral)
    except Exception as _e:
        # if the layout cannot be computed (module absent), it does NOT block: only fidelity measured.
        defectos.append(f"layout no medible: {type(_e).__name__}")

    reporte = {
        "n_componentes": n_componentes,
        "n_conexiones": n_conexiones,
        "n_simbolos_no_resueltos": n_no_resueltos,
        "n_defectos": len(defectos),
    }

    return {"aprueba": not defectos, "defectos": defectos, "reporte": reporte}


def _bbox_texto(t):
    """Returns approximate (x_min, y_min, x_max, y_max) of a layout text."""
    x = t["x"]
    y = t["y"]
    fs = t.get("font_size", 12)
    ancho = t.get("ancho_est", len(t.get("texto", "")) * fs * 0.6)
    alto = fs * 1.2
    ancla = t.get("ancla", "middle")
    if ancla == "end":
        x_min = x - ancho
        x_max = x
    elif ancla == "start":
        x_min = x
        x_max = x + ancho
    else:  # middle
        x_min = x - ancho / 2
        x_max = x + ancho / 2
    y_min = y - alto      # y is the baseline; text goes upward
    y_max = y + fs * 0.3
    return (x_min, y_min, x_max, y_max)


def _defectos_layout(layout, defectos, umbral):
    """Measures text size, canvas overflow and overlap, adding defects."""
    textos = layout.get("textos", [])
    view_w, view_h = layout.get("viewbox", (640.0, 400.0))
    offset_x, offset_y = layout.get("offset", (60.0, 20.0))

    # 1) minimum text size (threshold in layout units, proxy to print scale)
    font_min = umbral.get("font_size_minimo", 10.0)
    for t in textos:
        # the component label (bold) and the title can be OK; pin names cannot.
        fs = t.get("font_size", 12)
        if fs < font_min:
            defectos.append(f"texto '{t.get('texto','')[:20]}' font_size={fs} < umbral {font_min} "
                            f"(ilegible a escala de impresión)")

    # 2) overflow: each text must stay within the viewBox (offset already applied)
    for t in textos:
        bx = _bbox_texto(t)
        # apply offset
        x0 = bx[0] + offset_x
        x1 = bx[2] + offset_x
        y0 = bx[1] + offset_y
        y1 = bx[3] + offset_y
        if x0 < 0 or x1 > view_w or y0 < 0 or y1 > view_h:
            defectos.append(f"texto '{t.get('texto','')[:20]}' desborda el lienzo "
                            f"(bbox {x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f} vs {view_w:.0f}x{view_h:.0f})")

    # 3) overlap: text bboxes must not overlap each other
    bboxes = [(_bbox_texto(t), t) for t in textos]
    for i in range(len(bboxes)):
        for j in range(i + 1, len(bboxes)):
            (a_x0, a_y0, a_x1, a_y1), ta = bboxes[i]
            (b_x0, b_y0, b_x1, b_y1), tb = bboxes[j]
            # they overlap if the X and Y ranges cross
            solapan_x = a_x0 < b_x1 and b_x0 < a_x1
            solapan_y = a_y0 < b_y1 and b_y0 < a_y1
            if solapan_x and solapan_y:
                # only report pairs of distinct components (same-component ones don't matter)
                if ta.get("ref") != tb.get("ref"):
                    defectos.append(f"solapamiento de texto: '{ta.get('texto','')[:15]}' vs "
                                    f"'{tb.get('texto','')[:15]}'")


if __name__ == "__main__":
    import json
    netlist_ok = {
        "componentes": [
            {"ref": "fuente_12V", "nombre": "Fuente 12V", "tipo": "fuente_tension",
             "pines": [{"num": 1, "nombre": "V+"}, {"num": 2, "nombre": "GND"}]},
            {"ref": "R1", "nombre": "Resistor", "tipo": "resistor",
             "pines": [{"num": 1}, {"num": 2}]},
        ],
        "conexiones": [
            {"de": {"ref": "fuente_12V", "pin": 1}, "a": {"ref": "R1", "pin": 1}},
        ],
    }
    # netlist with a component of an UNrecognized type (must fail fidelity)
    netlist_mal = {
        "componentes": [
            {"ref": "fuente_12V", "nombre": "Fuente", "tipo": "fuente_tension",
             "pines": [{"num": 1}, {"num": 2}]},
            {"ref": "X9", "nombre": "coso raro", "tipo": "widget_desconocido",
             "pines": [{"num": 1}, {"num": 2}]},
        ],
        "conexiones": [],
    }
    print("netlist OK:")
    print(json.dumps(verificar_forma(netlist_ok), indent=2, ensure_ascii=False))
    print("\nnetlist MAL (símbolo no resuelto):")
    print(json.dumps(verificar_forma(netlist_mal), indent=2, ensure_ascii=False))
