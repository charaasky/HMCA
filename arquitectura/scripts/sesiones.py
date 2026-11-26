#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""sesiones.py — agent session registry (`session_id` attribution).

Fable ruling (step 2, traceability): a `session_id` identifies NOBODY without a session registry.
Here the MONITOR opens/closes a session per connection —opening on accepting the socket, closing
on seeing the socket die— leaving `{inicio, fin, agent_id, modelo, lanzamiento}` in governed memory
under `sesion:<id>`.

The identity (agent_id/modelo/lanzamiento) is metadata the launcher can complete later; the
MINIMUM to attribute `session_id` is that the session exists with `inicio` and `fin`.

These facts are UNVERIFIED (internal accounting record, like evidence/nonces): read with
`current_raw`, never with `current` (they are not "trustworthy" facts in the HCMA sense).
"""
import os
import sys
import time

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

PREFIJO = "sesion:"


def _clave(session_id):
    return PREFIJO + session_id


def _ahora():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def abrir(m, session_id, agent_id=None, modelo=None, lanzamiento=None):
    """Opens (create-once) a session. Idempotent: if an open one already exists, it does NOT
    reopen it. Returns the fact version (the new or the existing one)."""
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id requerido")
    f = m.current_raw(_clave(session_id))
    if f is not None and not (f.get("value") or {}).get("fin"):
        return f["version"]  # already open
    valor = {
        "session_id": session_id,
        "inicio": _ahora(),
        "fin": None,
        "agent_id": agent_id,
        "modelo": modelo,
        "lanzamiento": lanzamiento,
    }
    return m.write(_clave(session_id), valor, unit="", fuente="monitor",
                   verified=False, meta={"tipo": "sesion"})


def cerrar(m, session_id, motivo=""):
    """Closes a session (sets `fin`). Idempotent: if already closed, it does not overwrite the
    original `fin`. Returns True if it closed it now; False if it did not exist or was already
    closed."""
    if not isinstance(session_id, str) or not session_id:
        return False
    f = m.current_raw(_clave(session_id))
    if f is None:
        return False
    valor = f.get("value") or {}
    if valor.get("fin"):
        return False  # already closed
    nuevo = dict(valor)
    nuevo["fin"] = _ahora()
    nuevo["motivo_cierre"] = motivo or ""
    m.write(_clave(session_id), nuevo, unit="", fuente="monitor",
            verified=False, meta={"tipo": "sesion"})
    return True


def datos(m, session_id):
    """Session fact (raw value) or None."""
    f = m.current_raw(_clave(session_id))
    return (f or {}).get("value")


def abierta(m, session_id):
    d = datos(m, session_id)
    return bool(d and not d.get("fin"))


def identificar(m, session_id, agent_id=None, modelo=None, lanzamiento=None):
    """Completes the identity of an ALREADY-OPEN session (metadata declared by the LAUNCHER).

    First-write-wins: does NOT overwrite an already-set identity (an agent cannot overwrite the
    identity the launcher declared). Returns True if it updated something; False if the session
    does not exist or there was nothing to complete."""
    if not isinstance(session_id, str) or not session_id:
        return False
    f = m.current_raw(_clave(session_id))
    if f is None:
        return False
    valor = f.get("value") or {}
    nuevo = dict(valor)
    cambio = False
    for campo, val in (("agent_id", agent_id), ("modelo", modelo), ("lanzamiento", lanzamiento)):
        if val is not None and not nuevo.get(campo):
            nuevo[campo] = val
            cambio = True
    if not cambio:
        return False
    m.write(_clave(session_id), nuevo, unit="", fuente="monitor",
            verified=False, meta={"tipo": "sesion"})
    return True
