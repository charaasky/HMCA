#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_render_firma.py — test del render LEGIBLE de la ceremonia de firma (P0 potencial hito 3).

Dictamen Fable (fable_gate_egreso_fable.md §3/§5): la pantalla de aprobación debe mostrar el
dominio registrable destacado, el punycode visible y la query completa, SIN truncar, con strings
escapados. El render debe ser TOTAL y del MISMO objeto canónico que se firma.

Se corre como:  python3 arquitectura/scripts/test_render_firma.py
Exit 0 = OK, 1 = FAIL.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import render_firma as RF  # noqa: E402
import intent_canonico as IC  # noqa: E402

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (("  " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


HTTP = {
    "schema": 1, "tipo": "http", "metodo": "GET", "scheme": "https",
    "host": "example.org", "puerto": 443, "path": "/v1/data",
    "query": [{"clave": "q", "valor": "hola mundo", "tipo": "string", "max_len": 256}],
}


def test_normalizar_intent():
    """normalizar_intent devuelve el intent canónico (query=[] agregado si falta)."""
    crudo = dict(HTTP, query=None)  # sin query => debe normalizar a []
    n = RF.normalizar_intent(crudo)
    check("normaliza http (query omitido -> [])", n["query"] == [])


def test_render_dominio_destacado_y_query():
    """El render destaca el dominio registrable y muestra la query completa."""
    txt = RF.render_intent(HTTP)
    check("destaca dominio registrable", "DOMINIO REGISTRABLE" in txt)
    check("muestra el host", "example.org" in txt)
    check("muestra query completa (valor)", "hola mundo" in txt)
    check("muestra tipo/max de query", "string, máx 256" in txt)


def test_render_sin_truncar():
    """Un path/query largo NO se trunca (totalidad)."""
    largo = "x" * 500
    intent = dict(HTTP, path="/" + largo)
    txt = RF.render_intent(intent)
    check("path largo no truncado (aparece completo)", largo in txt)


def test_render_escapa_control():
    """Strings con control/bidi se escapan (no se inyecta crudo)."""
    hostil = {"clave": "q", "valor": "a\u200bb", "tipo": "string", "max_len": 256}
    intent = dict(HTTP, query=[hostil])
    txt = RF.render_intent(intent)
    # el zero-width space U+200B debe aparecer escapado, no crudo
    check("zero-width space escapado (no crudo)", "\u200b" not in txt and "\\u200B" in txt)


def test_render_rechaza_campo_descriptivo():
    """Un intent con campo descriptivo se rechaza (no se renderiza ni se firma)."""
    mal = dict(HTTP, resumen="solo consulta")
    try:
        RF.render_intent(mal)
        check("rechaza campo descriptivo en el render", False)
    except IC.IntentInvalido:
        check("rechaza campo descriptivo en el render", True)


def test_render_ceremonia_incluye_need_y_tool():
    """La ceremonia completa incluye necesidad y tool (lo que entra al payload firmado)."""
    txt = RF.render_ceremonia("necesidad:abc", "http", HTTP)
    check("ceremonia incluye necesidad", "necesidad:abc" in txt)
    check("ceremonia incluye tool", "http" in txt)


if __name__ == "__main__":
    test_normalizar_intent()
    test_render_dominio_destacado_y_query()
    test_render_sin_truncar()
    test_render_escapa_control()
    test_render_rechaza_campo_descriptivo()
    test_render_ceremonia_incluye_need_y_tool()
    print("\nRESULTADO: %d FAIL" % len(FAIL))
    sys.exit(1 if FAIL else 0)
