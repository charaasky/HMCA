#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_orquestador.py — puente objetivo↔pool (dictamen Fable 5.2, híbrido opción A)."""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

os.environ.setdefault("HCMA_HUMANO_HMAC_KEY", "clave-test-orquestador")

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import objetivo as OBJ                          # noqa: E402
import tareas as T                              # noqa: E402
import orquestador as O                         # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        FAIL.append(name)


def fresh():
    return HCMAMemoriaSQLite(":memory:")


def objetivo_firmado(n_sub=2):
    obj = OBJ.construir_objetivo(
        "oid", "pedido demo", "interpretacion demo", "criterio demo",
        subobjetivos=[f"so{i}" for i in range(n_sub)])
    return obj, OBJ.firmar_objetivo(obj)


def plan_basico():
    return {"tareas": [
        {"id": "t0", "subobjetivo_idx": 0, "descripcion": "sub 0"},
        {"id": "t1", "subobjetivo_idx": 1, "descripcion": "sub 1", "depends_on": ["t0"]},
    ], "merge_id": "merge"}


def objetivo_v2_firmado():
    sub = [
        {"id": "t0", "descripcion": "sub 0", "depends_on": []},
        {"id": "t1", "descripcion": "sub 1", "depends_on": ["t0"]},
    ]
    obj = OBJ.construir_objetivo_v2("oid2", "pedido demo", "interp demo", "criterio demo",
                                    sub, "merge")
    return obj, OBJ.firmar_objetivo(obj)


