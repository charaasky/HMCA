#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_tareas.py — pool de tareas: DAG + claim + ownership + CAS + evidencia + fallida."""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import tareas as T                             # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")
    return cond


def fresh():
    return HCMAMemoriaSQLite(":memory:")


def _ref(m, key, valor):
    """Escribe un resultado como fact y devuelve (resultado_ref, evidencia_hash)."""
    v = m.write(key, valor, unit="", fuente="calculadora", verified=True)
    return {"key": key, "version": v}, T._hash_valor(valor)


print("=" * 70)
print("[1] registrar + ready (dependencias imponen orden)")
print("=" * 70)
m = fresh()
T.registrar(m, "c", depends_on=("a", "b"))
T.registrar(m, "a")
T.registrar(m, "b")
check("1a. c NO ready (a,b pendientes)", T.ready(m, "c") is False)
check("1b. a y b ready (sin deps)", T.ready(m, "a") and T.ready(m, "b"))

print("\n[2] tomar (claim) + atomicidad (CAS)")
print("=" * 70)
check("2a. tomar a → True", T.tomar(m, "a", "s1") is True)
check("2b. tomar a de nuevo → False", T.tomar(m, "a", "s2") is False)
check("2c. estado a = en_curso", T.estado(m, "a") == T.EN_CURSO)
check("2d. agent_id a = s1", (T._leer(m, "a") or {}).get("agent_id") == "s1")

print("\n[3] resolver CON EVIDENCIA (ownership + verificado derivado)")
print("=" * 70)
ref_a, hash_a = _ref(m, "resultado:a", {"valor": 42})
check("3a. resolver a sin evidencia → False", T.resolver(m, "a", "s1", None, None) is False)
check("3b. resolver a (dueño s1 + evidencia) → True",
      T.resolver(m, "a", "s1", ref_a, hash_a) is True)
check("3c. resolver a por s2 → False (ya resuelto/no dueño)",
      T.resolver(m, "a", "s2", ref_a, hash_a) is False)
check("3d. resuelto_verificado a → True", T.resuelto_verificado(m, "a") is True)
check("3e. resultado a devuelve el fact", (T.resultado(m, "a") or {}).get("value") == {"valor": 42})
check("3f. resolved_by a = s1 (auditable)", (T._leer(m, "a") or {}).get("resolved_by") == "s1")

print("\n[4] fallida es resultado de primera clase")
print("=" * 70)
ref_b, hash_b = _ref(m, "resultado:b", {"valor": 7})
check("4a. tomar b (s2) → True", T.tomar(m, "b", "s2") is True)
check("4b. fallar b (s2) → True", T.fallar(m, "b", "s2", motivo="timeout") is True)
check("4c. estado b = fallida", T.estado(m, "b") == T.FALLIDA)
check("4d. c ready (a resuelto, b fallida cierra DAG)", T.ready(m, "c") is True)

print("\n[5] evidencia que no matchea → NO verificado")
print("=" * 70)
m2 = fresh()
T.registrar(m2, "a")
T.tomar(m2, "a", "s1")
ref, _ = _ref(m2, "resultado:a", {"valor": 42})
# hash FALSO: no matchea el value del fact
T.resolver(m2, "a", "s1", ref, "0" * 64)
check("5a. resuelto_verificado con hash falso → False", T.resuelto_verificado(m2, "a") is False)

print("\n[6] ciclos (self + forward)")
print("=" * 70)
m3 = fresh()
try:
    T.registrar(m3, "self", depends_on=("self",))
    check("6a. self-ciclo A→A detectado", False)
except ValueError:
    check("6a. self-ciclo A→A detectado", True)
m4 = fresh()
T.registrar(m4, "x", depends_on=("y",))
try:
    T.registrar(m4, "y", depends_on=("x",))
    check("6b. forward x→y→x detectado", False)
except ValueError:
    check("6b. forward x→y→x detectado", True)

print("\n[7] duplicado y dep pendiente")
print("=" * 70)
m5 = fresh()
T.registrar(m5, "a")
try:
    T.registrar(m5, "a")
    check("7a. duplicado rechazado", False)
except ValueError:
    check("7a. duplicado rechazado", True)
T.registrar(m5, "z", depends_on=("w",))
check("7b. z nunca ready (dep inexistente)", T.ready(m5, "z") is False)

