#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""deontica.py — DEONTIC layer over HCMA: obligations and deliverables (closure).

Design validated by Fable 5.2 (query `reapertura_deontica`): separates the NORM from the
EVIDENCE so that epistemic invalidation does not drag down the obligation (previous
quantification bug). Previous model in POSTMORTEM_skills.md §7duodecies, hardened with the
R/V/A invariants (cierre_deontico_drift) and now with M/L/S (reapertura).

TWO FACTS (instead of one):
  - `obligacion:<id>`  = the NORM (what is owed, to whom, with what requirements). It has NO
                         `derived_from` -> it is immune to `_invalidate_descendants`. It lives in the
                         O domain, monotonic.
  - `entregable:<id>`  = the CLOSURE (evidence that the obligation was settled). It derives from the
                         support (the requirements) -> it can lose authority if the support falls,
                         and it is right that it loses it.

STATE = DERIVED VIEW (`estado(m, id)`):
    satisfecho  ⟺ current(entregable:<id>) has authority AND every requirement valid(r,g) NOW.
    bloqueado   ⟺ there is a requirement with authority < required.
    pendiente   ⟺ otherwise.
There is no `estado` field persisted as such: the closure writes `satisfecho` only via
`cerrar_si_predicado`, and validity is re-evaluated in the present.

Invariants:
  R (faithful report)   return = read state after the write.
  V (single view)    the predicate and the lineage read the same view (`current`).
  A (bounded authority)  authority(closure) ≤ min authority(requirements); close only if valid.
  M (monotonic domain)   derived_from(obligacion:id) = ∅ → id does not leave O.
  L (complete lineage)    every `satisfecho` derives from {requisitos} ∪ {obligacion:id}.
  S (no false closure)    ¬sat(id) ⇒ id ∈ pendientes; corollary: if an ancestor of the closure
                         loses authority, completo() is False at that same instant.

Golden deontic rule (dual of the epistemic one):
  - Epistemic: "the unverified never self-promotes".
  - Deontic:   "the pending never self-closes".

USAGE (public interface, compatible):
    registrar_entregable(m, id, tipo, meta, predicado_ref, requiere=[(r,g),...])
    cerrar_si_predicado(m, id, predicado_fn) -> (estado, motivo)
    estado(m, id) -> dict {id, estado, motivo, caidos}
    pendientes(m) -> dict {clave: info}      # only non-satisfied
    completo(m) -> bool
"""
import linaje


OBL = "obligacion:"
ENT = "entregable:"
SUSTENTO = "sustento_invalidado"

ESTADOS = ("pendiente", "bloqueado", "satisfecho")
_ORDEN = {"canonico": 3, "conforme": 2, "desconocido": 1}


def _grado(m, key, version):
    """Computed degree of the lineage (never declared)."""
    try:
        return linaje.grado(m, key, version)
    except Exception:
        return "desconocido"


def _valido(m, r, g):
    """Invariant A: current(r) != None and degree ≥ g."""
    if g is None:
        g = "conforme"
    f = m.current(r)
    if f is None:
        return False
    return _ORDEN.get(_grado(m, r, f["version"]), 1) >= _ORDEN.get(g, 2)


def _requiere_normalizado(requiere):
    return [(item[0] if isinstance(item, (tuple, list)) else item,
             item[1] if isinstance(item, (tuple, list)) and len(item) >= 2 else None)
            for item in (requiere or [])]


def registrar_entregable(m, id, tipo, meta, predicado_ref, requiere=None):
    """Creates the OBLIGATION (norm) and the initial CLOSURE. I-1/monotonic.

    `requiere` cannot be empty: a goal without a sufficiency criterion."""
    requiere = requiere or []
    if not requiere:
        raise ValueError("entregable sin 'requiere': meta sin criterio de suficiencia (I-1)")
    if m.current(OBL + id) is not None:
        raise ValueError(f"obligación ya registrada: {id}")

    req = [list(x) if isinstance(x, (tuple, list)) else [x, None] for x in requiere]

    # 1) the norm: no derived_from (M)
    v_obl = m.write(OBL + id, {"tipo": tipo, "meta": meta, "predicado_ref": predicado_ref,
                               "requiere": req},
                    unit="", fuente="usuario", verified=True)

    # 2) the initial closure: derives ONLY from the norm (so it is not orphaned), pending state.
    m.write(ENT + id, {"estado": "pendiente", "motivo": None},
            unit="", fuente="usuario", verified=True,
            derived_from={OBL + id: v_obl})

    return estado(m, id)


def estado(m, id):
    """Effective deontic view. ONLY legitimate read of a deliverable's state."""
    o = m.current(OBL + id)
    if o is None:
        raise KeyError(f"obligación no registrada: {id}")
    requiere = _requiere_normalizado(o["value"].get("requiere", []))
    caidos = [r for r, g in requiere if not _valido(m, r, g)]

    e = m.current(ENT + id)
    if e is not None and e["value"].get("estado") == "satisfecho" and not caidos:
        return {"id": id, "estado": "satisfecho", "motivo": None, "caidos": []}
    cayo = (e is None or e["value"].get("estado") == "satisfecho"
            or (e["value"].get("motivo") or "") == SUSTENTO)
    return {
        "id": id,
        "estado": "bloqueado" if caidos else "pendiente",
        "motivo": SUSTENTO if cayo else (e["value"].get("motivo") if e else None),
        "caidos": caidos,
    }


