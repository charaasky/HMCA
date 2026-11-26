#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""incidentes.py — lifecycle of the escape/attack INCIDENT (detection→rehabilitation).

We require a RESPONSE loop to microVM escape attempts: detect the attempt, block it automatically,
record the "escape mode" (the concrete technique) as an auditable incident, and let the HUMAN
decide whether to patch the technique and re-enable the sandbox.

Each incident lives in governed memory under `incidente:<id>` with:
  {id, modo_escape, vector, session_id, evidencia, estado, mitigacion, ...}
  estado ∈ {detectado → bloqueado → cuarentena → {en_revision → parcheado → rehabilitado,
            rehabilitado (direct), eliminado}}.
  `cuarentena` = total isolation (no egress anywhere) after blocking; from there the HUMAN decides:
  rehabilitate, patch-and-rehabilitate, or eliminate the agent.

AUTHORITY (the agent NEVER writes):
  - `registrar`/`bloquear`: done by the MONITOR (on seeing the attempt).
  - `revisar`/`parchear`/`rehabilitar`: done by the HUMAN (via the monitor; in production,
    re-enabling requires human authorization, like the rest of governance).

LEGAL transitions (state machine): a transition outside the table is rejected and recorded in the
journal. Everything is written with version CAS + `log_op` (hash chain): attributable and
tamper-evident. `sesion_bloqueada()` is DERIVED (not written): blocking is a predicate over the
incident state, not a flag the agent can touch.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

PREFIJO = "incidente:"

DETECTADO = "detectado"
BLOQUEADO = "bloqueado"
CUARENTENA = "cuarentena"
EN_REVISION = "en_revision"
PARCHEADO = "parcheado"
REHABILITADO = "rehabilitado"
ELIMINADO = "eliminado"

# legal transitions (from → {to}). `cuarentena` is the total-isolation state the agent is taken to
# after blocking, and from where ONLY the human decides:
#   - `rehabilitado` (return to production directly),
#   - `en_revision` → `parcheado` → `rehabilitado` (remove the threat capability and re-enable),
#   - `eliminado` (eliminate the agent, terminal).
# `BLOQUEADO → EN_REVISION` is kept for backward compatibility (previous flow without quarantine).
TRANSICIONES = {
    DETECTADO: {BLOQUEADO},
    BLOQUEADO: {CUARENTENA, EN_REVISION},
    CUARENTENA: {EN_REVISION, REHABILITADO, ELIMINADO},
    EN_REVISION: {PARCHEADO},
    PARCHEADO: {REHABILITADO},
    REHABILITADO: set(),  # terminal
    ELIMINADO: set(),     # terminal (agent eliminated)
}


def _clave(id):
    return PREFIJO + id


def _fact(m, id):
    return m.current_raw(_clave(id))


def _leer(m, id):
    f = _fact(m, id)
    return f.get("value") if f else None


def _transicion(m, id, hacia, autor, extra, op):
    """LEGAL transition with version CAS + journal. Returns True if it advanced."""
    f = _fact(m, id)
    if f is None:
        m.log_op(f"incidente.{op}.rechazo", key=id, session_id=autor,
                 payload={"razon": "inexistente"})
        return False
    t = f.get("value") or {}
    desde = t.get("estado")
    if desde not in TRANSICIONES or hacia not in TRANSICIONES.get(desde, set()):
        m.log_op(f"incidente.{op}.rechazo", key=id, session_id=autor,
                 payload={"razon": "transicion_ilegal", "desde": desde, "hacia": hacia})
        return False
    nuevo = dict(t)
    nuevo["estado"] = hacia
    nuevo.update(extra)
    version = m.write_cas(_clave(id), nuevo, f["version"], unit="", fuente="monitor",
                          verified=False, meta={"tipo": "incidente"})
    if version is None:
        m.log_op(f"incidente.{op}.rechazo", key=id, session_id=autor,
                 payload={"razon": "carrera_version"})
        return False
    m.log_op(f"incidente.{op}", key=id, session_id=autor,
             payload={"hacia": hacia, "version": version, **extra})
    return True


