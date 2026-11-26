#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""lanzador_agente.py — Phase 2 (closure): dumb launcher that connects to the monitor and passes the FD to the
agent in the sandbox (runsc -pass-fd).

Fable (phase 2 revocation): "The inherited-FD principle still applies at the agent end.
The launcher connects and passes the already-connected descriptor to the agent. The agent never sees a route
nor can connect by name."

This process:
  1. Connects to the monitor socket (/run/hcma/monitor.sock) — the credentials the monitor sees
     are those of the LAUNCHER (root/human), NOT those of 101.
  2. Invokes `runsc run -pass-fd <fd>:3` passing the connected socket into the sandbox as fd 3.
  3. The agent (bundle entrypoint) uses the IPC client over fd 3.

NOT the monitor (does not touch the store), does NOT execute the tools (that is the agent inside).
Only connects + launches runsc with the FD already connected.
"""
import os
import socket
import subprocess
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import monitor_ipc as IPC  # noqa: E402  (framing; sin deps del monitor)

MONITOR_SOCK = os.environ.get("HCMA_MONITOR_SOCK", "/run/hcma/monitor.sock")
RUNSC = "/usr/bin/runsc"
BUNDLE = os.environ.get("HCMA_AGENT_BUNDLE",
                        "/home/charasky/Documentos/HMC/arquitectura/gvisor/bundle-agente")
GUEST_FD = 3  # fd inside the sandbox where the agent reads/writes the IPC
# Containment observability (opt-in): if set, runsc writes its debug-log to the host and
# the launcher reports it to the monitor at the end to classify/record escapes (vigilancia_gvisor).
GVISOR_LOG = os.environ.get("HCMA_GVISOR_LOG", "")


def conectar_monitor():
    """Connects to the monitor socket. Returns the connected socket (host fd)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10.0)
    s.connect(MONITOR_SOCK)
    return s


def saludar(sock, agent_id=None, modelo=None, lanzamiento=None):
    """Declares the session identity to the monitor BEFORE launching the agent.

    It is the FIRST frame of the connection (written before the agent exists), so the agent
    cannot forge it: the monitor only accepts the first greeting. Fire-and-forget: the launcher
    does NOT read the response (the socket is handed to the agent)."""
    IPC.enviar_frame(sock, {"tipo": "saludo_sesion",
                            "agent_id": agent_id,
                            "modelo": modelo,
                            "lanzamiento": lanzamiento})


def lanzar(sock):
    """Launches runsc with the connected socket passed into the sandbox as fd 3."""
    host_fd = sock.fileno()
    # mark the fd as inheritable by the child (runsc)
    cmd = [RUNSC, "--network=none"]
    if GVISOR_LOG:
        cmd += ["--debug", f"--debug-log={GVISOR_LOG}"]
    cmd += ["run", "--bundle", BUNDLE, "-pass-fd", f"{host_fd}:{GUEST_FD}",
            "hcma-agent-sesion"]
    # runsc needs the fd open; we pass it by inheriting it (pass_fds makes close_fds=True
    # by default, which is what we want: only the passed fd is inherited).
    return subprocess.run(cmd, pass_fds=(host_fd,))


def reportar_log_gvisor(agent_id):
    """Reads gVisor's debug-log and reports it to the monitor to classify/record escapes.

    It is processed AT THE END (when runsc finishes): immediate protocol blocking is covered by the
    monitor (invalid frame); escapes INSIDE the sandbox (network/fs_host/privileges/resources)
    are recorded here at close. The classifier (vigilancia_gvisor) is conservative."""
    if not GVISOR_LOG or not os.path.exists(GVISOR_LOG):
        return None
    try:
        with open(GVISOR_LOG, "r", errors="replace") as f:
            lineas = f.read().splitlines()
    except OSError:
        return None
    if not lineas:
        return None
    try:
        s = conectar_monitor()
    except OSError:
        return None
    try:
        return IPC.ClienteMonitor(sock=s).pedir(
            {"tipo": "procesar_log_gvisor", "agent_id": agent_id, "lineas": lineas})
    finally:
        try:
            s.close()
        except OSError:
            pass


def reportar_exit_anomalo(agent_id, returncode):
    """Reports an ABNORMAL agent death to the monitor (signal = DoS/resources).

    The launcher opens a SEPARATE CONTROL connection (the agent's already closed when
    runsc finished). The monitor classifies the exit code and, if anomalous, records an escape incident
    (resources) with the `agent_id` (blocks the agent until the human re-enables it)."""
    if returncode is None or (returncode >= 0 and returncode < 128):
        return None  # normal exit
    try:
        s = conectar_monitor()
    except OSError:
        return None
    try:
        cliente = IPC.ClienteMonitor(sock=s)
        return cliente.pedir({"tipo": "reportar_exit_agente",
                              "agent_id": agent_id, "returncode": returncode})
    finally:
        try:
            s.close()
        except OSError:
            pass


if __name__ == "__main__":
    agent_id = os.environ.get("HCMA_AGENT_ID")
    modelo = os.environ.get("HCMA_MODELO")
    lanzamiento = os.environ.get("HCMA_LANZAMIENTO")
    print(f"lanzador: conectando a {MONITOR_SOCK} ...")
    s = conectar_monitor()
    print(f"conectado. fd host={s.fileno()}, pasando al sandbox como fd {GUEST_FD}")
    saludar(s, agent_id=agent_id, modelo=modelo, lanzamiento=lanzamiento)
    r = lanzar(s)
    s.close()
    reportar_log_gvisor(agent_id)
    reportar_exit_anomalo(agent_id, r.returncode)
    sys.exit(r.returncode)