def pendientes(m):
    """Unsatisfied deliverables. Iterates the O domain (monotonic), NOT keys('entregable:')."""
    out = {}
    for k in m.keys(OBL):
        id = k[len(OBL):]
        s = estado(m, id)
        if s["estado"] != "satisfecho":
            out[ENT + id] = {"tipo": _tipo(m, id), "estado": s["estado"],
                             "motivo": s["motivo"], "caidos": s["caidos"]}
    return out


def _tipo(m, id):
    o = m.current(OBL + id)
    return o["value"].get("tipo") if o else None


def completo(m):
    """True iff there are NO deliverables with a non-satisfied state (derived view)."""
    return len(pendientes(m)) == 0


def cerrar_si_predicado(m, id, predicado_fn):
    """Executes the mechanical predicate under R/V/A. Only writer of 'satisfecho'.

    Returns (estado_efectivo, motivo). `predicado_fn(m)` receives the memory (`current` view);
    it is an ADDITIONAL condition, never a substitute for the gate (A).
    """
    o = m.current(OBL + id)
    if o is None:
        raise KeyError(f"obligación no registrada: {id}")

    s = estado(m, id)
    if s["estado"] == "satisfecho":
        return s["estado"], s["motivo"]          # idempotent

    requiere = _requiere_normalizado(o["value"].get("requiere", []))

    # ── A: pre-check requirements BEFORE running fn or writing ──
    for r, g in requiere:
        if not _valido(m, r, g):
            return "pendiente", f"bloqueado: requisito '{r}' sin autoridad o grado insuficiente"

    # ── V: the predicate is an ADDITIONAL condition ──
    ok, motivo = predicado_fn(m)
    if not ok:
        return _transicionar(m, id, o, "bloqueado", motivo)

    # ── L: closure lineage = {requisitos: version} ∪ {obligacion:id: version} ──
    derived_from = {}
    for r, _ in requiere:
        f = m.current(r)
        if f is None:
            raise RuntimeError(
                f"Inconsistencia de estado: requisito '{r}' vigente en pre-check, ausente al escribir")
        derived_from[r] = f["version"]
    derived_from[OBL + id] = o["version"]

    m.write(ENT + id, {"estado": "satisfecho", "motivo": None},
            unit="", fuente="calculadora", verified=True,
            derived_from=derived_from)

    # ── R: return = REAL post-write state ──
    return estado(m, id)["estado"], motivo


def _transicionar(m, id, o, estado, motivo):
    """Transitions the closure to 'bloqueado' (failed predicate). Derives from the norm alone."""
    m.write(ENT + id, {"estado": estado, "motivo": motivo},
            unit="", fuente="calculadora", verified=True,
            derived_from={OBL + id: o["version"]})
    return estado, motivo


def reabrir_invalidados(m):
    """OPTIONAL (audit): materializes the reopening to pending (idempotent).

    The correctness of completo() does NOT depend on calling it: `estado()` is already a present view.
    Useful to audit which deliverables lost support."""
    out = []
    for k in m.keys(OBL):
        id = k[len(OBL):]
        if m.current(ENT + id) is None:
            s = estado(m, id)
            m.write(ENT + id, {"estado": "pendiente", "motivo": SUSTENTO, "caidos": s["caidos"]},
                    unit="", fuente="calculadora", verified=True,
                    derived_from={OBL + id: m.current(OBL + id)["version"]})
            out.append(id)
    return out
