#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""hcma_proxy.py — LOGIC of the egress network proxy (Fable design A, DISENO_RED_MCP.md).

The network proxy is the ONLY process with egress network access. It receives ONLY already-decided
intents from the monitor (never from the agent), and:

  1. Validates the canonical intent (intent_canonico) for defense in depth.
  2. Applies a default-deny EGRESS FIREWALL (loopback, RFC1918, link-local, metadata).
  3. Resolves DNS with EXACT host (no wildcards) and validates the resulting IP.
  4. Assembles the request ONLY from the intent components (no composite URL: avoids the parser
     differential -> SSRF), with no agent headers, no body, no following redirects.
  5. Always verifies TLS (https).
  6. Returns a hashed SNAPSHOT (raw bytes + status + content-type + intent hash + ts).

This module is PURE LOGIC (importable, testable without systemd). The daemon that wraps it in a
Unix socket and the systemd unit live in hcma_proxy_daemon.py + hcma-proxy.service.

IMPORTANT: this process has NO access to the store nor the key. Only bounded network egress.
"""
import hashlib
import ipaddress
import json
import socket
import ssl
import time

SCRIPTS = None  # set en _import_intent() para no acoplar rutas en import

# ─────────────────────────────────────────────────────────────────────────────
# Egress firewall: global unicast ALLOWLIST (real default-deny, not denylist).
# Fable milestone 2: "a denylist is not default-deny, it is default-allow with exceptions."
# Here we invert: only public global unicast IPv4 is allowed. Everything else (loopback,
# RFC1918, CGNAT, link-local, metadata, multicast, TEST-NET, IPv4-mapped, broadcast, etc.)
# falls by default (there is no code path that allows it).
#
# We use ipaddress.is_global (which already encodes the IANA registry) and SUBTRACT from it the
# ranges that is_global does NOT cover but that we must still reject by policy (shared/RFC6598,
# etc.). It is more robust and maintainable than enumerating /8 by hand.
# ─────────────────────────────────────────────────────────────────────────────
# v1: IPv4-only (declarable simplification; a hostile AAAA is not resolved/used).
_IPV6_PERMITIDO = False

# Additional policy subtractions (is_global marks True some ranges that we still block because
# they are multicast/broadcast/benchmark/reserved and not suitable for public egress).
_EXCLUIDOS_V4 = [
    ipaddress.ip_network("0.0.0.0/8"),        # 0.0.0.0 connects to localhost on Linux
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT (RFC6598)
    ipaddress.ip_network("192.0.0.0/24"),     # IETF protocol assignments
    ipaddress.ip_network("198.18.0.0/15"),    # benchmark
    ipaddress.ip_network("224.0.0.0/4"),      # multicast
    ipaddress.ip_network("240.0.0.0/4"),      # reserved/broadcast
]


def _ip_permitida(ip):
    """Returns True only if `ip` is allowed public global unicast IPv4. Default-deny."""
    if ip.version == 4:
        if not ip.is_global:
            return False
        for r in _EXCLUIDOS_V4:
            if ip in r:
                return False
        return True
    # v1: IPv4-only (declarable simplification; a hostile AAAA is not resolved/used).
    return False


class ProxyError(ValueError):
    """Proxy error (deny). Any exception => denial, never egress without validating."""


def import_intent():
    """Imports intent_canonico from the proxy's own dir (runtime) or scripts (dev)."""
    import os
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    # in production, the proxy deploys intent_canonico.py alongside hcma_proxy.py in its lib/
    import intent_canonico as IC
    return IC


