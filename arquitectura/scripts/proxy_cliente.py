#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""proxy_cliente.py — MONITOR client to the egress network proxy.

Design A (DISENO_RED_MCP.md §3): the MONITOR is the only one that connects to the proxy. When the
monitor has DECIDED an intent (risk <=1 with current authorization, or an approved template), this
client sends the intent to the proxy over the Unix socket /run/hcma-proxy.sock and returns the
snapshot.

The AGENT never uses this module (it has no route to the proxy socket, only to the monitor via fd).
"""
import os
import socket
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import monitor_ipc as IPC  # noqa: E402  (reusa framing estricto)


PROXY_SOCK = os.environ.get("HCMA_PROXY_SOCK", "/run/hcma-proxy/proxy.sock")


def pedir_egreso(intent, sock_path=None, timeout=40.0, nonce=None, epoca=None, hash_accion=None):
    """Connects to the proxy, sends {intent, nonce, epoca, hash_accion}, receives the snapshot.

    nonce/epoch/hash_accion travel SEPARATE from the intent (the canonical intent does not admit
    extra fields). The monitor provides them after consuming the authorization; the proxy uses them
    for the external execution ledger. Fail-closed: no proxy or broken framing => {ok:False,
    bloqueado:True}.
    """
    path = sock_path or PROXY_SOCK
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        frame = {"intent": intent}
        if nonce is not None:
            frame["nonce"] = nonce
        if epoca is not None:
            frame["epoca"] = epoca
        if hash_accion is not None:
            frame["hash_accion"] = hash_accion
        IPC.enviar_frame(s, frame)
        return IPC.recibir_frame(s)
    except (OSError, IPC.FrameError) as e:
        return {"ok": False, "bloqueado": True, "detalle": f"proxy inalcanzable: {e}"}
    finally:
        try:
            s.close()
        except OSError:
            pass


if __name__ == "__main__":
    import json
    # smoke: attempts an egress (fails if no proxy is running, but confirms the plumbing)
    r = pedir_egreso({
        "schema": 1, "tipo": "http", "metodo": "GET", "scheme": "https",
        "host": "example.com", "puerto": 443, "path": "/",
    })
    print(json.dumps(r, ensure_ascii=False, default=str))
