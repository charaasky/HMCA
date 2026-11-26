#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""intent_canonico.py — the canonical INTENT for network egress (web + MCP).

Fable 5.2 ruling (fable_red_mcp_gobernada_fable.md, consolidated in DISENO_RED_MCP.md): the
anti-deception invariant is EXECUTION BY RECONSTRUCTION: the bytes that go out on the wire are a
deterministic and exclusive function of the canonical intent the monitor evaluated (and the human
signed if risk >=1). Every field of the intent is EXECUTIVE; there are no descriptive fields.

This module defines the STRICT SCHEMA of the intent and the functions that validate/canonicalize
it. It does NOT execute network (hcma-proxy, another UID, will do that). It only defines the SHAPE
of the object that crosses the boundary and that gets signed.

Hard rules (Fable P0):
  - An UNKNOWN field is rejected (a field executed but not rendered is the attack).
  - There is no `url` nor `destino` (two fields for the same thing can diverge): the request is
    assembled by the proxy from structured components (avoids the parser differential -> SSRF).
  - There are no headers: the proxy sets them by allowlist entry.
  - There is no body in v1 (GET/HEAD); `body_resumen` is left OUT (leaves the body unbound).
  - There are no summaries in MCP: complete canonical args.
  - There are no descriptive fields (`resumen`, `descripcion`, `nota`): everything present executes.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Schema: allowed fields PER intent TYPE.
# ─────────────────────────────────────────────────────────────────────────────
# The canonicalization (stable serialization for signing) only admits these keys.
# An extra field => ValueError (signature and execution cannot diverge).

HTTP_CAMPOS = {
    "schema", "tipo", "metodo", "scheme", "host", "puerto", "path", "query",
}

MCP_CAMPOS = {
    "schema", "tipo", "server_id", "tool", "hash_schema", "args",
}

# Allowed schema level in v1.
SCHEMA_V1 = 1

# Allowed methods in v1 (only GET/HEAD: read-only, no body).
HTTP_METODOS_V1 = ("GET", "HEAD")

# Required fields per type.
HTTP_OBLIGATORIOS = ("schema", "tipo", "metodo", "scheme", "host", "path")
MCP_OBLIGATORIOS = ("schema", "tipo", "server_id", "tool", "hash_schema", "args")


class IntentInvalido(ValueError):
    """Invalid intent according to the canonical schema (rejected: not signed nor executed)."""


def _normalizar_host(host):
    """Validates/normalizes a host to ASCII (punycode for international domains).

    The host travels in punycode; percent-encoding is shown raw and decoded in the signer's render,
    but here we only require it to be a syntactically valid hostname. We do not resolve DNS (the
    proxy does that); only shape.
    """
    if not isinstance(host, str) or not host:
        raise IntentInvalido("host vacío")
    try:
        # punycode: if it comes as unicode, we convert it; if already ASCII (and valid), we keep it.
        if any(ord(ch) > 127 for ch in host):
            host = host.encode("idna").decode("ascii")
        else:
            host = host.encode("ascii").decode("ascii")
    except (UnicodeError, ValueError, AttributeError) as e:
        raise IntentInvalido(f"host inválido: {e}")
    # rejects hosts with characters not allowed in a hostname (spaces, path, userinfo, etc.)
    if not host.replace("-", "").replace(".", "").replace("_", "").isalnum() or \
            "/" in host or ":" in host or "@" in host or " " in host:
        raise IntentInvalido(f"host inválido: {host!r}")
    return host.lower()


def _normalizar_query(query):
    """Validates the typed query: list of {clave, valor, tipo, max_len}. Each parameter is
    EXECUTIVE (sent as-is), so it is validated strictly and bounded."""
    if query is None:
        return []
    if not isinstance(query, list):
        raise IntentInvalido("query debe ser una lista")
    out = []
    for q in query:
        if not isinstance(q, dict):
            raise IntentInvalido("cada parámetro de query debe ser dict")
        # ALLOWED query fields (strict schema also at this level)
        extra = set(q.keys()) - {"clave", "valor", "tipo", "max_len"}
        if extra:
            raise IntentInvalido(f"campo de query desconocido: {sorted(extra)}")
        clave = q.get("clave")
        valor = q.get("valor")
        tipo = q.get("tipo", "string")
        max_len = q.get("max_len", 256)
        if not isinstance(clave, str) or not clave:
            raise IntentInvalido("parámetro de query sin clave")
        if tipo not in ("string", "int", "float"):
            raise IntentInvalido(f"tipo de query no soportado: {tipo}")
        if not isinstance(max_len, int) or max_len <= 0 or max_len > 1024:
            raise IntentInvalido(f"max_len de query fuera de rango: {max_len}")
        # the value is validated by type and bounded by max_len (string) or range (num)
        if tipo == "string":
            if not isinstance(valor, str):
                raise IntentInvalido("parámetro string debe ser str")
            if len(valor) > max_len:
                raise IntentInvalido(f"valor excede max_len {max_len}")
        elif tipo in ("int", "float"):
            if isinstance(valor, bool):
                raise IntentInvalido("bool no es un número válido en query")
            if tipo == "int" and not isinstance(valor, int):
                raise IntentInvalido("parámetro int debe ser int")
            if tipo == "float" and not isinstance(valor, (int, float)):
                raise IntentInvalido("parámetro float debe ser número")
        out.append({"clave": clave, "valor": valor, "tipo": tipo, "max_len": max_len})
    return out


