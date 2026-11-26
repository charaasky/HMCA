#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_intent_canonico.py — tests del intent canónico + conectores gobernados http/mcp.

Cubre el invariante anti-engaño (DISENO_RED_MCP.md §2): ejecución por reconstrucción. Todo
campo es ejecutivo; un campo desconocido se rechaza; el agente no puede bajar el riesgo.
Se corre como:  python3 arquitectura/scripts/test_intent_canonico.py
Exit 0 = todo OK, 1 = FAIL.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import intent_canonico as IC  # noqa: E402
import conectores as CNX  # noqa: E402

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (("  " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


HTTP_OK = {
    "schema": 1, "tipo": "http", "metodo": "GET", "scheme": "https",
    "host": "api.example.com", "puerto": 443, "path": "/v1/data",
    "query": [{"clave": "q", "valor": "texto", "tipo": "string", "max_len": 256}],
}
MCP_OK = {
    "schema": 1, "tipo": "mcp", "server_id": "proveedor-docs",
    "tool": "search_docs", "hash_schema": "sha256:" + "0" * 64,
    "args": {"q": "hola"},
}


def test_http_valido():
    r = IC.validar_http(HTTP_OK)
    check("http válido normaliza host a minúscula", r["host"] == "api.example.com")
    check("http válido conserva query tipada", len(r["query"]) == 1 and r["query"][0]["tipo"] == "string")


def test_rechaza_campo_descriptivo():
    mal = dict(HTTP_OK, resumen="solo consulta")
    try:
        IC.validar_http(mal)
        check("rechaza campo descriptivo", False)
    except IC.IntentInvalido:
        check("rechaza campo descriptivo", True)


def test_rechaza_url_destino():
    mal = {"schema": 1, "tipo": "http", "metodo": "GET", "scheme": "https",
           "url": "https://x.com/", "destino": "x.com"}
    try:
        IC.validar_http(mal)
        check("rechaza url/destino (dos fuentes de verdad)", False)
    except IC.IntentInvalido:
        check("rechaza url/destino (dos fuentes de verdad)", True)


def test_rechaza_post_y_http_plano():
    for mal in [
        dict(HTTP_OK, metodo="POST"),
        dict(HTTP_OK, scheme="http"),
        dict(HTTP_OK, puerto=80),
    ]:
        try:
            IC.validar_http(mal)
            check(f"rechaza método/scheme/puerto inválido: {mal.get('metodo', mal.get('scheme', mal.get('puerto')))}", False)
        except IC.IntentInvalido:
            check(f"rechaza método/scheme/puerto inválido: {mal.get('metodo', mal.get('scheme', mal.get('puerto')))}", True)


def test_rechaza_host_invalido():
    for host in ["x.com/evil", "user:pass@x.com", "x y.com"]:
        try:
            IC.validar_http(dict(HTTP_OK, host=host))
            check(f"rechaza host inválido {host!r}", False)
        except IC.IntentInvalido:
            check(f"rechaza host inválido {host!r}", True)


def test_canon_determinista():
    c1 = IC.canon_intent(HTTP_OK)
    c2 = IC.canon_intent(HTTP_OK)
    check("canon_intent determinista", c1 == c2)


def test_conector_http_no_ejecuta_red():
    r = CNX.conector_http(HTTP_OK)
    check("conector http ok y no ejecuta", r["ok"] is True and "http" in r["herramienta"])
    # bloquea intent inválido (campo descriptivo)
    r2 = CNX.conector_http(dict(HTTP_OK, resumen="x"))
    check("conector http bloquea intent inválido", r2["ok"] is False)


def test_riesgo_no_bajable():
    # el agente declara riesgo 0, pero el conector http tiene piso 1 => efectivo 1
    eff = CNX.riesgo_efectivo("http", HTTP_OK, riesgo_declarado=0)
    check("riesgo http no baja por declaración (efectivo=1)", eff == 1)
    # mcp igual
    eff2 = CNX.riesgo_efectivo("mcp", MCP_OK, riesgo_declarado=0)
    check("riesgo mcp no baja por declaración (efectivo=1)", eff2 == 1)
    # conector desconocido => default-deny (2)
    eff3 = CNX.riesgo_efectivo("herramienta_inexistente", {})
    check("conector desconocido => default-deny (2)", eff3 == 2)


def test_mcp_valido_y_rechazo():
    r = IC.validar_mcp(MCP_OK)
    check("mcp válido normaliza", r["server_id"] == "proveedor-docs")
    # args no-dict => rechazo
    try:
        IC.validar_mcp(dict(MCP_OK, args="resumen"))
        check("mcp rechaza args no-dict", False)
    except IC.IntentInvalido:
        check("mcp rechaza args no-dict", True)
    # hash_schema sin prefijo => rechazo
    try:
        IC.validar_mcp(dict(MCP_OK, hash_schema="0" * 64))
        check("mcp rechaza hash_schema sin sha256:", False)
    except IC.IntentInvalido:
        check("mcp rechaza hash_schema sin sha256:", True)


if __name__ == "__main__":
    test_http_valido()
    test_rechaza_campo_descriptivo()
    test_rechaza_url_destino()
    test_rechaza_post_y_http_plano()
    test_rechaza_host_invalido()
    test_canon_determinista()
    test_conector_http_no_ejecuta_red()
    test_riesgo_no_bajable()
    test_mcp_valido_y_rechazo()
    print("\nRESULTADO: %d FAIL" % len(FAIL))
    sys.exit(1 if FAIL else 0)