print("\n[8] trazabilidad (journal de ops, dictamen Fable paso 2)")
print("=" * 70)

def _journal(mm):
    rows = mm.conn.execute(
        "SELECT seq, op, key, payload FROM facts_log ORDER BY seq").fetchall()
    return [dict(r) for r in rows]

m6 = fresh()
T.tomar(m6, "no_existe", "s1")                 # tomar que PIERDE (inexistente)
T.registrar(m6, "x", depends_on=("y",))          # forward ref OK
try:
    T.registrar(m6, "y", depends_on=("x",))      # ciclo → rechazo
    check("8z. ciclo NO detectado", False)
except ValueError:
    pass
j = _journal(m6)
ops = [r["op"] for r in j]
check("8a. tomar inexistente queda en journal", "tarea.tomar.rechazo" in ops)
check("8b. registrar con ciclo queda en journal", "tarea.registrar.rechazo" in ops)
seqs = [r["seq"] for r in j]
check("8c. seq monótono creciente (único)",
      seqs == sorted(seqs) and len(seqs) == len(set(seqs)))

m7 = fresh()
T.registrar(m7, "a", session_id="sess:1")
check("8d. registered_by = session_id cuando hay sesión",
      (T._leer(m7, "a") or {}).get("registered_by") == "sess:1")

print("\n[9] reclaim (liberar_sesion, dictamen Fable paso 4)")
print("=" * 70)
m8 = fresh()
T.registrar(m8, "a")
T.registrar(m8, "b")
T.tomar(m8, "a", "s1")
T.tomar(m8, "b", "s2")  # otra sesión, no debe tocarla
check("9a. liberar s1 devuelve ['a']", T.liberar_sesion(m8, "s1") == ["a"])
check("9b. a vuelve a pendiente", T.estado(m8, "a") == T.PENDIENTE)
check("9c. agent_id a = None", (T._leer(m8, "a") or {}).get("agent_id") is None)
check("9d. b sigue en_curso (no era de s1)", T.estado(m8, "b") == T.EN_CURSO)
# fencing: el zombi s1 (claim viejo) no puede resolver a tras el reclaim
ref_a, hash_a = _ref(m8, "resultado:a", {"valor": 1})
check("9e. zombi s1 no resuelve a tras reclaim",
      T.resolver(m8, "a", "s1", ref_a, hash_a) is False)
check("9f. a ready de nuevo", T.ready(m8, "a") is True)
check("9g. s2 puede tomar a ahora", T.tomar(m8, "a", "s2") is True)
check("9h. liberar sesión sin tareas → []", T.liberar_sesion(m8, "nadie") == [])

print("\n[10] flujo de datos (inputs, dictamen Fable paso 5)")
print("=" * 70)
m9 = fresh()
T.registrar(m9, "a")
T.registrar(m9, "b")
T.registrar(m9, "merge", depends_on=("a", "b"))
T.tomar(m9, "a", "s1")
ref_a, hash_a = _ref(m9, "resultado:a", {"x": 1})
T.resolver(m9, "a", "s1", ref_a, hash_a)
T.tomar(m9, "b", "s2")
ref_b, hash_b = _ref(m9, "resultado:b", {"y": 2})
T.resolver(m9, "b", "s2", ref_b, hash_b)
ins = T.inputs(m9, "merge")
by_dep = {i["dep"]: i for i in ins}
check("10a. merge recibe 2 inputs", len(ins) == 2)
check("10b. input a = resultado fijado (versión pinneada)",
      (by_dep.get("a", {}).get("resultado") or {}).get("value") == {"x": 1})
check("10c. input b = resultado fijado",
      (by_dep.get("b", {}).get("resultado") or {}).get("value") == {"y": 2})
check("10d. estado de deps resuelto", all(i["estado"] == T.RESUELTO for i in ins))
# un dep FALLIDA: su resultado es None pero no cuelga el flujo
m10 = fresh()
T.registrar(m10, "a")
T.registrar(m10, "m", depends_on=("a",))
T.tomar(m10, "a", "s1")
T.fallar(m10, "a", "s1", motivo="sin datos")
check("10e. dep fallida → resultado None, estado fallida",
      T.inputs(m10, "m")[0]["resultado"] is None
      and T.inputs(m10, "m")[0]["estado"] == T.FALLIDA)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
