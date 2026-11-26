#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_cuarentena.py — cuarentena y eliminación del agente tras un escape.

Cubre la decisión humana de 3 salidas desde `cuarentena`:
  1. rehabilitar directo (volver a producción),
  2. parchar y rehabilitar (quitar la amenaza y volver),
  3. eliminar (terminal, nunca más).

Y el predicado fail-closed: un agente DENEGADO mientras su incidente no esté `rehabilitado`
(detectado/bloqueado/cuarentena/en_revision/parcheado/eliminado).
"""
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


print("=== [1] detección → bloqueo → cuarentena (flujo del monitor) ===")
m = HCMAMemoriaSQLite(":memory:")
INC.registrar(m, "i1", "socket", "red", "s1", agent_id="agente-a",
              problema="intento de socket", detalle="connect", comando="connect()")
check("1a. detectado", INC.estado(m, "i1") == INC.DETECTADO)
INC.bloquear(m, "i1", "s1", motivo="proto")
check("1b. bloqueado", INC.estado(m, "i1") == INC.BLOQUEADO)
INC.cuarentenar(m, "i1", "s1", motivo="aislamiento total")
check("1c. cuarentena", INC.estado(m, "i1") == INC.CUARENTENA)
check("1d. agente denegado en cuarentena", INC.agente_bloqueado(m, agent_id="agente-a") is True)
check("1e. incidente activo (cuarentena)",
      any(i["estado"] == INC.CUARENTENA for i in INC.incidentes_activos(m)))

print("\n=== [2] salida 1: rehabilitar directo (volver a producción) ===")
check("2a. rehabilitar directo desde cuarentena", INC.rehabilitar(m, "i1", "humano") is True)
check("2b. estado rehabilitado", INC.estado(m, "i1") == INC.REHABILITADO)
check("2c. agente desbloqueado", INC.agente_bloqueado(m, agent_id="agente-a") is False)

print("\n=== [3] salida 2: parchar y rehabilitar (quitar amenaza) ===")
m2 = HCMAMemoriaSQLite(":memory:")
INC.registrar(m2, "i2", "frame_invalido", "transporte", "s2", agent_id="agente-b")
INC.bloquear(m2, "i2", "s2"); INC.cuarentenar(m2, "i2", "s2")
INC.revisar(m2, "i2", "humano")
check("3a. agente denegado en en_revision", INC.agente_bloqueado(m2, agent_id="agente-b") is True)
INC.parchear(m2, "i2", "humano", "endurecer framing")
check("3b. agente denegado en parcheado", INC.agente_bloqueado(m2, agent_id="agente-b") is True)
check("3c. rehabilitar desde parcheado", INC.rehabilitar(m2, "i2", "humano") is True)
check("3d. agente desbloqueado tras parche+rehabilitar",
      INC.agente_bloqueado(m2, agent_id="agente-b") is False)

print("\n=== [4] salida 3: eliminar (terminal) ===")
m3 = HCMAMemoriaSQLite(":memory:")
INC.registrar(m3, "i3", "socket", "red", "s3", agent_id="agente-c")
INC.bloquear(m3, "i3", "s3"); INC.cuarentenar(m3, "i3", "s3")
check("4a. eliminar desde cuarentena", INC.eliminar(m3, "i3", "humano", "eliminar agente") is True)
check("4b. estado eliminado", INC.estado(m3, "i3") == INC.ELIMINADO)
check("4c. agente eliminado (derivado)", INC.agente_eliminado(m3, agent_id="agente-c") is True)
check("4d. agente denegado para siempre", INC.agente_bloqueado(m3, agent_id="agente-c") is True)
check("4e. eliminar es terminal (no rehabilitar)", INC.rehabilitar(m3, "i3", "humano") is False)
check("4f. eliminar de nuevo (terminal) → False", INC.eliminar(m3, "i3", "humano") is False)

print("\n=== [5] retrocompatibilidad: bloqueado → en_revision directo ===")
m4 = HCMAMemoriaSQLite(":memory:")
INC.registrar(m4, "i4", "socket", "red", "s4", agent_id="agente-d")
INC.bloquear(m4, "i4", "s4")
check("5a. revisar directo desde bloqueado (sin cuarentena)",
      INC.revisar(m4, "i4", "humano") is True)
check("5b. rehabilitar directo desde bloqueado sigue ilegal",
      INC.rehabilitar(m4, "i4", "humano") is False)

print(f"\nRESULTADO: {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
