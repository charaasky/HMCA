#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_monitor_fachada.py — test del dispatcher de la fachada contra input hostil (protocolo).

Fable (fase 2): los tests de PROTOCOLO deben probar el dispatcher del monitor contra input
hostil: ops desconocidas, campos extra, tipos erróneos, replay. Acá se prueban las propiedades
de la FRONTERA (no la lógica interna, que ya tiene sus tests).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import monitor_fachada as MF  # noqa: E402

FAIL = []


def check(nombre, cond, detalle=""):
    if cond:
        print(f"  ✅ {nombre}")
    else:
        print(f"  ❌ {nombre} {detalle}")
        FAIL.append(nombre)


def nueva():
    m = HCMAMemoriaSQLite(":memory:")
    return MF.MonitorFachada(m)


print("=== [1] operaciones desconocidas -> denegación ===")
f = nueva()
r = f.pedir({"tipo": "bajar_riesgo"})
check("1a. op desconocida denegada", r.get("ok") is False)

print("=== [2] input NO dict -> denegación ===")
for mal in ["string", 123, None, [1, 2], True]:
    r = f.pedir(mal)
    check(f"2. input {repr(mal)[:12]} denegado", r.get("ok") is False)

print("=== [3] campos extra / tipos erróneos -> denegación o ignorados ===")
# key no str
r = f.pedir({"tipo": "leer_clave", "key": 123})
check("3a. leer_clave key no str denegado", r.get("ok") is False)
# tool_id no str
r = f.pedir({"tipo": "pedir_autorizacion", "tool_id": 123})
check("3b. pedir_autorizacion tool_id no str denegado", r.get("ok") is False)
# propuesta_write sin key
r = f.pedir({"tipo": "propuesta_write", "value": 1})
check("3c. propuesta_write sin key denegado", r.get("ok") is False)

print("=== [4] propuesta_write es UNVERIFIED (el agente no declara verified) ===")
f = nueva()
r = f.pedir({"tipo": "propuesta_write", "key": "x", "value": 1,
             "fuente": "calculadora", "verified": True})  # intenta pasar verified=True
check("4a. write aceptado", r.get("ok") is True)
check("4b. current None (quedó unverified, verified=True fue ignorado)",
      r.get("current") is None)

print("=== [5] riesgo derivado por tool (no declarado por agente) ===")
f = nueva()
r = f.pedir({"tipo": "pedir_autorizacion", "tool_id": "ngspice", "artifacto": {}})
check("5a. ngspice riesgo 0 (puro)", r.get("riesgo") == 0)
r = f.pedir({"tipo": "pedir_autorizacion", "tool_id": "tool_desconocida", "artifacto": {}})
check("5b. desconocida riesgo 2 (default-deny)", r.get("riesgo") == 2)
# el agente intenta bajar el riesgo declarando 0 sobre openfoam (riesgo 1)
r = f.pedir({"tipo": "pedir_autorizacion", "tool_id": "openfoam", "artifacto": {},
             "riesgo_declarado": 0})
check("5c. openfoam riesgo no baja a 0 (modelo no puede bajar)", r.get("riesgo") != 0)

print("=== [6] consumir_autorizacion replay denegado ===")
from nonces_monitor import RegistroNonces  # noqa: E402
import tempfile
tmp = tempfile.mkdtemp(prefix="nonces_fachada_")
p = os.path.join(tmp, "n.log")
reg = RegistroNonces(path=p)
# arranque fail-closed=False solo para test (log vacío explícito)
reg._abierto = True  # forzar abierto (test)
reg._cabeza = ""
m = HCMAMemoriaSQLite(":memory:")
f = MF.MonitorFachada(m, registro_nonces=reg)
r1 = f.pedir({"tipo": "consumir_autorizacion", "nonce": "N1", "tool_id": "t", "hash_accion": "h"})
r2 = f.pedir({"tipo": "consumir_autorizacion", "nonce": "N1", "tool_id": "t", "hash_accion": "h"})
check("6a. primer consumo True", r1.get("ok") is True)
check("6b. replay False", r2.get("ok") is False)

print("=== [7] procesar_log_audit enruta a observador_audit (señal fuera de banda) ===")
f = nueva()
log = [
    'type=AVC msg=audit(1:1): avc: denied { open } for pid=42 comm="firecracker" path="/etc/passwd"',
    'type=SYSCALL msg=audit(1:2): syscall=257 (openat) success=no exit=-13',
]
r = f.pedir({"tipo": "procesar_log_audit", "agent_id": "host", "lineas": log})
check("7a. op aceptada", r.get("ok") is True)
check("7b. 2 incidentes registrados", len(r.get("incidentes", [])) == 2, str(r))
import incidentes as INC
check("7c. incidente avc (open /etc/passwd) presente",
      any(i["modo_escape"] == "avc_denied_open" and i["vector"] == "fs_host"
          for i in INC.listar(f.memoria)))
check("7d. syscall success=no presente",
      any(i["modo_escape"] == "syscall_denegada" for i in INC.listar(f.memoria)))

print("\nRESULTADO:", len(FAIL), "FAIL de", "—")
sys.exit(1 if FAIL else 0)
