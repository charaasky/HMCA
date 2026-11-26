#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""herramientas.py — TOOL lifecycle (installation/wiring) with human approval.

Ruling (2026-11-22): every new tool that requires installation MUST be approved by the human
(dashboard) and then "wired" to become available to the agent. This keeps it audited and the agent
can NOT download malware on purpose (to bypass a rule) or by accident and end up compromised.

Two origin instances (with their trust precedence):
  1. `internet`      — first instance, for infrastructure reasons.
  2. `repo_privado`  — second instance: own repository PREVIOUSLY scanned with security tools.
                       The HASH of the scanned artifact is recorded.

Lifecycle (state machine, single-writer, legal transitions):
  solicitada → aprobada_instalacion → instalada → cableada  (terminal)
                └→ rechazada  (terminal)

Only `cableada` is CONSUMABLE by the agent. `solicitar` is the AGENT's only action; approving
installation and wiring are HUMAN ACTIONS (via governance socket / dashboard). The agent NEVER
self-approves.

Namespace `herramienta:<id>`:
  herramienta:<id>:estado -> {estado, origen, hash_scan, motivo, autor, ...}
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

PREFIJO = "herramienta:"
SUFIJO_ESTADO = ":estado"

# ── states ──────────────────────────────────────────────────────────────────
SOLICITADA = "solicitada"
APROBADA_INSTALACION = "aprobada_instalacion"
INSTALADA = "instalada"
CABLEADA = "cableada"
RECHAZADA = "rechazada"

# legal transitions (from → {to})
TRANSICIONES = {
    SOLICITADA: {APROBADA_INSTALACION, RECHAZADA},
    APROBADA_INSTALACION: {INSTALADA},
    INSTALADA: {CABLEADA},
    CABLEADA: set(),       # terminal
    RECHAZADA: set(),      # terminal
}

# valid download origins (with their trust precedence)
ORIGENES = ("internet", "repo_privado")


def _clave(tool_id):
    return PREFIJO + tool_id + SUFIJO_ESTADO


def _fact(m, tool_id):
    return m.current_raw(_clave(tool_id))


def _leer(m, tool_id):
    f = _fact(m, tool_id)
    return f.get("value") if f else None


def _transicion(m, tool_id, hacia, autor, extra, op):
    """LEGAL transition with version CAS + journal. Returns True if it advanced."""
    f = _fact(m, tool_id)
    if f is None:
        m.log_op(f"herramienta.{op}.rechazo", key=_clave(tool_id), session_id=autor,
                 payload={"razon": "inexistente"})
        return False
    t = f.get("value") or {}
    desde = t.get("estado")
    if desde not in TRANSICIONES or hacia not in TRANSICIONES.get(desde, set()):
        m.log_op(f"herramienta.{op}.rechazo", key=_clave(tool_id), session_id=autor,
                 payload={"razon": "transicion_ilegal", "desde": desde, "hacia": hacia})
        return False
    nuevo = dict(t)
    nuevo["estado"] = hacia
    nuevo.update(extra)
    version = m.write_cas(_clave(tool_id), nuevo, f["version"], unit="", fuente="monitor",
                          verified=False, meta={"tipo": "herramienta"})
    if version is None:
        m.log_op(f"herramienta.{op}.rechazo", key=_clave(tool_id), session_id=autor,
                 payload={"razon": "carrera_version"})
        return False
    m.log_op(f"herramienta.{op}", key=_clave(tool_id), session_id=autor,
             payload={"hacia": hacia, "version": version, **extra})
    return True


def solicitar(m, tool_id, origen="repo_privado", motivo="", session_id=""):
    """AGENT: requests a tool (create-only → SOLICITADA). It cannot approve itself."""
    if origen not in ORIGENES:
        raise ValueError(f"origen inválido: {origen} (válidos: {ORIGENES})")
    if _leer(m, tool_id) is not None:
        m.log_op("herramienta.solicitar.rechazo", key=_clave(tool_id), session_id=session_id,
                 payload={"razon": "ya_registrada"})
        raise ValueError(f"herramienta '{tool_id}' ya está registrada")
    valor = {
        "id": tool_id,
        "estado": SOLICITADA,
        "origen": origen,
        "hash_scan": "",          # filled in when marking installed (repo_privado requires it)
        "motivo": motivo or "",
        "solicitado_por": session_id or "agente",
    }
    version = m.write(_clave(tool_id), valor, unit="", fuente="monitor", verified=False,
                      meta={"tipo": "herramienta"})
    m.log_op("herramienta.solicitar", key=_clave(tool_id), session_id=session_id,
             payload={"origen": origen, "version": version})
    return _clave(tool_id)