def _rechaza(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


def _ref(m, key, valor):
    v = m.write(key, valor, unit="", fuente="calculadora", verified=True)
    return {"key": key, "version": v}, T._hash_valor(valor)


def _resolver(m, tid, ses, key, valor):
    T.tomar(m, tid, ses)
    ref, h = _ref(m, key, valor)
    return T.resolver(m, tid, ses, ref, h)


def _completar_pool(m):
    """Ejecuta t0 → t1 → merge (sumidero), todo con evidencia válida."""
    _resolver(m, "t0", "s1", "res:t0", {"x": 1})
    _resolver(m, "t1", "s2", "res:t1", {"y": 2})
    _resolver(m, "merge", "s3", "res:merge", {"final": 3})


print("=== [1] firma inválida -> None + journal ===")
m = fresh()
obj, firma = objetivo_firmado()
r = O.descomponer(m, obj, "0" * 64, plan_basico())  # firma falsa
check("1a. firma inválida devuelve None", r is None)
ops = [x["op"] for x in m.conn.execute(
    "SELECT op FROM facts_log ORDER BY seq").fetchall()]
check("1b. rechazo queda en journal", "orquestador.descomponer.rechazo" in ops)

print("\n=== [2] validación del plan ===")
m = fresh()
obj, firma = objetivo_firmado()
for nombre, plan in [
    ("ciclo", {"tareas": [{"id": "a", "subobjetivo_idx": 0, "depends_on": ["b"]},
                          {"id": "b", "subobjetivo_idx": 1, "depends_on": ["a"]}],
               "merge_id": "m"}),
    ("dep inexistente", {"tareas": [{"id": "a", "subobjetivo_idx": 0, "depends_on": ["zzz"]},
                                    {"id": "b", "subobjetivo_idx": 1}], "merge_id": "m"}),
    ("subobjetivo sin cubrir", {"tareas": [{"id": "a", "subobjetivo_idx": 0}], "merge_id": "m"}),
    ("merge_id colisiona", {"tareas": [{"id": "m", "subobjetivo_idx": 0},
                                       {"id": "b", "subobjetivo_idx": 1}], "merge_id": "m"}),
]:
    try:
        O.descomponer(m, obj, firma, plan)
        check(f"2. {nombre} rechazado", False)
    except ValueError:
        check(f"2. {nombre} rechazado", True)

print("\n=== [3] descomponer registra DAG + merge + marcador ===")
m = fresh()
obj, firma = objetivo_firmado()
r = O.descomponer(m, obj, firma, plan_basico())
check("3a. tareas registradas", r["tareas"] == ["t0", "t1"])
check("3b. merge registrado", r["merge_id"] == "merge")
check("3c. tarea atada a objetivo_hash",
      (T._leer(m, "t0") or {}).get("objetivo_hash") == r["objetivo_hash"])
check("3d. merge atado a plan_hash",
      (T._leer(m, "merge") or {}).get("plan_hash") == r["plan_hash"])
check("3e. merge depende de todas las tareas",
      (T._leer(m, "merge") or {}).get("depends_on") == ["t0", "t1"])
check("3f. registered_by = fuente (session_id None)",
      (T._leer(m, "t0") or {}).get("registered_by") == "orquestador")

print("\n=== [4] idempotente por plan_hash + plan distinto rechazado ===")
m = fresh()
obj, firma = objetivo_firmado()
O.descomponer(m, obj, firma, plan_basico())
r2 = O.descomponer(m, obj, firma, plan_basico())
check("4a. segunda descomponer idempotente", r2.get("idempotente") is True)
plan_otro = {"tareas": [{"id": "u", "subobjetivo_idx": 0},
                        {"id": "v", "subobjetivo_idx": 1}], "merge_id": "m2"}
try:
    O.descomponer(m, obj, firma, plan_otro)
    check("4b. plan distinto rechazado", False)
except ValueError:
    check("4b. plan distinto rechazado", True)

print("\n=== [5] estado: sin descomponer -> ABIERTO ===")
m = fresh()
obj, firma = objetivo_firmado()
check("5a. abierto", O.estado_objetivo_orquestado(m, obj, firma)["estado"] == OBJ.ABIERTO)

print("\n=== [6] pool completo + criterio pasa -> RESUELTO ===")
m = fresh()
obj, firma = objetivo_firmado()
O.descomponer(m, obj, firma, plan_basico())
_completar_pool(m)
ev6 = O.atar_evidencia_merge(m, obj, firma, "merge")
check("6a. atar_evidencia_merge devuelve hash", isinstance(ev6, str) and len(ev6) == 64)
check("6b. resuelto", O.estado_objetivo_orquestado(
    m, obj, firma, lambda v: v and v.get("final") == 3)["estado"] == OBJ.RESUELTO)

print("\n=== [7] criterio falla sobre output del merge -> EN_CURSO ===")
m = fresh()
obj, firma = objetivo_firmado()
O.descomponer(m, obj, firma, plan_basico())
_completar_pool(m)
O.atar_evidencia_merge(m, obj, firma, "merge")
check("7a. criterio falla no resuelve", O.estado_objetivo_orquestado(
    m, obj, firma, lambda v: v and v.get("final") == 999)["estado"] == OBJ.EN_CURSO)

print("\n=== [8] merge con evidencia falsa (output alterado) -> EN_CURSO ===")
m = fresh()
obj, firma = objetivo_firmado()
O.descomponer(m, obj, firma, plan_basico())
_resolver(m, "t0", "s1", "res:t0", {"x": 1})
_resolver(m, "t1", "s2", "res:t1", {"y": 2})
# merge se resuelve con hash que NO matchea el output (evidencia falsa)
T.tomar(m, "merge", "s3")
ref, _ = _ref(m, "res:merge", {"final": 3})
T.resolver(m, "merge", "s3", ref, "0" * 64)
check("8a. merge no verificado", T.resuelto_verificado(m, "merge") is False)
check("8b. estado EN_CURSO", O.estado_objetivo_orquestado(
    m, obj, firma, lambda v: True)["estado"] == OBJ.EN_CURSO)

print("\n=== [9] estado es SOLO LECTURA ===")
m = fresh()
obj, firma = objetivo_firmado()
O.descomponer(m, obj, firma, plan_basico())
_completar_pool(m)
O.atar_evidencia_merge(m, obj, firma, "merge")
n_antes = m.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
O.estado_objetivo_orquestado(m, obj, firma, lambda v: True)
n_despues = m.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
check("9a. ninguna versión nueva en el store", n_antes == n_despues)

print("\n=== [10] schema v2: subobjetivos = specs de tarea firmadas (opción B) ===")
obj1, f1 = objetivo_firmado()  # v1
check("10a. firma v1 sigue verificando (retrocompatibilidad)",
      OBJ.verificar_objetivo(obj1, f1) is True)
obj2, f2 = objetivo_v2_firmado()
check("10b. firma v2 verifica", OBJ.verificar_objetivo(obj2, f2) is True)

m = fresh()
r = O.descomponer_v2(m, obj2, f2)
check("10c. descomponer_v2 registra el DAG firmado",
      r["tareas"] == ["t0", "t1"] and r["merge_id"] == "merge")
check("10d. t1 depende de t0 (DAG del objetivo, no del LLM)",
      (T._leer(m, "t1") or {}).get("depends_on") == ["t0"])

# editar el DAG (quitar la dependencia) invalida la firma
sub_edit = [{"id": "t0", "depends_on": []}, {"id": "t1", "depends_on": []}]
obj2e = OBJ.construir_objetivo_v2("oid2", "pedido demo", "interp demo", "criterio demo",
                                  sub_edit, "merge")
check("10e. editar el DAG invalida la firma", OBJ.verificar_objetivo(obj2e, f2) is False)
check("10f. descomponer_v2 sobre schema != 2 -> ValueError",
      _rechaza(lambda: O.descomponer_v2(m, obj1, f1)))
check("10g. descomponer_v2 idempotente", O.descomponer_v2(m, obj2, f2).get("idempotente") is True)

_completar_pool(m)
O.atar_evidencia_merge(m, obj2, f2, "merge")
check("10h. v2 resuelto con criterio", O.estado_objetivo_orquestado(
    m, obj2, f2, lambda v: v and v.get("final") == 3)["estado"] == OBJ.RESUELTO)

print(f"\nRESULTADO: {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
