#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""lanzar.py — lanzador de la microVM Firecracker (boundary de hardware).

Reemplaza a `lanzador_agente.py` (runsc). Firecracker no tiene FD-pass: la comunicación
agente↔monitor pasa por virtio-vsock:

    agente (guest) ── vsock ──▶ firecracker ── uds (/run/hcma/vsock.sock) ──▶ monitor

Propiedad del UDS (importante): el MONITOR es quien crea `/run/hcma/vsock.sock` (bind + listen +
0600) vía `hcma_monitor_daemon._crear_socket_agente` cuando `HCMA_VSOCK_UDS` está seteado.
# Firecracker (este proceso, como usuario NO-root hcma-firecracker) se CONECTA a ese socket
# cuando el guest inicia una conexión
vsock (CID 2). Por eso este lanzador NO crea ni deslinkea el UDS: el dueño del listener es el
monitor, y su ciclo de vida (bind/unlink) muere con el proceso del monitor (fail-closed).

Este lanzador:
  1. verifica que `firecracker` y `config.json` existan (fail-closed),
  2. arranca `firecracker --config-file config.json` en foreground (systemd lo gestiona).

El framing (monitor_ipc), el session_id, el saludo y la gobernanza NO cambian.
"""
import os
import subprocess
import sys

FIRECRACKER = os.environ.get("HCMA_FIRECRACKER", "/usr/bin/firecracker")
CONFIG = os.environ.get("HCMA_FIRECRACKER_CONFIG", "/var/lib/hcma-firecracker/config.json")
VSOCK_UDS = "/run/hcma/vsock.sock_5000"
API_SOCK = os.environ.get("HCMA_FIRECRACKER_API_SOCK", "/run/hcma-firecracker/api.sock")


def main():
    if not os.path.exists(FIRECRACKER):
        print(f"firecracker no encontrado en {FIRECRACKER}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(CONFIG):
        print(f"config.json no encontrado en {CONFIG}", file=sys.stderr)
        sys.exit(1)
    print(f"firecracker arrancando con {CONFIG} (vsock → {VSOCK_UDS}, UDS creado por el monitor)")
    # Firecracker queda en foreground; systemd lo gestiona (Restart=no, fail-closed).
    r = subprocess.run([FIRECRACKER, "--api-sock", API_SOCK, "--config-file", CONFIG])
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
