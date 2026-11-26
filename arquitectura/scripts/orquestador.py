#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""orquestador.py — objective↔task-pool BRIDGE (Fable 5.2 ruling: hybrid, option A).

Fable ruled NOT to unify `objetivo.py` with `tareas.py` (that would break the root of trust: the
objective's HMAC signature would cover mutable fields, and would change `objetivo_hash`). Instead,
a BRIDGE that:

  - freezes the PLAN: `plan_hash = H(canon({objetivo_hash, tareas, depends_on, merge_id}))` and
    records it in the journal BEFORE the first claim;
  - binds the evidence CHAIN: signature → objetivo_hash → plan_hash → tasks (by pinned version)
    → merge (resuelto_verificado, re-hashes the output);
  - derives the objective state (NEVER writes it): the objective is only RESOLVED if the merge is
    `resuelto_verificado` AND the deterministic check of the signed criterion passes over the
    merge output.

Candidate invariant for Lean 4:
    resuelto(obj) → firma_valida ∧ merge_verificado ∧ check(criterio, output_merge)
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import procedencia as P  # noqa: E402  (normalizar canónico, igual que objetivo.py)
import tareas as T       # noqa: E402
import objetivo as OBJ   # noqa: E402

# The plan: {"tareas": [{"id", "subobjetivo_idx", "descripcion", "depends_on"}], "merge_id": str}.
# `merge_id` is the SINK node (depends on ALL tasks): it is only ready when all of them close
# (resolved or failed), and its output is the deliverable validated against the criterion.


def _objetivo_hash(objetivo):
    import hashlib
    return hashlib.sha256(P.normalizar(objetivo).encode("utf-8")).hexdigest()


def _plan_hash(objetivo_hash, plan):
    import hashlib
    import json
    canon = json.dumps({"objetivo_hash": objetivo_hash, "plan": plan},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _marcador(m, oid):
    """Reads the plan marker (internal accounting record, unverified)."""
    f = m.current_raw(f"plan:{oid}")
    return (f or {}).get("value")


def _ciclo_plan(ids, ady):
    """Cycle detection (DFS with colors) over the plan's task sub-DAG."""
    estado = {}

    def visitar(n):
        estado[n] = 1
        for d in ady.get(n, []):
            if estado.get(d) == 1:
                return True
            if estado.get(d) == 0:
                if visitar(d):
                    return True
        estado[n] = 2
        return False

    for i in ids:
        estado.setdefault(i, 0)
        if estado[i] == 0 and visitar(i):
            return True
    return False


def _validar_plan(objetivo, plan):
    """Validates the plan BEFORE writing anything (all-or-nothing). Returns (tareas, merge_id, ids)."""
    subobjetivos = objetivo.get("subobjetivos") or []
    n_sub = len(subobjetivos)
    if not isinstance(plan, dict):
        raise ValueError("plan debe ser dict")
    tareas = plan.get("tareas")
    merge_id = plan.get("merge_id")
    if not isinstance(tareas, list) or not tareas:
        raise ValueError("plan.tareas debe ser lista no vacía")
    if not isinstance(merge_id, str) or not merge_id:
        raise ValueError("plan.merge_id requerido")
    ids = []
    for t in tareas:
        if not isinstance(t, dict):
            raise ValueError("cada tarea del plan debe ser dict")
        tid = t.get("id")
        if not isinstance(tid, str) or not tid:
            raise ValueError("tarea sin id")
        ids.append(tid)
        sidx = t.get("subobjetivo_idx")
        if not isinstance(sidx, int) or not (0 <= sidx < n_sub):
            raise ValueError(f"subobjetivo_idx inválido en {tid!r}")
        deps = t.get("depends_on") or []
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise ValueError(f"depends_on inválido en {tid!r}")
    if len(ids) != len(set(ids)):
        raise ValueError("ids de tareas duplicados")
    if merge_id in ids:
        raise ValueError("merge_id no puede ser una tarea")
    id_set = set(ids)
    for t in tareas:
        for d in (t.get("depends_on") or []):
            if d not in id_set:
                raise ValueError(f"dependencia inexistente: {d}")
    # coverage: each signed subobjective maps to at least one task
    cubiertos = {t["subobjetivo_idx"] for t in tareas}
    faltan = sorted(set(range(n_sub)) - cubiertos)
    if faltan:
        raise ValueError(f"subobjetivos sin cubrir: {faltan}")
    # acyclic
    ady = {t["id"]: (t.get("depends_on") or []) for t in tareas}
    if _ciclo_plan(ids, ady):
        raise ValueError("ciclo en el plan")
    return tareas, merge_id, ids


def descomponer(m, objetivo, firma, plan, session_id=None):
    """Decomposes a SIGNED OBJECTIVE into a task DAG of the pool. All-or-nothing and idempotent.

    Verifies the HMAC signature (reuses objetivo.verificar_objetivo), validates the plan
    (subobjective coverage, acyclic, existing deps, single sink), and only then registers the tasks
    + the merge. Each task is bound with `objetivo_hash`, `plan_hash` and `subobjetivo_idx`
    (evidence chain). Returns a descriptive dict, or None if the signature is invalid; raises
    ValueError if the plan is invalid or the objective was already decomposed with ANOTHER plan."""
    if not OBJ.verificar_objetivo(objetivo, firma):
        m.log_op("orquestador.descomponer.rechazo", key=objetivo.get("id"),
                 session_id=session_id, payload={"razon": "firma_invalida"})
        return None
    oid = objetivo.get("id")
    objetivo_hash = _objetivo_hash(objetivo)
    plan_hash = _plan_hash(objetivo_hash, plan)
    try:
        tareas, merge_id, ids = _validar_plan(objetivo, plan)
    except ValueError as e:
        m.log_op("orquestador.descomponer.rechazo", key=oid, session_id=session_id,
                 payload={"razon": str(e), "plan_hash": plan_hash})
        raise
    marcador = _marcador(m, oid)
    if marcador is not None:
        if marcador.get("plan_hash") == plan_hash:
            m.log_op("orquestador.descomponer.idempotente", key=oid, session_id=session_id,
                     payload={"plan_hash": plan_hash})
            return {"objetivo_id": oid, "objetivo_hash": objetivo_hash,
                    "plan_hash": plan_hash, "merge_id": marcador.get("merge_id"),
                    "tareas": marcador.get("tareas"), "idempotente": True}
        m.log_op("orquestador.descomponer.rechazo", key=oid, session_id=session_id,
                 payload={"razon": "plan_distinto", "vigente": marcador.get("plan_hash")})
        raise ValueError(f"objetivo {oid!r} ya descompuesto con otro plan")

    # validated before: registering cannot fail (unique ids, no cycles, merge_id does not collide)
    for t in tareas:
        T.registrar(m, t["id"], descripcion=t.get("descripcion", ""),
                    depends_on=t.get("depends_on") or [], fuente="orquestador",
                    session_id=session_id,
                    meta={"objetivo_hash": objetivo_hash, "plan_hash": plan_hash,
                          "subobjetivo_idx": t["subobjetivo_idx"]})
    T.registrar(m, merge_id, descripcion="merge", depends_on=ids, fuente="orquestador",
                session_id=session_id,
                meta={"objetivo_hash": objetivo_hash, "plan_hash": plan_hash,
                      "es_merge": True})
    m.write(f"plan:{oid}", {"objetivo_id": oid, "objetivo_hash": objetivo_hash,
                            "plan_hash": plan_hash, "merge_id": merge_id, "tareas": ids},
            unit="", fuente="monitor", verified=False, meta={"tipo": "plan"})
    m.log_op("orquestador.descomponer", key=oid, session_id=session_id,
             payload={"objetivo_hash": objetivo_hash, "plan_hash": plan_hash,
                      "merge_id": merge_id, "tareas": ids})
    return {"objetivo_id": oid, "objetivo_hash": objetivo_hash, "plan_hash": plan_hash,
            "merge_id": merge_id, "tareas": ids, "idempotente": False}


def _plan_desde_v2(objetivo):
    """Derives the plan (tasks + merge_id) from the SIGNED DAG in a schema v2 objective.

    In v2 the 'plan' is not proposed by the LLM: it is the objective itself (subobjectives with
    depends_on + merge_id), already frozen by the human HMAC signature. An LLM cannot decompose
    the objective badly: the DAG is immutable (any edit invalidates the signature)."""
    tareas = []
    for i, so in enumerate(objetivo.get("subobjetivos") or []):
        if isinstance(so, dict):
            so_id = so.get("id")
            deps = so.get("depends_on") or []
            desc = so.get("descripcion", "")
        else:
            so_id = so
            deps = []
            desc = ""
        tareas.append({"id": so_id, "subobjetivo_idx": i, "descripcion": desc,
                       "depends_on": deps})
    return {"tareas": tareas, "merge_id": objetivo.get("merge_id")}


def descomponer_v2(m, objetivo, firma, session_id=None):
    """Decomposes a SCHEMA v2 objective (subobjectives = signed task specs) into the pool.

    There is no separate 'plan': the DAG comes SIGNED in the objective. Derives the plan from the
    objective and delegates to `descomponer` (which verifies the signature, validates the DAG,
    registers tasks + merge, and freezes plan_hash = H({objetivo_hash, plan}))."""
    if objetivo.get("schema") != 2:
        m.log_op("orquestador.descomponer.rechazo", key=objetivo.get("id"),
                 session_id=session_id, payload={"razon": "schema_no_v2"})
        raise ValueError("descomponer_v2 requiere objetivo schema 2")
    return descomponer(m, objetivo, firma, _plan_desde_v2(objetivo), session_id=session_id)


def atar_evidencia_merge(m, objetivo, firma, merge_id):
    """Binds the merge evidence to EACH subobjective of the signed objective.

    Closes the evidence chain: the merge output (resuelto_verificado) is re-hashed and its hash is
    recorded as evidence bound to all subobjectives (via `objetivo.registrar_evidencia`). Returns
    the evidencia_hash, or None if the signature is invalid or the merge is not verified."""
    if not OBJ.verificar_objetivo(objetivo, firma):
        return None
    if not T.resuelto_verificado(m, merge_id):
        return None
    fact = T.resultado(m, merge_id)
    valor = fact.get("value") if isinstance(fact, dict) else None
    evidencia = T._hash_valor(valor)
    for so in (objetivo.get("subobjetivos") or []):
        so_id = so.get("id") if isinstance(so, dict) else so
        OBJ.registrar_evidencia(m, objetivo, firma, so_id, evidencia)
    return evidencia


def estado_objetivo_orquestado(m, objetivo, firma, verificador_criterio=None):
    """READ-ONLY: derives the objective state DELEGATING to `objetivo.estado_objetivo`.

    Builds a verifier that, for each subobjective, returns the merge OUTPUT as evidence and the
    result of `verificador_criterio` over that output. Thus closure requires the complete chain:
    signature → objetivo_hash → plan_hash → verified merge → bound evidence → deterministic
    criterion. RESOLVED only if all of that passes; the LLM never writes 'resuelto'."""
    if not OBJ.verificar_objetivo(objetivo, firma):
        return {"estado": OBJ.BLOQUEADO, "motivo": "firma inválida"}
    marcador = _marcador(m, objetivo.get("id"))
    if marcador is None:
        return {"estado": OBJ.ABIERTO, "motivo": "objetivo no descompuesto"}
    merge_id = marcador.get("merge_id")
    if not T.resuelto_verificado(m, merge_id):
        return {"estado": OBJ.EN_CURSO, "motivo": "merge no verificado"}
    fact = T.resultado(m, merge_id)
    valor = fact.get("value") if isinstance(fact, dict) else None
    evidencia = T._hash_valor(valor)

    def _verificador(so_id):
        paso = True
        if callable(verificador_criterio):
            paso = bool(verificador_criterio(valor))
        return {"paso": paso, "evidencia_hash": evidencia}

    return OBJ.estado_objetivo(m, objetivo, firma, _verificador)
