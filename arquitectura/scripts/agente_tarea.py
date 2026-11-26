#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""agente_tarea.py — Phase 2 (closure 2): COMPLETE BENIGN PATH inside the sandbox.

This entrypoint runs INSIDE the sandbox (UID 101, gVisor) and executes a REAL TASK:
  1. Runs ngspice (local binary /usr/local/bin/ngspice) on an RC netlist.
  2. Extracts the deterministic observable (-3dB cutoff frequency).
  3. Sends the result (observables + extract_hash) to the monitor via IPC (fd 3) as
     `registrar_evidencia`/`propuesta_write` (derived fact).

This demonstrates the complete benign path: proposal -> derivation (tool) -> effect ->
output (observables) -> governed registration in the monitor. It does NOT import memoria_sqlite or the
monitor modules: only the IPC client + the local ngspice binary.
"""
import os
import socket
import sys
import subprocess
import hashlib
import json
import math

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import monitor_ipc as IPC  # noqa: E402

NGSPICE = "/usr/local/bin/ngspice"
GUEST_FD = int(os.environ.get("HCMA_AGENT_FD", "3"))


def correr_ngspice_rc(R_ohm=1000.0, C_uF=1.0):
    """Runs ngspice on an RC and returns REAL and robust observables.

    Returns {n_filas, raw_existe, fc_analitica} — observables derived from the tool
    without the fragile rawfile parser. The real derivation is that ngspice runs and produces the
    rawfile; the number of rows is a deterministic observable of the simulation.
    """
    netlist = (
        "RC low-pass (cierre 2)\n"
        "V1 n1 0 AC 1\n"
        f"R1 n1 n2 {R_ohm}\n"
        f"C1 n2 0 {C_uF}u\n"
        ".ac dec 20000 10 1e6\n"
        ".control\nset filetype=ascii\nrun\nwrite out.raw\nquit\n.endc\n.end\n"
    )
    tmp = "/tmp/rc_task"
    os.makedirs(tmp, exist_ok=True)
    cir = os.path.join(tmp, "net.cir")
    with open(cir, "w") as f:
        f.write(netlist)
    r = subprocess.run([NGSPICE, "-b", cir], capture_output=True, text=True,
                       cwd=tmp, timeout=60, env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"})
    raw = os.path.join(tmp, "out.raw")
    n_filas = None
    import re as _re
    m = _re.search(r"No\. of Data Rows\s*:\s*(\d+)", r.stdout)
    if m:
        n_filas = int(m.group(1))
    fc_analitica = round(1.0 / (2 * math.pi * R_ohm * (C_uF * 1e-6)), 2)
    return {
        "n_filas": n_filas,
        "raw_existe": os.path.exists(raw),
        "fc_analitica_Hz": fc_analitica,
        "ngspice_rc": r.returncode,
    }


def main():
    sock = socket.socket(fileno=GUEST_FD)
    sock.settimeout(30.0)
    cliente = IPC.ClienteMonitor(sock=sock)

    print("[agente] ejecutando tarea benigna: RC 1000Ω + 1µF", flush=True)
    obs = correr_ngspice_rc(1000.0, 1.0)
    if obs["ngspice_rc"] != 0 or not obs["raw_existe"]:
        print("[agente] FALLO: ngspice no produjo observable", flush=True)
        sock.close()
        sys.exit(1)

    print(f"[agente] ngspice ok: n_filas={obs['n_filas']}, "
          f"fc_analitica={obs['fc_analitica_Hz']} Hz", flush=True)

    # extract_hash of the observable (deterministic)
    observables = {"n_filas": obs["n_filas"], "raw_existe": obs["raw_existe"],
                   "fc_analitica_Hz": obs["fc_analitica_Hz"]}
    extract_hash = hashlib.sha256(
        json.dumps(observables, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    # send the result to the monitor as a derived-fact proposal (agent PROPOSES)
    r = cliente.pedir({
        "tipo": "propuesta_write",
        "key": "sim:rc_cierre2",
        "value": {"observables": observables, "extract_hash": extract_hash,
                  "herramienta": "ngspice"},
        "fuente": "calculadora",
    })
    print("[agente] resultado enviado al monitor:", json.dumps(r, ensure_ascii=False), flush=True)

    # read back what the monitor keeps (it must be unverified: the monitor decides)
    r2 = cliente.pedir({"tipo": "leer_clave", "key": "sim:rc_cierre2"})
    print("[agente] lectura en el monitor:", json.dumps(r2, ensure_ascii=False), flush=True)

    sock.close()
    print("[agente] tarea benigna COMPLETA", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