def validar_http(intent):
    """Validates an HTTP intent. Returns the normalized intent. Raises IntentInvalido if it is
    not canonical. This is the ONLY shape that crosses the boundary: no descriptive fields, no
    body, no headers, no composite URL."""
    if not isinstance(intent, dict):
        raise IntentInvalido("intent no es dict")
    extra = set(intent.keys()) - HTTP_CAMPOS
    if extra:
        raise IntentInvalido(f"campo desconocido (no renderizado=no ejecutado): {sorted(extra)}")
    for k in HTTP_OBLIGATORIOS:
        if k not in intent:
            raise IntentInvalido(f"falta campo obligatorio: {k}")
    if intent.get("schema") != SCHEMA_V1:
        raise IntentInvalido("schema no soportado")
    if intent.get("tipo") != "http":
        raise IntentInvalido("tipo != http")
    metodo = intent.get("metodo")
    if metodo not in HTTP_METODOS_V1:
        raise IntentInvalido(f"método no permitido en v1: {metodo}")
    scheme = intent.get("scheme")
    if scheme != "https":
        raise IntentInvalido("solo https en v1")
    host = _normalizar_host(intent.get("host"))
    puerto = intent.get("puerto", 443)
    if not isinstance(puerto, int) or puerto != 443:  # v1: only 443 (explicit TLS)
        raise IntentInvalido("puerto debe ser 443 en v1")
    path = intent.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise IntentInvalido("path debe ser string que empiece en /")
    query = _normalizar_query(intent.get("query"))
    return {
        "schema": SCHEMA_V1,
        "tipo": "http",
        "metodo": metodo,
        "scheme": "https",
        "host": host,
        "puerto": 443,
        "path": path,
        "query": query,
    }


def validar_mcp(intent):
    """Validates an MCP intent. Returns the normalized intent. Raises IntentInvalido if it is
    not canonical. args must be COMPLETE and a dict (validated by the proxy against the pinned
    schema); here we only validate the SHAPE and reject summaries/descriptive fields."""
    if not isinstance(intent, dict):
        raise IntentInvalido("intent no es dict")
    extra = set(intent.keys()) - MCP_CAMPOS
    if extra:
        raise IntentInvalido(f"campo desconocido (no renderizado=no ejecutado): {sorted(extra)}")
    for k in MCP_OBLIGATORIOS:
        if k not in intent:
            raise IntentInvalido(f"falta campo obligatorio: {k}")
    if intent.get("schema") != SCHEMA_V1:
        raise IntentInvalido("schema no soportado")
    if intent.get("tipo") != "mcp":
        raise IntentInvalido("tipo != mcp")
    server_id = intent.get("server_id")
    tool = intent.get("tool")
    hash_schema = intent.get("hash_schema")
    args = intent.get("args")
    if not isinstance(server_id, str) or not server_id:
        raise IntentInvalido("server_id vacío")
    if not isinstance(tool, str) or not tool:
        raise IntentInvalido("tool vacía")
    if not isinstance(hash_schema, str) or not hash_schema.startswith("sha256:"):
        raise IntentInvalido("hash_schema debe ser sha256:<hex>")
    if not isinstance(args, dict):
        raise IntentInvalido("args debe ser dict completo (sin resúmenes)")
    # server_id/tool cannot contain network metadata (they are cataloged IDs, not URLs).
    if any(ch in server_id for ch in ":/@ ") or any(ch in tool for ch in ":/@ "):
        raise IntentInvalido("server_id/tool con caracteres de red no permitidos")
    return {
        "schema": SCHEMA_V1,
        "tipo": "mcp",
        "server_id": server_id,
        "tool": tool,
        "hash_schema": hash_schema,
        "args": args,
    }


def validar_intent(intent):
    """Dispatcher: validates an intent by its `tipo` and returns the normalized intent."""
    if not isinstance(intent, dict):
        raise IntentInvalido("intent no es dict")
    tipo = intent.get("tipo")
    if tipo == "http":
        return validar_http(intent)
    if tipo == "mcp":
        return validar_mcp(intent)
    raise IntentInvalido(f"tipo de intent desconocido: {tipo!r}")


def canon_intent(intent):
    """Stable canonical serialization of a VALIDATED intent (for hashing/signing).

    Requires that the intent already passed validar_intent() (it is re-validated defensively).
    sort_keys=True + ensure_ascii=False + compact separators => deterministic.
    """
    import json
    intent = validar_intent(intent)
    return json.dumps(intent, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


if __name__ == "__main__":
    import json
    ok = validar_http({
        "schema": 1, "tipo": "http", "metodo": "GET", "scheme": "https",
        "host": "api.example.com", "puerto": 443, "path": "/v1/data",
        "query": [{"clave": "q", "valor": "texto", "tipo": "string", "max_len": 256}],
    })
    print("HTTP válido:", json.dumps(ok, ensure_ascii=False, sort_keys=True))
    print("canon:", canon_intent(ok)[:80], "...")

    # unknown descriptive field => rejection (demonstrates anti-deception)
    try:
        validar_http({"schema": 1, "tipo": "http", "metodo": "GET", "scheme": "https",
                      "host": "x.com", "path": "/", "resumen": "solo consulta"})
        print("ERROR: aceptó campo descriptivo")
    except IntentInvalido as e:
        print("OK rechazo campo descriptivo:", e)

    # composite url => rejection
    try:
        validar_http({"schema": 1, "tipo": "http", "metodo": "GET", "scheme": "https",
                      "url": "https://x.com/", "destino": "x.com"})
        print("ERROR: aceptó url/destino")
    except IntentInvalido as e:
        print("OK rechazo url/destino:", e)

    # valid MCP
    okm = validar_mcp({"schema": 1, "tipo": "mcp", "server_id": "proveedor-docs",
                       "tool": "search_docs", "hash_schema": "sha256:" + "0" * 64,
                       "args": {"q": "hola"}})
    print("MCP válido:", json.dumps(okm, ensure_ascii=False, sort_keys=True))
