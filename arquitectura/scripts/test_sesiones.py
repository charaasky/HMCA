#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_sesiones.py — registro de sesiones (imputación de session_id, dictamen Fable paso 2)."""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import sesiones as S                           # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        FAIL.append(name)


def _raise(fn, *a):
    try:
        fn(*a)
        return False
    except (ValueError, TypeError):
        return True


m = HCMAMemoriaSQLite(":memory:")
v1 = S.abrir(m, "s1", modelo="claude-sonnet-5.5")
d = S.datos(m, "s1")
check("1a. abrir crea sesión con inicio", d is not None and bool(d.get("inicio")))
check("1b. abierta True", S.abierta(m, "s1") is True)
v2 = S.abrir(m, "s1")  # idempotente
check("1c. abrir idempotente (misma versión)", v1 == v2)
check("2a. cerrar setea fin", S.cerrar(m, "s1", motivo="EOF") is True)
check("2b. abierta False tras cerrar", S.abierta(m, "s1") is False)
check("2c. cerrar de nuevo no pisa fin (idempotente)", S.cerrar(m, "s1") is False)
check("2d. datos conserva motivo_cierre", S.datos(m, "s1").get("motivo_cierre") == "EOF")
check("3a. cerrar sesión inexistente → False", S.cerrar(m, "no_existe") is False)
check("3b. abrir sin session_id levanta", _raise(S.abrir, m, ""))

print("\n[4] identificar (metadata del lanzador, first-write-wins)")
m2 = HCMAMemoriaSQLite(":memory:")
S.abrir(m2, "s2")
check("4a. identificar completa identidad",
      S.identificar(m2, "s2", agent_id="a1", modelo="sonnet", lanzamiento="run-1") is True)
d2 = S.datos(m2, "s2")
check("4b. agent_id seteado", d2.get("agent_id") == "a1")
check("4c. modelo seteado", d2.get("modelo") == "sonnet")
check("4d. lanzamiento seteado", d2.get("lanzamiento") == "run-1")
# un agente malicioso NO puede sobreescribir la identidad (first-write-wins):
# identificar devuelve False porque no hay nada que actualizar (ya estaba seteado).
check("4e. segundo identificar no pisa agent_id",
      S.identificar(m2, "s2", agent_id="malo") is False
      and S.datos(m2, "s2").get("agent_id") == "a1")
check("4f. identificar sin cambios → False",
      S.identificar(m2, "s2", agent_id="a1", modelo="sonnet") is False)
check("4g. identificar sesión inexistente → False", S.identificar(m2, "nadie", agent_id="x") is False)

print(f"\nRESULTADO: {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
