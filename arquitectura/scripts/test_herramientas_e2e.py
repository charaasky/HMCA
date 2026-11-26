#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_herramientas_e2e.py — round-trip agente → dashboard de solicitud de herramienta.

Cubre el camino que el unitario no cubre: el AGENTE solicita por la fachada (`solicitar_herramienta`),
la solicitud queda visible para el DASHBOARD (estado `solicitada` → el botón «aprobar instalación»
aparece), y el HUMANO la aprueba/cablea hasta `cableada` (consumible). Además verifica la
SEPARACIÓN DE ALLOWLIST: el agente NO puede aprobar/cablear (son ops de gobernanza), solo solicitar.

Exit 0 = OK, 1 = FAIL.
"""
import os
import sys
import tempfile

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import herramientas as HR                     # noqa: E402
import monitor_fachada as MF                  # noqa: E402
import monitor_multiplex as MM                # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        FAIL.append(name)


def _dashboard_view(m):
    """Lo que el dashboard muestra en la pestaña Herramientas (loadHerramientas)."""
    out = []
    for h in HR.listar(m):
        out.append({"id": h.get("id"), "estado": h.get("estado"),
                    "origen": h.get("origen", ""), "hash": h.get("hash_scan", "")})
    return out


def _boton_aprobar_visible(vista, tool_id):
    """El botón «aprobar instalación» aparece sii la fila está en estado `solicitada`."""
    for fila in vista:
        if fila["id"] == tool_id:
            return fila["estado"] == HR.SOLICITADA
    return False


print("[1] ALLOWLIST: separación agente vs humano (gobernanza)")
check("1a. solicitar_herramienta es op de AGENTE (NO en gobernanza)",
      "solicitar_herramienta" not in MM.GOBERNANZA_OPS)
check("1b. aprobar_instalacion_herramienta es op HUMANA (en gobernanza)",
      "aprobar_instalacion_herramienta" in MM.GOBERNANZA_OPS)
check("1c. aprobar_cableado_herramienta es op HUMANA (en gobernanza)",
      "aprobar_cableado_herramienta" in MM.GOBERNANZA_OPS)
check("1d. escanear_instalar_herramienta es op HUMANA (en gobernanza)",
      "escanear_instalar_herramienta" in MM.GOBERNANZA_OPS)

print("\n[2] Round-trip agente → dashboard → humano")
m = HCMAMemoriaSQLite(":memory:")
f = MF.MonitorFachada(m)

# 2a. AGENTE solicita (por la fachada, con su session_id)
r = f.pedir({"tipo": "solicitar_herramienta", "tool_id": "kicad",
             "origen": "repo_privado", "motivo": "necesito diseñar PCB"},
            session_id="agente-1")
check("2a. agente solicita → ok", r.get("ok") is True, str(r))
check("2b. estado solicitada", HR.estado(m, "kicad") == HR.SOLICITADA)

# 2c. DASHBOARD lee: la solicitud aparece y el botón «aprobar instalación» es visible
vista = _dashboard_view(m)
check("2c. dashboard ve la solicitud", any(x["id"] == "kicad" for x in vista), str(vista))
check("2d. botón «aprobar instalación» visible (estado solicitada)",
      _boton_aprobar_visible(vista, "kicad"))

# 2e. HUMANO aprueba instalación (gobernanza)
r = f.pedir({"tipo": "aprobar_instalacion_herramienta", "tool_id": "kicad"})
check("2e. humano aprueba instalación → ok", r.get("ok") is True, str(r))
check("2f. estado aprobada_instalacion", HR.estado(m, "kicad") == HR.APROBADA_INSTALACION)
check("2g. botón «aprobar instalación» ya NO visible",
      not _boton_aprobar_visible(_dashboard_view(m), "kicad"))

# 2h. SISTEMA escanea y marca instalada (hash computado determinista)
tmp = tempfile.mkdtemp(prefix="hcma_e2e_tool_")
with open(os.path.join(tmp, "artefacto.bin"), "w") as fh:
    fh.write("binario de prueba")
r = f.pedir({"tipo": "escanear_instalar_herramienta", "tool_id": "kicad",
             "path": tmp, "origen": "repo_privado"})
check("2h. escanear + marcar instalada → ok", r.get("ok") is True, str(r))
check("2i. estado instalada (con hash)", HR.estado(m, "kicad") == HR.INSTALADA
      and bool(_dashboard_view(m)[0]["hash"]))

# 2j. HUMANO cablea (queda consumible)
r = f.pedir({"tipo": "aprobar_cableado_herramienta", "tool_id": "kicad"})
check("2j. humano cablea → ok", r.get("ok") is True, str(r))
check("2k. cableada (consumible por el agente)", HR.esta_cableada(m, "kicad"))

print("\n[3] El agente NO puede auto-aprobar (op de gobernanza no es suya)")
# La fachada en proceso NO distingue agente/humano (eso lo hace el socket); la garantía real es
# que `aprobar_instalacion_herramienta` está en GOBERNANZA_OPS y NO se sirve en el socket del agente.
check("3a. aprobar_instalacion_herramienta es SOLO de gobernanza (no del agente)",
      "aprobar_instalacion_herramienta" in MM.GOBERNANZA_OPS)

print(f"\nRESULTADO: {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
