#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""tareas.py — TASK POOL with dependencies (DAG) + atomic claim.

Minimal core for "N concurrent agents take tasks from a pool". Answers the design question: "does
each agent take whichever it wants, or in order if it must". Answer: an agent can take any READY task
(dependencies resolved); the "in order" is imposed by the `depends_on` DAG (topological order), not
a FIFO queue.

Each task lives in governed memory under `tarea:<id>` with:
  {id, descripcion, estado, depends_on, agent_id}
  estado ∈ {pendiente, en_curso, resuelto, fallida}.

ATOMICITY INVARIANT: `tomar()` does NOT use a lock; the race "two agents take the same task" is
resolved because the only one that runs `tomar()` is the MONITOR (single-writer, one process/
thread). The read-check-write of `tomar()` is atomic WITHIN a monitor op. Outside that invariant,
`tomar()` is not thread-safe (it must be serialized, which is exactly what the monitor does).

Fable 5.2 ruling (5 steps) — implemented state:
  1. ownership + legal transitions + CAS + cycle          ✅
  2. traceability: journal (`log_op`) + session registry ✅ (sesiones.py)
  3. evidence: `resolver` requires resultado_ref + hash; `fallar` as a 1st-class result ✅
  4. reclaim: `liberar_sesion` (disconnection) + fencing by claim version ✅
  5. data flow: `inputs` (dep results by pinned version); merge = sink node ✅
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

PENDIENTE = "pendiente"
EN_CURSO = "en_curso"
RESUELTO = "resuelto"
FALLIDA = "fallida"

PREFIJO = "tarea:"


def _clave(id):
    return PREFIJO + id


def _fact(m, id):
    """Full fact (value + version) of the task, or None. The version feeds the CAS."""
    return m.current_raw(_clave(id))


def _leer(m, id):
    """Reads the task's raw value (internal bookkeeping: does not filter by authority)."""
    f = _fact(m, id)
    return f.get("value") if f else None


def _escribir(m, id, valor, fuente):
    return m.write(_clave(id), valor, unit="", fuente=fuente, verified=False,
                   meta={"tipo": "tarea"})


def _escribir_cas(m, id, valor, expected_version, fuente):
    """Version CAS (Fable ruling): writes ONLY if the version did not change since reading.
    Returns the new version, or None if it lost the race."""
    return m.write_cas(_clave(id), valor, expected_version, unit="", fuente=fuente,
                       verified=False, meta={"tipo": "tarea"})


def registrar(m, id, descripcion="", depends_on=(), fuente="orquestador", session_id=None,
              meta=None):
    """Registers a PENDIENTE task. Rejects a duplicate id or if it introduces a CYCLE in the DAG.

    `depends_on` = ids of tasks that must be RESOLVED before this one is claimable. Allows forward
    references (a not-yet-registered dep); the cycle is detected when registering the dep (when its
    `depends_on` points back to this task).

    `meta` (optional dict) is merged into the value (e.g. the orchestrator binds `objetivo_hash`,
    `plan_hash`, `subobjetivo_idx` for the objective→plan→task evidence chain).

    Fable ruling (step 2, traceability): each op —including REJECTION for duplicate/cycle— is
    recorded in the journal (`log_op`) with its `session_id`. `session_id` is injected by the
    monitor; `registered_by` falls back to `fuente` if there is no session (backward
    compatibility)."""
    if _leer(m, id) is not None:
        m.log_op("tarea.registrar.rechazo", key=id, session_id=session_id,
                 payload={"razon": "duplicado"})
        raise ValueError(f"tarea ya registrada: {id}")
    deps = list(depends_on)
    if _ciclo(m, id, deps):
        m.log_op("tarea.registrar.rechazo", key=id, session_id=session_id,
                 payload={"razon": "ciclo", "depends_on": deps})
        raise ValueError(f"ciclo detectado al registrar {id} (un dep depende transitivamente de {id})")
    registered_by = session_id or fuente
    valor = {"id": id, "descripcion": descripcion, "estado": PENDIENTE,
             "depends_on": deps, "agent_id": None, "registered_by": registered_by}
    if meta:
        valor.update(meta)
    version = _escribir(m, id, valor, fuente)
    m.log_op("tarea.registrar", key=id, session_id=session_id,
             payload={"descripcion": descripcion, "depends_on": deps,
                      "registered_by": registered_by, "version": version})
    return _clave(id)