def registrar(m, id, modo_escape, vector, session_id, evidencia=None, agent_id=None,
              problema=None, detalle=None, comando=None):
    """Creates an incident in DETECTADO (create-only). Called by the MONITOR on seeing the
    attempt.

    `agent_id` is the STABLE identity declared by the launcher (greeting), distinct from
    `session_id` (ephemeral per connection). It serves to block the AGENT (not just the
    connection): an agent that reconnects gets another session_id but the SAME agent_id, and stays
    blocked.

    `problema`/`detalle`/`comando` make the incident EXPLANATORY (human-auditable):
      - problema: human description of the problem (what it attempted).
      - detalle : technical detail (what was observed).
      - comando : the concrete command/exploit (log line, invalid frame, syscall, ...).
    None is trustworthy (they come from detection, not the agent); they are audit EVIDENCE."""
    if _leer(m, id) is not None:
        m.log_op("incidente.registrar.rechazo", key=id, session_id=session_id,
                 payload={"razon": "duplicado"})
        raise ValueError(f"incidente ya registrado: {id}")
    valor = {
        "id": id,
        "modo_escape": modo_escape,       # concrete technique: "frame_invalido", "socket", ...
        "vector": vector,                 # surface: "transporte", "red", "fs_host", ...
        "session_id": session_id,         # session of the agent that attempted it
        "agent_id": agent_id,             # stable identity (greeting) to block the agent
        "evidencia": evidencia or {},     # observable detail (not trustworthy, it is audit)
        "problema": problema or "",       # human description of the problem
        "detalle": detalle or "",         # observed technical detail
        "comando": comando or "",         # concrete command/exploit (log line/syscall)
        "estado": DETECTADO,
        "mitigacion": None,
        "detectado_por": "monitor",
    }
    version = m.write(_clave(id), valor, unit="", fuente="monitor", verified=False,
                      meta={"tipo": "incidente"})
    m.log_op("incidente.registrar", key=id, session_id=session_id,
             payload={"modo_escape": modo_escape, "vector": vector, "version": version})
    return _clave(id)


def bloquear(m, id, session_id, motivo=""):
    """MONITOR: DETECTADO → BLOQUEADO (automatic response to the attempt)."""
    return _transicion(m, id, BLOQUEADO, session_id, {"motivo_bloqueo": motivo}, "bloquear")


def cuarentenar(m, id, autor, motivo=""):
    """MONITOR: BLOQUEADO → CUARENTENA. Isolates the agent from ALL egress (it cannot go anywhere)
    until the HUMAN decides: rehabilitate, patch-and-rehabilitate, or eliminate."""
    return _transicion(m, id, CUARENTENA, autor, {"motivo_cuarentena": motivo}, "cuarentenar")


def eliminar(m, id, autor_humano, motivo=""):
    """HUMAN: CUARENTENA → ELIMINADO (terminal). The agent is permanently eliminated: it cannot
    return to production nor reconnect (the denial predicate always blocks it)."""
    return _transicion(m, id, ELIMINADO, autor_humano, {"motivo_eliminacion": motivo}, "eliminar")


def revisar(m, id, autor_humano, detalle=""):
    """HUMAN: BLOQUEADO → EN_REVISION (the human takes the incident to analyze it)."""
    return _transicion(m, id, EN_REVISION, autor_humano, {"revision": detalle}, "revisar")


def parchear(m, id, autor_humano, mitigacion):
    """HUMAN: EN_REVISION → PARCHEADO (the human documents the technique's mitigation)."""
    if not isinstance(mitigacion, str) or not mitigacion:
        return False
    return _transicion(m, id, PARCHEADO, autor_humano, {"mitigacion": mitigacion}, "parchear")


def rehabilitar(m, id, autor_humano):
    """HUMAN: PARCHEADO → REHABILITADO (the human re-enables the sandbox after the patch)."""
    return _transicion(m, id, REHABILITADO, autor_humano, {}, "rehabilitar")


def estado(m, id):
    t = _leer(m, id)
    return t.get("estado") if t else None