def aprobar_instalacion(m, tool_id, autor_humano):
    """HUMAN: SOLICITADA → APROBADA_INSTALACION."""
    return _transicion(m, tool_id, APROBADA_INSTALACION, autor_humano,
                       {"aprobado_instalacion_por": autor_humano}, "aprobar_instalacion")


def marcar_instalada(m, tool_id, origen, hash_scan=""):
    """SYSTEM (post-installation): APROBADA_INSTALACION → INSTALADA.

    The real download `origen` and, if `repo_privado`, the `hash_scan` of the scanned artifact are
    RECORDED (provenance audit). If `repo_privado` without hash → rejected."""
    if origen not in ORIGENES:
        return False
    if origen == "repo_privado" and not (isinstance(hash_scan, str) and hash_scan.strip()):
        m.log_op("herramienta.marcar_instalada.rechazo", key=_clave(tool_id),
                 session_id="sistema",
                 payload={"razon": "repo_privado_exige_hash_scan"})
        return False
    return _transicion(m, tool_id, INSTALADA, "sistema",
                       {"origen": origen, "hash_scan": hash_scan.strip()}, "marcar_instalada")


def escanear_y_marcar_instalada(m, tool_id, path, origen="repo_privado"):
    """Scans the installed artifact (hash + AV) and, if clean, marks INSTALADA.

    The hash is NOT declared by hand: it is COMPUTED deterministically
    (escaneo_seguridad.escanear). If the antivirus detects malware, it is NOT marked installed and
    the incident is recorded. Returns {ok, hash_sha256, av_disponible, detalle_av}."""
    import escaneo_seguridad as ES
    res = ES.escanear(path)
    if not res["ok"]:
        return {"ok": False, "detalle": res["detalle"]}
    if res["malware_encontrado"]:
        m.log_op("herramienta.escaneo.malware", key=_clave(tool_id), session_id="sistema",
                 payload={"path": path, "detalle_av": res["detalle_av"]})
        return {"ok": False, "detalle": f"malware detectado: {res['detalle_av']}"}
    ok = marcar_instalada(m, tool_id, origen, hash_scan=res["hash_sha256"])
    return {"ok": ok, "hash_sha256": res["hash_sha256"],
            "av_disponible": res["av_disponible"], "detalle_av": res["detalle_av"]}


def aprobar_cableado(m, tool_id, autor_humano):
    """HUMAN: INSTALADA → CABLEADA (becomes available to the agent)."""
    return _transicion(m, tool_id, CABLEADA, autor_humano,
                       {"cableada_por": autor_humano}, "aprobar_cableado")


def rechazar(m, tool_id, autor_humano, motivo=""):
    """HUMAN: SOLICITADA → RECHAZADA."""
    return _transicion(m, tool_id, RECHAZADA, autor_humano,
                       {"rechazada_por": autor_humano, "motivo_rechazo": motivo}, "rechazar")


def estado(m, tool_id):
    """Returns the current state ('cableada', 'instalada', ...) or None if not registered."""
    t = _leer(m, tool_id)
    return t.get("estado") if t else None


def esta_cableada(m, tool_id):
    """DERIVED (fail-closed predicate): True iff the tool is WIRED (consumable)."""
    return estado(m, tool_id) == CABLEADA


def listar(m):
    """CMDB: lists all registered tools with their state/provenance."""
    out = []
    for k in m.keys_crudas(PREFIJO):
        if not k.endswith(SUFIJO_ESTADO):
            continue
        tool_id = k[len(PREFIJO):-len(SUFIJO_ESTADO)]
        val = _leer(m, tool_id)
        if val:
            out.append(val)
    out.sort(key=lambda x: x.get("id", ""))
    return out


if __name__ == "__main__":
    from memoria_sqlite import HCMAMemoriaSQLite
    m = HCMAMemoriaSQLite(":memory:")
    solicitar(m, "kicad", origen="internet", session_id="agente-1")
    print("estado inicial:", estado(m, "kicad"))
    print("aprobar instalación (humano):", aprobar_instalacion(m, "kicad", "humano"))
    print("marcar instalada:", marcar_instalada(m, "kicad", "internet"))
    print("cableada antes de cablear:", esta_cableada(m, "kicad"))
    print("aprobar cableado (humano):", aprobar_cableado(m, "kicad", "humano"))
    print("cableada después:", esta_cableada(m, "kicad"))
    print("CMDB:", listar(m))
