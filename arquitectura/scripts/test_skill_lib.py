#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test end-to-end del subsistema skill_lib sobre HCMAMemoriaSQLite.

Verifica el contrato acordado con Fable:
  registrar_spec -> proponer_impl -> evaluar -> activar -> ejecutables,
con los invariantes I2/I3/I7/I8/I11-I16 en el comportamiento real.

NO toca hcma_v64.py ni paper6_osf/. Uso:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps \
      python3 arquitectura/scripts/test_skill_lib.py
"""

import os
import sys
import tempfile

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import skill_lib  # noqa: E402


def _mk():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_skill_")
    os.close(fd)
    return HCMAMemoriaSQLite(path)


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


# ── especificación autocontenida de un skill "doble" ─────────────────────────
SPEC_DOBLE = {
    "nombre": "duplicar",
    "inputs": [{"nombre": "x", "unidad": "1"}],
    "outputs": [{"nombre": "y", "unidad": "1", "tolerancia": {"tipo": "abs", "valor": 1e-6}}],
    "tests_dorados": [
        {"id": "cero", "inputs": {"x": 0}, "esperado": {"y": 0}},
        {"id": "tres", "inputs": {"x": 3}, "esperado": {"y": 6}},
    ],
}
CODIGO_BUENO = "def calcular(inputs):\n    return {'y': inputs['x'] * 2}\n"
CODIGO_MALO = "def calcular(inputs):\n    return {'y': inputs['x'] * 3}\n"  # falla 'tres' (3*3=9 != 6)


m = _mk()

# 1. spec sin tests dorados => rechazada
spec_vacia = dict(SPEC_DOBLE, tests_dorados=[])
try:
    skill_lib.registrar_spec(m, "doblar", spec_vacia)
    check("spec sin tests dorados es rechazada", False)
except ValueError:
    check("spec sin tests dorados es rechazada", True)

# 2. registrar spec válido
v_spec = skill_lib.registrar_spec(m, "doblar", SPEC_DOBLE)
check("registrar_spec escribe spec verified (fuente humana)", v_spec is not None)

# 3. proponer impl buena compila y queda unverified
v_impl, h = skill_lib.proponer_impl(m, "doblar", CODIGO_BUENO)
check("proponer_impl devuelve (version, impl_hash)",
      isinstance(v_impl, int) and isinstance(h, str) and len(h) == 64)
impl_fact = m.get_version("skill:doblar:impl", v_impl)
check("impl queda unverified (I1)", impl_fact is not None and not impl_fact["verified"])

# 4. idempotencia de proponer_impl (mismo hash -> misma versión) I3
n_before = m.conn.execute("SELECT COUNT(*) FROM facts WHERE key='skill:doblar:impl'").fetchone()[0]
v_impl2, h2 = skill_lib.proponer_impl(m, "doblar", CODIGO_BUENO)
n_after = m.conn.execute("SELECT COUNT(*) FROM facts WHERE key='skill:doblar:impl'").fetchone()[0]
check("proponer_impl idempotente por hash (I3)",
      v_impl == v_impl2 and h == h2 and n_before == n_after,
      f"{v_impl}->{v_impl2}, {n_before}->{n_after}")

# 5. evaluar impl buena -> ok, todos los tests pasan
res = skill_lib.evaluar(m, "doblar", v_impl)
check("evaluar impl buena produce ok (todos pasan)", res.get("todos_ok") is True, str(res))
ok = m.current(f"skill:doblar:ok:{h}")
check("ok:<impl_hash> es canónico", ok is not None)
check("ok value lleva impl_hash + spec_hash + n_casos (I8)",
      ok["value"].get("impl_hash") == h and ok["value"].get("n_casos") == 2
      and isinstance(ok["value"].get("spec_hash"), str))

# 6. completitud de linaje (I7): ok deriva de TODOS los test:*
test_keys = [f"test:doblar:{h}:cero", f"test:doblar:{h}:tres"]
check("ok deriva de TODOS los tests (I7)",
      set(ok["derived_from"].keys()) == set(test_keys),
      f"{sorted(ok['derived_from'].keys())}")

# 7. tests registrados, ambos pass=true
for tk in test_keys:
    t = m.current(tk)
    check(f"test {tk.split(':')[-1]} es verified y pass", t is not None and t["value"]["pass"])

# 8. idempotencia de evaluar (I14): reevaluar no reescribe ok
ok_before = ok["version"]
res2 = skill_lib.evaluar(m, "doblar", v_impl)
ok_after = m.current(f"skill:doblar:ok:{h}")
check("evaluar idempotente por (impl_hash, spec_version) (I14)",
      res2.get("reusado") is True and ok_after["version"] == ok_before)

# 9. proponer impl mala NO desactiva la buena (P1 de Fable)
v_bad, h_bad = skill_lib.proponer_impl(m, "doblar", CODIGO_MALO)
res_bad = skill_lib.evaluar(m, "doblar", v_bad)
check("evaluar impl mala no produce ok (no promoción parcial, I11)",
      res_bad.get("ok_version") is None and res_bad.get("todos_ok") is False)
ok_bueno = m.current(f"skill:doblar:ok:{h}")
check("la impl buena sigue vigente pese a candidato malo",
      ok_bueno is not None and not ok_bueno["stale"])

# 10. tests que fallan SÍ se registran (I: 'la calculadora corrió esto')
t_bad = m.current(f"test:doblar:{h_bad}:tres")
check("test fallido se registra con pass=false",
      t_bad is not None and t_bad["value"]["pass"] is False)

# 11. mutar spec invalida ok (stale transitivo) — hueco 2 confirmado
skill_lib.registrar_spec(m, "doblar", dict(SPEC_DOBLE, nombre="duplicar-v2"))
ok_despues = m.current(f"skill:doblar:ok:{h}")
check("mutar spec deja stale al ok (auto-invalidación)", ok_despues is None)

# 12. activar + ejecutables
v_impl3, h3 = skill_lib.proponer_impl(m, "doblar", CODIGO_BUENO)
m_limpio = _mk()  # spec nueva porque la anterior la mutamos arriba
skill_lib.registrar_spec(m_limpio, "doblar", SPEC_DOBLE)
v_i, hh = skill_lib.proponer_impl(m_limpio, "doblar", CODIGO_BUENO)
skill_lib.evaluar(m_limpio, "doblar", v_i)
skill_lib.activar(m_limpio, "doblar", hh)
ej = skill_lib.ejecutables(m_limpio)
check("ejecutables() lista el skill con su impl_hash (I2)",
      "doblar" in ej and hh in ej["doblar"], str(ej))
activo = m_limpio.current("skill:doblar:activo")
check("activo apunta a la impl (deriva del ok)", activo is not None
      and activo["value"].get("impl_hash") == hh)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
