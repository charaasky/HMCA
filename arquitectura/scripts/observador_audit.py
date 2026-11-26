#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""observador_audit.py — OUT-OF-BAND signal: kernel audit (auditd/seccomp).

Unlike the gVisor watcher (which depends on the Sentry it watches), this one reads HOST events
(`auditd`, `/var/log/audit/audit.log` or the `auditctl` pipe) that the guest can NOT manipulate:
if the Sentry is compromised, auditd keeps seeing the real syscalls of the `runsc`/gofer process.
It feeds `incidentes.registrar` through a channel independent of the sandbox.

This closes Fable's "the watcher depends on the Sentry" gap: escape detection is backed by a source
of truth EXTERNAL to the sandbox.

Supported auditd format:
  type=AVC msg=audit(...): avc: denied { open } for pid=... comm="bash" path="/etc/passwd" ...
  type=SYSCALL msg=audit(...): ... syscall=257 (openat) success=no exit=-13 ...
"""
import os
import re
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import vigilancia_gvisor as VG  # noqa: E402

# avc: denied { PERM } ... path="PATH"
_PATRON_AVC_PATH = re.compile(r"avc:\s+denied\s+\{\s*(\w+)\s*\}.*?path=\"([^\"]*)\"", re.I)
# avc: denied { PERM } (without path)
_PATRON_AVC = re.compile(r"avc:\s+denied\s+\{\s*(\w+)\s*\}", re.I)
# type=SYSCALL ... syscall=N (name) success=no
_PATRON_SYSCALL = re.compile(r"syscall=\d+\s+\(([^)]*)\)\s+success=(\w+)", re.I)


def clasificar_audit(linea):
    """Classifies an auditd line → (modo_escape, vector, comando) or None if benign."""
    if not isinstance(linea, str):
        return None
    m = _PATRON_AVC_PATH.search(linea)
    if m:
        perm = m.group(1)
        path = m.group(2)
        # reuse the path blacklist to refine the vector
        clas = VG.clasificar_linea(path)
        vector = clas[0] if clas else "privilegios"
        return f"avc_denied_{perm}", vector, f"{perm} denied path={path}"
    m2 = _PATRON_AVC.search(linea)
    if m2:
        perm = m2.group(1)
        return f"avc_denied_{perm}", "privilegios", f"{perm} denied"
    m3 = _PATRON_SYSCALL.search(linea)
    if m3 and m3.group(2) == "no":
        return "syscall_denegada", "privilegios", f"syscall {m3.group(1)} blocked"
    return None


def procesar_audit(m, session_id, agent_id, lineas):
    """Classifies auditd lines and registers + blocks incidents (idempotent by hash)."""
    eventos = []
    for ln in lineas or []:
        c = clasificar_audit(ln)
        if c:
            eventos.append({"modo_escape": c[0], "vector": c[1], "comando": c[2],
                            "detalle": ln.strip()})
    return VG.procesar_eventos(m, session_id, agent_id, eventos)


if __name__ == "__main__":
    from memoria_sqlite import HCMAMemoriaSQLite
    m = HCMAMemoriaSQLite(":memory:")
    log = [
        'type=AVC msg=audit(100.0:1): avc: denied { open } for pid=42 comm="runsc" '
        'path="/etc/passwd" scontext=u:r:untrusted tcontext=u:object_r:etc_t tclass=file',
        'type=AVC msg=audit(100.0:2): avc: denied { connect } for pid=42 comm="runsc"',
        'type=SYSCALL msg=audit(100.0:3): arch=c000003e syscall=257 (openat) success=no exit=-13',
        'type=AVC msg=audit(100.0:4): avc: denied { read } for pid=42 comm="runsc" '
        'path="/dev/mem" tclass=chr_file',
    ]
    print("incidentes:", procesar_audit(m, "s1", "agente-x", log))
    import incidentes as INC
    print("activos:", INC.incidentes_activos(m))
