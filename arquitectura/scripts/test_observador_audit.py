#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_observador_audit.py — señal fuera de banda (auditd) → incidentes."""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import observador_audit as OA                 # noqa: E402
import incidentes as INC                      # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        FAIL.append(name)


print("=== [1] clasificación de líneas auditd ===")
check("1a. avc denied open /etc/passwd → fs_host",
      OA.clasificar_audit('type=AVC msg=audit(1:1): avc: denied { open } for pid=42 '
                          'comm="runsc" path="/etc/passwd"')[1] == "fs_host")
check("1b. avc denied read /dev/mem → kernel_mem",
      OA.clasificar_audit('type=AVC msg=audit(1:1): avc: denied { read } for pid=42 '
                          'comm="runsc" path="/dev/mem"')[1] == "kernel_mem")
check("1c. avc denied connect (sin path) → privilegios",
      OA.clasificar_audit('type=AVC msg=audit(1:1): avc: denied { connect } for pid=42 '
                          'comm="runsc"')[1] == "privilegios")
check("1d. syscall success=no → syscall_denegada",
      OA.clasificar_audit('type=SYSCALL msg=audit(1:1): syscall=257 (openat) success=no')[0]
      == "syscall_denegada")
check("1e. syscall success=yes → None",
      OA.clasificar_audit('type=SYSCALL msg=audit(1:1): syscall=257 (openat) success=yes') is None)

print("\n=== [2] procesar_audit registra + bloquea + idempotente ===")
m = HCMAMemoriaSQLite(":memory:")
log = [
    'type=AVC msg=audit(1:1): avc: denied { open } for pid=42 comm="runsc" path="/etc/passwd"',
    'type=AVC msg=audit(1:2): avc: denied { read } for pid=42 comm="runsc" path="/dev/mem"',
    'type=AVC msg=audit(1:3): avc: denied { connect } for pid=42 comm="runsc"',
    'type=SYSCALL msg=audit(1:4): syscall=257 (openat) success=no exit=-13',
]
inc = OA.procesar_audit(m, "s1", "agente-x", log)
check("2a. 4 incidentes registrados", len(inc) == 4, inc)
check("2b. agente bloqueado", INC.agente_bloqueado(m, agent_id="agente-x") is True)
inc2 = OA.procesar_audit(m, "s1", "agente-x", log)
check("2c. idempotente", len(inc2) == 0)

print(f"\nRESULTADO: {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
