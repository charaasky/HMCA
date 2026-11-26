#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_herramientas.py — ciclo de vida de herramientas (instalación/cableado) + CMDB + gate.

Verifica el dictamen: toda herramienta nueva requiere APROBACIÓN HUMANA de instalación y de
cableado antes de quedar consumible; el agente solo puede `solicitar`. Solo `cableada` es
consumible (predicado `esta_cableada`, que usa el gate de conectores.derivar).

Uso:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/test_herramientas.py
Exit 0 = OK, 1 = FAIL.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import herramientas as HR                     # noqa: E402
import monitor_fachada as MF                  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        FAIL.append(name)


def _mk():
    return HCMAMemoriaSQLite(":memory:")


print("[1] Ciclo de vida feliz: solicitar → aprobar instalación → instalar → cablear")
m = _mk()
HR.solicitar(m, "kicad", origen="internet", session_id="agente-1")
check("1a. solicitar → solicitada", HR.estado(m, "kicad") == HR.SOLICITADA)
check("1b. aún no cableada", not HR.esta_cableada(m, "kicad"))
check("1c. aprobar instalación (humano)", HR.aprobar_instalacion(m, "kicad", "humano"))
check("1d. marcar instalada", HR.marcar_instalada(m, "kicad", "internet"))
check("1e. aprobar cableado (humano)", HR.aprobar_cableado(m, "kicad", "humano"))
check("1f. cableada (consumible)", HR.esta_cableada(m, "kicad"))

print("\n[2] Transiciones ilegales (fail-closed)")
m2 = _mk()
HR.solicitar(m2, "lean", session_id="a")
check("2a. cablear antes de instalar → False", not HR.aprobar_cableado(m2, "lean", "h"))
check("2b. marcar instalada antes de aprobar → False",
      not HR.marcar_instalada(m2, "lean", "internet"))
check("2c. aprobar instalación dos veces → False (segunda vez)",
      HR.aprobar_instalacion(m2, "lean", "h") and not HR.aprobar_instalacion(m2, "lean", "h"))

print("\n[3] repo_privado exige hash del escaneo de seguridad")
m3 = _mk()
HR.solicitar(m3, "openfoam", origen="repo_privado", session_id="a")
HR.aprobar_instalacion(m3, "openfoam", "h")
check("3a. repo_privado sin hash → rechazado", not HR.marcar_instalada(m3, "openfoam",
                                                                      "repo_privado", ""))
check("3b. repo_privado con hash → ok",
      HR.marcar_instalada(m3, "openfoam", "repo_privado", "abc123def"))

print("\n[4] Rechazo humano (terminal) y origen inválido")
m4 = _mk()
HR.solicitar(m4, "kicad", session_id="a")
check("4a. rechazar", HR.rechazar(m4, "kicad", "h", "no confiable"))
check("4b. rechazada no es cableable", not HR.aprobar_cableado(m4, "kicad", "h"))
m4b = _mk()
try:
    HR.solicitar(m4b, "x", origen="malware.example", session_id="a")
    check("4c. origen inválido → ValueError", False)
except ValueError:
    check("4c. origen inválido → ValueError", True)

print("\n[5] CMDB (listar)")
m5 = _mk()
HR.solicitar(m5, "a", session_id="x")
HR.solicitar(m5, "b", session_id="x")
check("5a. CMDB lista 2 herramientas", len(HR.listar(m5)) == 2)

print("\n[6] Fachada (ops del monitor)")
m6 = _mk()
f = MF.MonitorFachada(m6)
r = f.pedir({"tipo": "solicitar_herramienta", "tool_id": "lean", "origen": "repo_privado"})
check("6a. solicitar_herramienta ok", r.get("ok") is True)
r = f.pedir({"tipo": "aprobar_instalacion_herramienta", "tool_id": "lean"})
check("6b. aprobar_instalacion_herramienta ok", r.get("ok") is True)
r = f.pedir({"tipo": "aprobar_cableado_herramienta", "tool_id": "lean"})
check("6c. cablear sin instalar → False (ilegal)", r.get("ok") is False)
r = f.pedir({"tipo": "listar_herramientas"})
check("6d. listar_herramientas ok", r.get("ok") is True and len(r.get("herramientas", [])) == 1)

print("\n[7] Escaneo (hash determinista) + marcar instalada")
m7 = _mk()
HR.solicitar(m7, "toolx", origen="repo_privado", session_id="a")
HR.aprobar_instalacion(m7, "toolx", "h")
import tempfile
tmp = tempfile.mkdtemp(prefix="hcma_scan_test_")
with open(os.path.join(tmp, "x.bin"), "w") as f:
    f.write("contenido de prueba")
r7 = HR.escanear_y_marcar_instalada(m7, "toolx", tmp, "repo_privado")
check("7a. escaneo ok", r7.get("ok") is True, str(r7))
check("7b. hash computado (no vacío)", bool(r7.get("hash_sha256")), str(r7))
check("7c. estado instalada", HR.estado(m7, "toolx") == HR.INSTALADA)
check("7d. hash registrado == hash computado",
      HR.listar(m7)[0].get("hash_scan") == r7.get("hash_sha256"))

print(f"\nRESULTADO: {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
