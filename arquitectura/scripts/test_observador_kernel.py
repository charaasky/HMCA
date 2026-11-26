#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_observador_kernel.py — watcher de log (tail) que alimenta incidentes."""
import os
import sys
import tempfile

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import observador_kernel as OBS               # noqa: E402
import vigilancia_gvisor as VG                # noqa: E402
import incidentes as INC                      # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        FAIL.append(name)


print("=== [1] leer_cola ===")
tmp = tempfile.mkdtemp(prefix="obs_")
log_path = os.path.join(tmp, "gvisor.log")
with open(log_path, "w") as f:
    f.write("connect(...) => EPERM\n")
    f.write("read(fd) => ok (benigno)\n")
    f.write("open(/dev/mem) => EPERM\n")
lineas = OBS.leer_cola(log_path)
check("1a. lee 3 líneas", len(lineas) == 3, lineas)

print("\n=== [2] vigilar (once) clasifica y reporta al monitor ===")
m = HCMAMemoriaSQLite(":memory:")

def reportar(ls):
    # en producción esto envía procesar_log_gvisor al monitor; acá directo a memoria (test)
    return VG.procesar_log(m, "s1", "agente-a", ls)

OBS.vigilar(log_path, "agente-a", reportar, once=True)
check("2a. agente bloqueado tras escapes", INC.agente_bloqueado(m, agent_id="agente-a") is True)
activos = INC.incidentes_activos(m)
check("2b. 2 incidentes (red + kernel_mem), ignora la benigna", len(activos) == 2, activos)
check("2c. vectores correctos",
      {i["vector"] for i in activos} == {"red", "kernel_mem"},
      [i["vector"] for i in activos])

print("\n=== [3] vigilar con log inexistente no rompe ===")
OBS.vigilar(os.path.join(tmp, "no_existe.log"), "agente-a", reportar, once=True)
check("3a. no lanza", True)

print("\n=== [4] _reportar_socket elige la op según clasificador ===")
capturados = []

class _FakeCliente:
    def __init__(self, sock=None):
        pass
    def pedir(self, op):
        capturados.append(op)
        return {"ok": True}

class _FakeSocket:
    def __init__(self, *a, **k):
        pass
    def settimeout(self, t):
        pass
    def connect(self, p):
        pass
    def close(self):
        pass

_orig_socket = OBS.socket.socket
_orig_cli = OBS.IPC.ClienteMonitor
OBS.socket.socket = _FakeSocket
OBS.IPC.ClienteMonitor = _FakeCliente
try:
    OBS._reportar_socket("host", clasificador="audit")(["avc: denied { open }"])
    OBS._reportar_socket("host")(["connect(...) => EPERM"])
finally:
    OBS.socket.socket = _orig_socket
    OBS.IPC.ClienteMonitor = _orig_cli

check("4a. clasificador audit → procesar_log_audit",
      capturados[0]["tipo"] == "procesar_log_audit", str(capturados))
check("4b. clasificador gvisor (default) → procesar_log_gvisor",
      capturados[1]["tipo"] == "procesar_log_gvisor", str(capturados))

print(f"\nRESULTADO: {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