def _ciclo(m, nuevo_id, deps):
    """Does any of `deps` depend (transitively) on `nuevo_id`? DFS over `depends_on`."""
    pila = list(deps)
    visitados = set()
    while pila:
        d = pila.pop()
        if d == nuevo_id:
            return True
        if d in visitados:
            continue
        visitados.add(d)
        t = _leer(m, d)
        if t:
            pila.extend(t.get("depends_on", []))
    return False


def estado(m, id):
    """Current task state (or None if it does not exist)."""
    t = _leer(m, id)
    return t.get("estado") if t else None


def ready(m, id):
    """Is the task claimable? = pendiente AND all its dependencies in TERMINAL state (resolved or
    failed). A failed dependency does NOT hang the DAG: the dependent can proceed and consult
    `dependencias_resueltas` to know which one failed."""
    t = _leer(m, id)
    if t is None or t.get("estado") != PENDIENTE:
        return False
    return all((_leer(m, d) or {}).get("estado") in (RESUELTO, FALLIDA)
               for d in t.get("depends_on", []))


def listas_ready(m, ids=None):
    """Ids of tasks ready to claim (pendiente + resolved deps)."""
    if ids is None:
        ids = [k[len(PREFIJO):] for k in m.keys_crudas(PREFIJO)]
    return [i for i in ids if ready(m, i)]


def tomar(m, id, session_id):
    """Atomic claim: pendiente+ready → en_curso, with version CAS.

    Returns True if THIS caller won the task; False if it was already claimed, resolved, not-ready
    or lost the version race. `session_id` is the identity the monitor assigns. Each outcome (won
    or rejection) is recorded in the journal (`log_op`, Fable ruling step 2)."""
    f = _fact(m, id)
    if f is None:
        m.log_op("tarea.tomar.rechazo", key=id, session_id=session_id,
                 payload={"razon": "inexistente"})
        return False
    t = f.get("value")
    if t.get("estado") != PENDIENTE:
        m.log_op("tarea.tomar.rechazo", key=id, session_id=session_id,
                 payload={"razon": "estado", "estado": t.get("estado")})
        return False
    if not ready(m, id):
        m.log_op("tarea.tomar.rechazo", key=id, session_id=session_id,
                 payload={"razon": "no_ready", "depends_on": t.get("depends_on", [])})
        return False
    t = dict(t)
    t["estado"] = EN_CURSO
    t["agent_id"] = session_id
    version = _escribir_cas(m, id, t, f["version"], "monitor")
    if version is None:
        m.log_op("tarea.tomar.rechazo", key=id, session_id=session_id,
                 payload={"razon": "carrera_version"})
        return False
    m.log_op("tarea.tomar", key=id, session_id=session_id,
             payload={"agent_id": session_id, "version": version})
    return True


