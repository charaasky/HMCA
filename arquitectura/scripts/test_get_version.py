#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Regresión de `get_version` (lectura pura por versión) en HCMAMemoriaSQLite.

get_version se agregó para I9 (lectura de impl unverified). Este test fija las
garantías que Fable exigió en consultas/fable_i9_lectura_impl_fable.md:

  1. Devuelve el hecho por VERSIÓN EXACTA, sin filtro de verified/authority.
  2. NO cambia estado: no promueve, no altera verified/authority/stale.
  3. Devuelve None si la versión no existe (o es de otra key).
  4. NO existe `latest()` (la lectura cruda solo por versión pinneada).

NO toca `hcma_v64.py` ni los otros paths. Es un test de la primitiva, aislado.

Uso:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps \
      python3 arquitectura/scripts/test_get_version.py
"""

import os
import sys
import tempfile

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402


def _mk():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_gv_")
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


# 1. lee un hecho unverified por versión exacta
m = _mk()
v = m.write("skill:demo:impl", "f(x)=x*2", fuente="base", verified=False)
fact = m.get_version("skill:demo:impl", v)
check("1. get_version lee impl unverified por versión",
      fact is not None and fact["value"] == "f(x)=x*2",
      f"fact={fact}")

# current() sigue sin verlo (unverified)
check("1b. current() sigue devolviendo None para la impl unverified",
      m.current("skill:demo:impl") is None)

# 2. no cambia estado
vm = m.current("skill:demo:impl")  # None, esperado
n_before = m.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
auth_before = m.conn.execute(
    "SELECT COUNT(*) FROM facts WHERE key='skill:demo:impl' AND authority=1").fetchone()[0]
m.get_version("skill:demo:impl", v)  # lectura
n_after = m.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
auth_after = m.conn.execute(
    "SELECT COUNT(*) FROM facts WHERE key='skill:demo:impl' AND authority=1").fetchone()[0]
check("2. get_version no agrega filas ni cambia authority",
      n_before == n_after and auth_before == auth_after == 0,
      f"rows {n_before}->{n_after}, auth {auth_before}->{auth_after}")

# 3. versión inexistente -> None
check("3. versión inexistente devuelve None",
      m.get_version("skill:demo:impl", 999999) is None)

# versión correcta pero de OTRA key -> None
v2 = m.write("otro:key", 1.0, fuente="usuario", verified=True)
check("3b. versión de otra key no se cuela por (key,version) incorrecto",
      m.get_version("skill:demo:impl", v2) is None)

# 4. NO existe latest()
check("4. no existe latest() (la lectura cruda solo por versión pinneada)",
      not hasattr(m, "latest"),
      "No debe haber latest(); su semántica es 'leer y creer'.")

# 5. leer un fact verified por versión también funciona (y devuelve flags reales)
verified_fact = m.get_version("otro:key", v2)
check("5. get_version lee un fact verified con sus flags",
      verified_fact is not None and verified_fact["verified"] is True
      and verified_fact["authority"] is True and verified_fact["stale"] is False,
      f"fact={verified_fact}")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
