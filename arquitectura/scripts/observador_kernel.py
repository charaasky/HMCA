#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""observador_kernel.py — observability connector: sandbox/kernel log watcher.

Closes the "silent blocks" gap by feeding `incidentes.registrar` with the escape vectors INSIDE the
sandbox. It tails a host log (runsc --debug-log/--strace, or the output of an eBPF LSM / auditd)
and, for each new line, classifies it with `vigilancia_gvisor` (sensitive path blacklist +
syscalls) and reports the escapes to the monitor (op `procesar_log_gvisor`).

This process runs on the HOST (TCB, outside the sandbox), NOT the agent. The agent never writes
the incident: the watcher only DETECTS and reports it; the monitor registers and blocks it; the
human decides to patch and re-enable (incidentes.py).

Modes:
  --once                    : reads the log once, reports and exits (for the launcher / tests).
  (without --once)          : tail -f (positions at the end and reports new lines).
  --clasificador gvisor|audit : gvisor => procesar_log_gvisor (vigilancia_gvisor);
                                audit  => procesar_log_audit (observador_audit, auditd).

Usage:
  observador_kernel.py --log /run/hcma/gvisor/agente.log --agent-id agente-42 [--once]
  observador_kernel.py --log /var/log/audit/audit.log --clasificador audit --agent-id host [--once]
"""
import argparse
import os
import socket
import sys
import time

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import vigilancia_gvisor as VG  # noqa: E402
import monitor_ipc as IPC      # noqa: E402  (cliente del monitor, sin deps del store)

MONITOR_SOCK = os.environ.get("HCMA_MONITOR_SOCK", "/run/hcma/monitor.sock")


def leer_cola(path):
    """Reads the whole file and returns the lines (for --once or first sweep)."""
    try:
        with open(path, "r", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def vigilar(path, agent_id, reportar, once=False, poll=0.5):
    """Tails the log: classifies and reports escapes via `reportar(lineas) -> result`.

    `reportar` is a callable that receives the list of new lines and returns whatever. In socket
    mode it wraps `procesar_log_gvisor`; in tests it wraps `vigilancia_gvisor.procesar_log`."""
    if once:
        lineas = leer_cola(path)
        if lineas:
            reportar(lineas)
        return
    try:
        f = open(path, "r", errors="replace")
    except OSError:
        return
    try:
        f.seek(0, os.SEEK_END)  # start from the end (only NEW lines)
        while True:
            lineas = f.readlines()
            if lineas:
                reportar([ln.rstrip("\n") for ln in lineas])
            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        f.close()


def _reportar_socket(agent_id, session_id="watcher", clasificador="gvisor"):
    """Creates a `reportar` that connects to the monitor and sends the log op per line read.

    `clasificador` chooses the monitor op:
      - "gvisor": `procesar_log_gvisor` (vigilancia_gvisor: path blacklist + syscalls).
      - "audit" : `procesar_log_audit` (observador_audit: parses avc: denied / syscall success=no).
    """
    tipo_op = "procesar_log_audit" if clasificador == "audit" else "procesar_log_gvisor"

    def _rep(lineas):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(10.0)
            s.connect(MONITOR_SOCK)
        except OSError:
            return None
        try:
            cli = IPC.ClienteMonitor(sock=s)
            return cli.pedir({"tipo": tipo_op, "agent_id": agent_id, "lineas": lineas})
        finally:
            try:
                s.close()
            except OSError:
                pass
    return _rep


def main():
    ap = argparse.ArgumentParser(description="sandbox/kernel escape watcher")
    ap.add_argument("--log", required=True, help="file to watch (tail)")
    ap.add_argument("--agent-id", default=None, help="stable identity to attribute to")
    ap.add_argument("--once", action="store_true", help="process once and exit")
    ap.add_argument("--clasificador", default="gvisor", choices=("gvisor", "audit"),
                    help="gvisor: procesar_log_gvisor | audit: procesar_log_audit (auditd)")
    args = ap.parse_args()

    reportar = _reportar_socket(args.agent_id, clasificador=args.clasificador)
    vigilar(args.log, args.agent_id, reportar, once=args.once)


if __name__ == "__main__":
    main()
