#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_incidentes.py — ciclo de vida del incidente de escape (detección→rehabilitación)."""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import incidentes as INC                       # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        FAIL.append(name)


m = HCMAMemoriaSQLite(":memory:")

print("=== [1] registrar (monitor) → DETECTADO + journal ===")
INC.registrar(m, "i1", "frame_invalido", "transporte", "s1", evidencia={"n": 1})
check("1a. estado detectado", INC.estado(m, "i1") == INC.DETECTADO)
check("1b. sesión bloqueada (predicado derivado)", INC.sesion_bloqueada(m, "s1") is True)
check("1c. incidente activo listado", any(i["id"] == "i1" for i in INC.incidentes_activos(m)))

print("\n=== [2] transiciones legales ===")
check("2a. bloquear (monitor) → bloqueado", INC.bloquear(m, "i1", "s1", motivo="proto") is True)
check("2b. sesión sigue bloqueada", INC.sesion_bloqueada(m, "s1") is True)
# transición ilegal: rehabilitar directo desde bloqueado (sin revisar/parchear)
check("2c. rehabilitar directo desde bloqueado → False",
      INC.rehabilitar(m, "i1", "humano") is False)
check("2d. revisar (humano) → en_revision", INC.revisar(m, "i1", "humano") is True)
# transición ilegal: bloquear desde en_revision
check("2e. bloquear desde en_revision → False", INC.bloquear(m, "i1", "s1") is False)

print("\n=== [3] parchear y rehabilitar (humano) ===")
check("3a. parchear sin mitigación → False", INC.parchear(m, "i1", "humano", "") is False)
check("3b. parchear con mitigación → parcheado",
      INC.parchear(m, "i1", "humano", "endurecer framing") is True)
check("3c. rehabilitar → rehabilitado", INC.rehabilitar(m, "i1", "humano") is True)
check("3d. sesión desbloqueada tras rehabilitar", INC.sesion_bloqueada(m, "s1") is False)
check("3e. rehabilitar de nuevo (terminal) → False", INC.rehabilitar(m, "i1", "humano") is False)
check("3f. no hay incidentes activos", INC.incidentes_activos(m) == [])

print("\n=== [4] duplicado y transición sobre inexistente ===")
try:
    INC.registrar(m, "i1", "otro", "red", "s2")
    check("4a. duplicado rechazado", False)
except ValueError:
    check("4a. duplicado rechazado", True)
check("4b. bloquear incidente inexistente → False", INC.bloquear(m, "no_existe", "s1") is False)

print("\n=== [5] bloqueo por identidad estable (agent_id) sobrevive a la reconexión ===")
m2 = HCMAMemoriaSQLite(":memory:")
INC.registrar(m2, "i2", "socket", "red", "s_conn_1", agent_id="agente-a")
check("5a. agente bloqueado por agent_id", INC.agente_bloqueado(m2, agent_id="agente-a") is True)
check("5b. sesión bloqueada por session_id", INC.agente_bloqueado(m2, session_id="s_conn_1") is True)
check("5c. otro agente NO bloqueado", INC.agente_bloqueado(m2, agent_id="otro") is False)
INC.bloquear(m2, "i2", "s_conn_1")
INC.revisar(m2, "i2", "humano")
INC.parchear(m2, "i2", "humano", "bloquear socket")
INC.rehabilitar(m2, "i2", "humano")
check("5d. tras rehabilitar, agente desbloqueado", INC.agente_bloqueado(m2, agent_id="agente-a") is False)

print(f"\nRESULTADO: {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
