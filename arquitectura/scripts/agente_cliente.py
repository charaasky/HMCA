#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""agente_cliente.py — Phase 2 (closure): entrypoint of the AGENT inside the sandbox.

This process runs INSIDE the gVisor sandbox as UID 101. It does NOT import memoria_sqlite,
gobernanza, nonces, or objetivo (those live in the monitor). Only:
  1. Opens the fd inherited from the launcher (fd 3) as an IPC socket.
  2. Uses ClienteMonitor to send `pedir(op)` to the monitor and receive responses.

The agent PROPOSES operations; the monitor DECIDES. The agent never touches the store.
"""
import os
import socket
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import monitor_ipc as IPC  # noqa: E402  (imports the client; NOT the monitor modules)

GUEST_FD = int(os.environ.get("HCMA_AGENT_FD", "3"))


def main():
    # open the inherited fd as a socket (passed by the launcher with runsc -pass-fd)
    sock = socket.socket(fileno=GUEST_FD)
    sock.settimeout(30.0)
    cliente = IPC.ClienteMonitor(sock=sock)

    print("[agente] conectado al monitor vía fd", GUEST_FD)

    # demo: request the current monitor state
    r = cliente.pedir({"tipo": "estado"})
    print("[agente] estado del monitor:", r)

    # demo: propose a write (stays unverified; the monitor decides)
    r2 = cliente.pedir({"tipo": "propuesta_write", "key": "agente.demo",
                        "value": 1, "fuente": "calculadora"})
    print("[agente] propuesta_write:", r2)

    sock.close()
    print("[agente] listo")


if __name__ == "__main__":
    main()