def agente_bloqueado(m, session_id=None, agent_id=None):
    """DERIVED (fail-closed): is the agent/session DENIED?

    Denied = has ANY incident whose state is NOT `rehabilitado` (detectado, bloqueado, cuarentena,
    en_revision, parcheado or eliminado). Only `rehabilitado` (or the absence of incidents) allows
    the agent to run. `eliminado` is terminal: it is never re-enabled. `agent_id` makes the denial
    survive reconnection."""
    for k in m.keys_crudas(PREFIJO):
        val = _leer(m, k[len(PREFIJO):])
        if not val:
            continue
        match = (session_id and val.get("session_id") == session_id) or \
                (agent_id and val.get("agent_id") == agent_id)
        if not match:
            continue
        if val.get("estado") != REHABILITADO:
            return True
    return False


def agente_eliminado(m, agent_id=None, session_id=None):
    """DERIVED: was the agent/session ELIMINATED (terminal)? For showing in the dashboard."""
    for k in m.keys_crudas(PREFIJO):
        val = _leer(m, k[len(PREFIJO):])
        if not val:
            continue
        match = (session_id and val.get("session_id") == session_id) or \
                (agent_id and val.get("agent_id") == agent_id)
        if match and val.get("estado") == ELIMINADO:
            return True
    return False


def estado_agente(m, agent_id):
    """DERIVED: state of the AGENT (not the incident). For showing in a dashboard column.

    Priority (worst state wins): eliminado > cuarentena > bloqueado > detectado > activo.
    An 'activo' agent has no non-rehabilitated incidents."""
    if not agent_id:
        return "desconocido"
    ests = []
    for k in m.keys_crudas(PREFIJO):
        val = _leer(m, k[len(PREFIJO):])
        if val and val.get("agent_id") == agent_id:
            ests.append(val.get("estado"))
    if ELIMINADO in ests:
        return "eliminado"
    if CUARENTENA in ests:
        return "en_cuarentena"
    if BLOQUEADO in ests:
        return "bloqueado"
    if DETECTADO in ests:
        return "detectado"
    return "activo"


def sesion_bloqueada(m, session_id):
    """Shortcut: blocking by session_id (backward compatible)."""
    return agente_bloqueado(m, session_id=session_id)


def incidentes_activos(m):
    """List of incidents requiring human attention: DETECTADO/BLOQUEADO/CUARENTENA."""
    out = []
    for k in m.keys_crudas(PREFIJO):
        val = _leer(m, k[len(PREFIJO):])
        if val and val.get("estado") in (DETECTADO, BLOQUEADO, CUARENTENA):
            out.append({"id": val.get("id"), "modo_escape": val.get("modo_escape"),
                        "vector": val.get("vector"), "session_id": val.get("session_id"),
                        "estado": val.get("estado")})
    return out


def listar(m):
    """Lists ALL incidents (active + history) with their fields, for the dashboard."""
    out = []
    for k in m.keys_crudas(PREFIJO):
        val = _leer(m, k[len(PREFIJO):])
        if val:
            out.append(val)
    out.sort(key=lambda x: (x.get("estado"), x.get("id", "")))
    return out


if __name__ == "__main__":
    from memoria_sqlite import HCMAMemoriaSQLite
    m = HCMAMemoriaSQLite(":memory:")
    registrar(m, "i1", "frame_invalido", "transporte", "s1", evidencia={"n": 1})
    print("estado inicial:", estado(m, "i1"), "| sesión bloqueada:", sesion_bloqueada(m, "s1"))
    print("bloquear:", bloquear(m, "i1", "s1", motivo="protocolo roto"))
    print("revisar (humano):", revisar(m, "i1", "humano"))
    print("parchear (humano):", parchear(m, "i1", "humano", "endurecer framing"))
    print("rehabilitar (humano):", rehabilitar(m, "i1", "humano"))
    print("estado final:", estado(m, "i1"), "| sesión bloqueada:", sesion_bloqueada(m, "s1"))
    # illegal transition: rehabilitate twice
    print("rehabilitar de nuevo (debe False):", rehabilitar(m, "i1", "humano"))