def resolver(m, id, session_id, resultado_ref, evidencia_hash):
    """Marks RESUELTO with EVIDENCE (Fable ruling step 3). OWNERSHIP + CAS.

    `resultado_ref` = {key, version} of a fact in governed memory (the task OUTPUT).
    `evidencia_hash` = canonical hash of that output (captured by the orchestrator, not the agent).
    Rejects if there is no result/evidence (anti 'false resolved' that propagates through the DAG).
    Each outcome is recorded in the journal (`log_op`, Fable ruling step 2)."""
    def _rechazo(razon, **extra):
        m.log_op("tarea.resolver.rechazo", key=id, session_id=session_id,
                 payload={"razon": razon, **extra})
        return False
    if not isinstance(resultado_ref, dict) or "key" not in resultado_ref \
            or "version" not in resultado_ref:
        return _rechazo("resultado_ref_invalido")
    if not isinstance(evidencia_hash, str) or not evidencia_hash:
        return _rechazo("evidencia_faltante")
    f = _fact(m, id)
    if f is None:
        return _rechazo("inexistente")
    t = f.get("value")
    if t.get("estado") != EN_CURSO:
        return _rechazo("estado", estado=t.get("estado"))
    if t.get("agent_id") != session_id:
        return _rechazo("no_dueno", agent_id=t.get("agent_id"))
    t = dict(t)
    t["estado"] = RESUELTO
    t["agent_id"] = None
    t["resolved_by"] = session_id
    t["resultado_ref"] = resultado_ref
    t["evidencia_hash"] = evidencia_hash
    version = _escribir_cas(m, id, t, f["version"], "monitor")
    if version is None:
        return _rechazo("carrera_version")
    m.log_op("tarea.resolver", key=id, session_id=session_id,
             payload={"resultado_ref": resultado_ref, "evidencia_hash": evidencia_hash,
                      "resolved_by": session_id, "version": version})
    return True


def fallar(m, id, session_id, motivo=""):
    """Marks FALLIDA (a failure is a FIRST-CLASS result, not a lying 'resuelto').
    OWNERSHIP + CAS. Unblocks dependents like resolved (avoids hanging the DAG). Each outcome is
    recorded in the journal (`log_op`, Fable ruling step 2)."""
    def _rechazo(razon, **extra):
        m.log_op("tarea.fallar.rechazo", key=id, session_id=session_id,
                 payload={"razon": razon, **extra})
        return False
    f = _fact(m, id)
    if f is None:
        return _rechazo("inexistente")
    t = f.get("value")
    if t.get("estado") != EN_CURSO:
        return _rechazo("estado", estado=t.get("estado"))
    if t.get("agent_id") != session_id:
        return _rechazo("no_dueno", agent_id=t.get("agent_id"))
    t = dict(t)
    t["estado"] = FALLIDA
    t["agent_id"] = None
    t["resolved_by"] = session_id
    t["motivo"] = motivo
    version = _escribir_cas(m, id, t, f["version"], "monitor")
    if version is None:
        return _rechazo("carrera_version")
    m.log_op("tarea.fallar", key=id, session_id=session_id,
             payload={"resolved_by": session_id, "motivo": motivo, "version": version})
    return True


def liberar_sesion(m, session_id, motivo="desconexion"):
    """RECLAIM (Fable ruling step 4): returns the EN_CURSO tasks of `session_id` to PENDIENTE.

    Called when the monitor sees an agent socket die (lazy lease: evaluated at the disconnection
    event, NOT with timers). Each task returns to `pendiente` with an explicit reason.

    FENCING = the claim version: the transition uses version CAS, so a zombie retaining the old
    claim version can NOT resolve after the reclaim (its CAS fails: the version already advanced).
    Returns the list of released ids."""
    liberadas = []
    for id in [k[len(PREFIJO):] for k in m.keys_crudas(PREFIJO)]:
        f = _fact(m, id)
        if f is None:
            continue
        t = f.get("value")
        if not t or t.get("estado") != EN_CURSO or t.get("agent_id") != session_id:
            continue
        nuevo = dict(t)
        nuevo["estado"] = PENDIENTE
        nuevo["agent_id"] = None
        nuevo["reclaimed_by"] = session_id
        nuevo["motivo_reclaim"] = motivo
        version = _escribir_cas(m, id, nuevo, f["version"], "monitor")
        if version is None:
            m.log_op("tarea.reclaim.rechazo", key=id, session_id=session_id,
                     payload={"razon": "carrera_version"})
            continue
        m.log_op("tarea.reclaim", key=id, session_id=session_id,
                 payload={"motivo": motivo, "version": version})
        liberadas.append(id)
    return liberadas


