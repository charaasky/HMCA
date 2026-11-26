#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""agente_vsock.py — entrypoint del AGENTE dentro de la microVM Firecracker.

A diferencia de agente_cliente.py (gVisor, fd heredado), acá el transporte es virtio-vsock:
el guest conecta a CID_HOST=2, puerto 5000, y Firecracker reenvía al UDS /run/hcma/vsock.sock
que el MONITOR crea y escucha.

El framing NO cambia (monitor_ipc): el agente PROPOSE, el monitor DECIDE.
"""
import os
import socket
import sys

SCRIPTS = "/usr/local/lib/hcma_agente"
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import monitor_ipc as IPC  # noqa: E402

VSOCK_PORT = int(os.environ.get("HCMA_VSOCK_PORT", "5000"))
CID_HOST = getattr(socket, "VMADDR_CID_HOST", 2)


def main():
    sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    sock.settimeout(30.0)
    sock.connect((CID_HOST, VSOCK_PORT))
    cliente = IPC.ClienteMonitor(sock=sock)

    print("[agente-vsock] conectado al monitor (cid=%d, port=%d)" % (CID_HOST, VSOCK_PORT))

    r = cliente.pedir({"tipo": "estado"})
    print("[agente-vsock] estado del monitor:", r)

    r2 = cliente.pedir({"tipo": "propuesta_write", "key": "agente.demo",
                        "value": 1, "fuente": "calculadora"})
    print("[agente-vsock] propuesta_write:", r2)

    sock.close()
    print("[agente-vsock] listo")


if __name__ == "__main__":
    main()
