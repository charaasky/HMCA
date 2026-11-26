#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_hcma_proxy.py — tests de la LÓGICA del proxy-red (sin red real).

Cubre: ensamble del request SOLO desde el intent, firewall de egreso default-deny (loopback/
RFC1918/link-local/metadata), host exacto (no wildcard), y rechazo de intents no canónicos.
No ejecuta red: las partes de red (egresar) se probarán en config real con hcma-proxy desplegado.
Se corre como:  python3 arquitectura/scripts/test_hcma_proxy.py
Exit 0 = OK, 1 = FAIL.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import hcma_proxy as PX  # noqa: E402

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (("  " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


HTTP_OK = {
    "schema": 1, "tipo": "http", "metodo": "GET", "scheme": "https",
    "host": "api.example.com", "puerto": 443, "path": "/v1/data",
    "query": [{"clave": "q", "valor": "hola mundo", "tipo": "string", "max_len": 256}],
}


def test_ensamble_solo_desde_intent():
    r = PX.ensamblar_request(HTTP_OK)
    check("compute scheme", r["scheme"] == "https")
    check("computa host exacto", r["host"] == "api.example.com")
    check("computa path", r["path"] == "/v1/data")
    check("query encodeada desde intent", r["query_str"] == "?q=hola%20mundo")
    check("host_header desde intent", r["host_header"] == "api.example.com")


def test_firewall_default_deny():
    # default-deny real: las direcciones internas/especiales NO están en la allowlist
    for ip, tag in [
        ("127.0.0.1", "loopback"),
        ("10.0.0.5", "RFC1918 10/8"),
        ("192.168.1.1", "RFC1918 192.168/16"),
        ("172.16.0.1", "RFC1918 172.16/12"),
        ("169.254.169.254", "metadata cloud"),
        ("0.0.0.0", "0/8 (conecta a localhost)"),
        ("100.64.0.1", "CGNAT 100.64/10"),
        ("::1", "loopback v6"),
        ("fe80::1", "link-local v6"),
        ("::ffff:127.0.0.1", "IPv4-mapped loopback"),
    ]:
        try:
            PX._validar_ip_no_bloqueada(ip)
            check(f"rechaza {tag} ({ip})", False)
        except PX.ProxyError:
            check(f"rechaza {tag} ({ip})", True)


def test_firewall_permite_publica():
    try:
        PX._validar_ip_no_bloqueada("8.8.8.8")
        check("permite IP pública 8.8.8.8", True)
    except PX.ProxyError:
        check("permite IP pública 8.8.8.8", False)
    try:
        PX._validar_ip_no_bloqueada("1.1.1.1")
        check("permite IP pública 1.1.1.1", True)
    except PX.ProxyError:
        check("permite IP pública 1.1.1.1", False)


def test_host_exacto_no_wildcard():
    # host con wildcard/asterisco debe ser rechazado por intent_canonico antes de llegar al proxy
    import intent_canonico as IC
    try:
        IC.validar_http(dict(HTTP_OK, host="*.evil.com"))
        check("rechaza host con wildcard", False)
    except IC.IntentInvalido:
        check("rechaza host con wildcard", True)


def test_egresar_rechaza_no_canonico():
    # intent con campo descriptivo => ProxyError/valida falla, nunca egresa
    r = PX.egresar_intent(dict(HTTP_OK, resumen="x"))
    check("egresar_intent rechaza campo descriptivo", r["ok"] is False and r.get("bloqueado") is True)


def test_snapshot_determinista():
    import hashlib
    canon = "abc"
    snap = PX._snapshot({"schema": 1, "tipo": "http", "metodo": "GET", "scheme": "https",
                         "host": "x.com", "puerto": 443, "path": "/", "query": []},
                        {}, b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nhola")
    check("snapshot tiene hash_bytes", isinstance(snap["snapshot"]["hash_bytes"], str)
          and len(snap["snapshot"]["hash_bytes"]) == 64)
    check("snapshot extrae status 200", snap["snapshot"]["status"] == 200)
    check("snapshot extrae content_type", snap["snapshot"]["content_type"] == "text/plain")
    check("snapshot con hash_intent", isinstance(snap["snapshot"]["hash_intent"], str))


if __name__ == "__main__":
    test_ensamble_solo_desde_intent()
    test_firewall_default_deny()
    test_firewall_permite_publica()
    test_host_exacto_no_wildcard()
    test_egresar_rechaza_no_canonico()
    test_snapshot_determinista()
    print("\nRESULTADO: %d FAIL" % len(FAIL))
    sys.exit(1 if FAIL else 0)