def _hash_valor(valor):
    import hashlib
    import json
    canon = json.dumps(valor, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def resultado(m, id):
    """Reads the OUTPUT of a resolved task (the fact resultado_ref points to), or None."""
    t = _leer(m, id)
    if not t or t.get("estado") != RESUELTO:
        return None
    ref = t.get("resultado_ref")
    if not isinstance(ref, dict):
        return None
    return m.get_version(ref.get("key"), ref.get("version"))


def resuelto_verificado(m, id):
    """DERIVED (not stored, Fable ruling): a resolved task is VERIFIED only if its resultado_ref
    points to an existing fact whose value hashes to the recorded evidencia_hash. The agent does
    not write 'resuelto'; here it is CHECKED against the bound evidence."""
    t = _leer(m, id)
    if not t or t.get("estado") != RESUELTO:
        return False
    ref = t.get("resultado_ref")
    if not isinstance(ref, dict):
        return False
    f = m.get_version(ref.get("key"), ref.get("version"))
    if f is None:
        return False
    return _hash_valor(f.get("value")) == t.get("evidencia_hash")


def dependencias_resueltas(m, id):
    """List (dep_id, estado) of a task's dependencies (for audit)."""
    t = _leer(m, id)
    if t is None:
        return []
    return [(d, estado(m, d)) for d in t.get("depends_on", [])]


def inputs(m, id):
    """DATA FLOW (Fable ruling step 5): FIXED results of the dependencies.

    Each dep is read by its `resultado_ref` (PINNED version), not by `current()` (which could
    advance after this task started). Returns [{dep, estado, resultado}] where `resultado` is the
    value of the fact `resultado_ref` points to (None if the dep failed or has no output).

    MERGE = sink node: a task whose `depends_on` are the subtasks, and whose `resolver` records
    the combined output. `inputs` is what that node consumes to produce the merge."""
    t = _leer(m, id)
    if t is None:
        return []
    salida = []
    for d in t.get("depends_on", []):
        salida.append({
            "dep": d,
            "estado": estado(m, d),
            "resultado": resultado(m, d),
        })
    return salida


if __name__ == "__main__":
    from memoria_sqlite import HCMAMemoriaSQLite
    m = HCMAMemoriaSQLite(":memory:")

    def _resolver_demo(tid, ses):
        v = m.write(f"resultado:{tid}", {"valor": 1}, unit="", fuente="calculadora",
                    verified=True)
        ref = {"key": f"resultado:{tid}", "version": v}
        return resolver(m, tid, ses, ref, _hash_valor({"valor": 1}))

    registrar(m, "c", "calcular X", depends_on=("a", "b"))
    registrar(m, "a", "datos de entrada")
    registrar(m, "b", "modelo")
    print("ready c (a,b pendientes):", ready(m, "c"))
    print("tomar a:", tomar(m, "a", "s1"), "| resolver a (dueño s1):", _resolver_demo("a", "s1"))
    print("tomar b:", tomar(m, "b", "s2"), "| resolver b (dueño s2):", _resolver_demo("b", "s2"))
    print("ready c (a,b resueltos):", ready(m, "c"))
    print("listas_ready:", listas_ready(m))
    print("tomar c:", tomar(m, "c", "s1"))
    # second claim must fail (already en_curso)
    print("tomar c de nuevo (debe False):", tomar(m, "c", "s2"))
    # ownership: another agent cannot resolve c
    print("resolver c por s2 (debe False, no es dueño):", _resolver_demo("c", "s2"))
    # cycle (forward reference): x depends on y, then y depends on x → cycle
    try:
        registrar(m, "x", depends_on=("y",))   # forward ref, OK
        registrar(m, "y", depends_on=("x",))   # cycle x→y→x → must fail
        print("ERROR: no detectó ciclo")
    except ValueError as e:
        print("OK ciclo:", e)
    # duplicate
    try:
        registrar(m, "a", depends_on=())
        print("ERROR: no detectó duplicado")
    except ValueError as e:
        print("OK duplicado:", e)