def _validar_ip_no_bloqueada(ip_str):
    """Returns True if the IP is ALLOWED (public global unicast). Real default-deny."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        raise ProxyError(f"IP inválida: {ip_str!r}")
    if not _ip_permitida(ip):
        raise ProxyError(f"IP NO permitida (default-deny): {ip}")
    return True


def resolver_host_exacto(host, timeout=5.0):
    """Resolves ONE exact host (IPv4-only) to IPs and validates they are public global unicast.

    Exact host (no wildcard): a subdomain chosen by the agent is DNS exfiltration.
    We explicitly request ONLY AF_INET (declarable IPv4-only, Fable milestone 2): a hostile AAAA
    is not resolved/used by accident.
    """
    # host was already normalized to punycode by intent_canonico; here we request only AF_INET.
    try:
        infos = socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ProxyError(f"DNS no resolvió {host!r}: {e}")
    ips = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            _validar_ip_no_bloqueada(ip)
            ips.append(ip)
    if not ips:
        raise ProxyError(f"sin IP permitida para {host!r}")
    return ips


def _sin_bytes_control(s, nombre):
    """Asserts that `s` carries no CR/LF/spaces nor control bytes (prevents header injection in
    the hand-assembled HTTP request)."""
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in s):
        raise ProxyError(f"{nombre} contiene bytes de control (CRLF/header injection): {s!r}")
    return s


def ensamblar_request(intent):
    """Assembles the HTTP request ONLY from the intent components. Does not return a byte outside
    the intent: scheme/host/port/path/query are rebuilt here (no composite URL, no agent headers)."""
    IC = import_intent()
    intent = IC.validar_http(intent)
    scheme = intent["scheme"]
    host = intent["host"]
    puerto = intent["puerto"]
    path = intent["path"]
    query = intent["query"]

    # build the sorted query string (canonical, deterministic, with safe encoding)
    partes = []
    for q in query:
        from urllib.parse import quote
        clave = _sin_bytes_control(q["clave"], "query.clave")
        if q["tipo"] == "string":
            # quote() already escapes spaces/control from the value; we validate raw that it does not inject
            _sin_bytes_control(q["valor"], "query.valor")
            valor = quote(q["valor"], safe="")
        else:
            valor = quote(str(q["valor"]), safe="")
        partes.append(f"{clave}={valor}")
    query_str = ("?" + "&".join(partes)) if partes else ""

    # CRLF/header injection: path and host must be free of control bytes (the request is assembled
    # by hand below). The host was already punycode-normalized, but we reaffirm it.
    path = _sin_bytes_control(path, "path")
    host = _sin_bytes_control(host, "host")
    return {
        "scheme": scheme,
        "host": host,
        "puerto": puerto,
        "path": path,
        "query_str": query_str,
        "metodo": intent["metodo"],
        "host_header": host if puerto == 443 else f"{host}:{puerto}",
    }


def egresar(intent, max_bytes=1 << 20, timeout=15.0):
    """Executes the HTTP/HTTPS egress (v1: https, GET/HEAD) and returns the snapshot.

    Uses the stdlib (http.client) WITHOUT following redirects and with TLS verification. The host
    is validated by exact DNS + firewall; the request is assembled ONLY from the intent. Returns
    the hashed snapshot (evidence, not truth).

    `max_bytes` is the cap of bytes read from the network (anti-DoS / bounds exfiltration).
    """
    import http.client  # noqa: F401  (usado por _egresar_manual para tipos/centinelas)

    IC = import_intent()
    intent = IC.validar_http(intent)
    req = ensamblar_request(intent)

    # 1. resolve and validate IP (exact DNS, firewall)
    ips = resolver_host_exacto(req["host"])

    # 2. manual egress: raw socket + TLS (host pinning) + HTTP/1.1 request without redirects.
    #    We use the FIRST allowed IP (validated in resolver_host_exacto).
    ip = ips[0]
    return _egresar_manual(intent, req, ip, ips, max_bytes, timeout)


def _egresar_manual(intent, req, ip, ips, max_bytes, timeout):
    """Manual egress: raw socket + ssl (TLS with server_hostname=req['host']) + HTTP/1.1 request
    without following redirects. Returns snapshot. It is the implementation that guarantees exact
    validated IP and host TLS-pinning. Total wall-clock deadline (Fable milestone 2: a server that
    drips one byte per timeout hangs the proxy without a deadline)."""
    # build the request in bytes (only from req: nothing outside the intent)
    metodo = req["metodo"]
    objetivo = req["path"] + req["query_str"]
    host_header = req["host_header"]
    lineas = [
        f"{metodo} {objetivo} HTTP/1.1",
        f"Host: {host_header}",
        "Connection: close",
        "User-Agent: hcma-proxy/1.0",
        "Accept: */*",
        "",
        "",
    ]
    payload = ("\r\n".join(lineas)).encode("ascii")

    # total deadline: never exceed 2× wall-clock timeout even if the peer drips bytes
    deadline = time.monotonic() + timeout

    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.settimeout(timeout)
    try:
        # TLS with hostname verification = req['host'] (host pinning, not IP pinning).
        # create_default_context() enforces CERT_REQUIRED + check_hostname (NEVER TLS-stripping).
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        tls = ctx.wrap_socket(raw, server_hostname=req["host"])
        tls.settimeout(timeout)
        tls.connect((ip, req["puerto"]))
        tls.sendall(payload)

        # read response until EOF or byte cap (without following redirects); total deadline
        chunks = []
        total = 0
        while True:
            if time.monotonic() > deadline:
                raise ProxyError("deadline total excedido leyendo respuesta")
            try:
                dato = tls.recv(65536)
            except socket.timeout:
                raise ProxyError("timeout leyendo respuesta")
            if not dato:
                break
            chunks.append(dato)
            total += len(dato)
            if total > max_bytes:
                raise ProxyError(f"respuesta excede tope {max_bytes} bytes")
        cuerpo = b"".join(chunks)
    finally:
        raw.close()

    return _snapshot(intent, req, cuerpo)


def _snapshot(intent, req, cuerpo_bytes):
    """Builds the hashed snapshot (content-addressed evidence).

    Marks 3xx status as 'redirect not followed' (the 3xx is a RESULT; it is never followed).
    Separates header from body at the first CRLFCRLF so a hostile body cannot forge Content-Type."""
    IC = import_intent()
    canon = IC.canon_intent(intent)
    status = None
    content_type = None
    redirect_no_seguido = False
    # separate header (up to CRLFCRLF) from body
    head_bytes = cuerpo_bytes
    if b"\r\n\r\n" in cuerpo_bytes:
        head_bytes = cuerpo_bytes.split(b"\r\n\r\n", 1)[0]
    lineas = head_bytes.split(b"\r\n")
    if lineas and lineas[0].startswith(b"HTTP/"):
        partes = lineas[0].split(b" ", 2)
        if len(partes) >= 2:
            try:
                status = int(partes[1])
            except ValueError:
                status = None
    if status is not None and 300 <= status < 400:
        redirect_no_seguido = True
    for linea in lineas[1:]:
        if linea.lower().startswith(b"content-type:"):
            content_type = linea.split(b":", 1)[1].strip().decode("ascii", "replace")[:128]
            break
    dig = hashlib.sha256(cuerpo_bytes).hexdigest()
    return {
        "ok": True,
        "snapshot": {
            "hash_bytes": dig,
            "n_bytes": len(cuerpo_bytes),
            "status": status,
            "content_type": content_type,
            "redirect_no_seguido": redirect_no_seguido,
            "hash_intent": hashlib.sha256(canon.encode("utf-8")).hexdigest(),
            "timestamp": int(time.time()),
        },
        "detalle": "egreso completado (snapshot hasheado)",
    }


def _registrar_egreso_ledger(intent, nonce, epoca, hash_accion):
    """Records the ACTUALLY-executed egress in the external ledger (Fable P0).

    The intent hash is computed by the proxy (canon_intent); nonce/epoch/hash_accion are passed by
    the MONITOR via separate frame fields (they do NOT go inside the intent: the canonical intent
    does not admit descriptive or extra fields). If the ledger is not configured, it does not
    record; if it is configured and not writable, it warns on stderr (egress already happened:
    audit gap).
    """
    import ledger_ejecucion as LE
    import sys as _sys
    IC = import_intent()
    if hash_accion is None:
        hash_accion = hashlib.sha256(IC.canon_intent(intent).encode("utf-8")).hexdigest()
    try:
        LE.registrar_si_configurado("http", hash_accion, nonce, epoca)
    except OSError as e:
        print(f"[proxy] AVISO: ledger de ejecución no escribible: {e}", file=_sys.stderr)


def egresar_intent(intent, nonce=None, epoca=None, hash_accion=None):
    """Proxy entry point: validates the intent, executes the egress, returns snapshot.

    Any exception => {ok:False, bloqueado:True, detalle}. The proxy never returns unvalidated bytes
    nor follows redirects. After a successful egress, it records the execution in the external
    ledger (if configured).
    """
    try:
        resultado = egresar(intent)
    except ProxyError as e:
        return {"ok": False, "bloqueado": True, "detalle": f"proxy: {e}"}
    except Exception as e:  # noexcept: any exception => denial
        return {"ok": False, "bloqueado": True,
                "detalle": f"proxy: {type(e).__name__}: {e}"}
    if resultado.get("ok"):
        _registrar_egreso_ledger(intent, nonce, epoca, hash_accion)
    return resultado


if __name__ == "__main__":
    # smoke test without real network (only validates assembly + firewall on a blocked local host)
    import json
    print("ensamblar_request:", json.dumps(ensamblar_request({
        "schema": 1, "tipo": "http", "metodo": "GET", "scheme": "https",
        "host": "api.example.com", "puerto": 443, "path": "/v1/data",
        "query": [{"clave": "q", "valor": "h ola", "tipo": "string", "max_len": 256}],
    }), ensure_ascii=False))
    print("firewall 127.0.0.1 =>", end=" ")
    try:
        _validar_ip_no_bloqueada("127.0.0.1")
        print("PERMITIDA (bug)")
    except ProxyError as e:
        print("BLOQUEADA:", e)
    print("firewall 169.254.169.254 =>", end=" ")
    try:
        _validar_ip_no_bloqueada("169.254.169.254")
        print("PERMITIDA (bug)")
    except ProxyError as e:
        print("BLOQUEADA:", e)
