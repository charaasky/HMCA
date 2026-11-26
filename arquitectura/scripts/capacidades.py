#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""capacidades.py — awareness of the system's TOOLS (what I can do / what I am missing).

Models the lack of a TOOL as governed data, not as a developer failure. When a request demands an
output (PCB schematic, simulation) that requires an external tool, HCMA DECLARES the dependency
and, if the tool is not available, PROPOSES it — instead of delivering an unreadable result or
degrading silently.

Namespace `tool:*`:
  tool:<id>:spec   -> declaration of the tool (binary, verification command, min version).
  tool:<id>:estado -> DERIVED fact (calculadora): {disponible, version} result of running the
                      deterministic verification command.

This relegates the "KiCad is missing" decision to the system (which knows it and proposes it), not
to the agent that solves it by hand. It is the awareness of "which tool I need" that was not yet
modeled.
"""
import os
import shutil
import subprocess
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


# Catalog of tools KNOWN by the system (declarative, not domain hardcode).
# Each one: {binario, comando_verificacion, version_minima, proposito, instalacion}.
HERRAMIENTAS = {
    "kicad": {
        "binario": "kicad-cli",
        "comando_verificacion": ["kicad-cli", "version"],
        "proposito": "esquematico + netlist + layout de PCB (EDA)",
        "instalacion": "apt install kicad  (o binario en .deps/ como hicimos con ngspice)",
        "alternativa": "render_esquematico_vectorial",
    },
    "ngspice": {
        "binario": "/home/charasky/Documentos/HMC/.deps/ngspice/usr/bin/ngspice",
        "comando_verificacion": None,  # verified via fixed absolute path
        "proposito": "simulacion electrica (derivador de fisica)",
        "instalacion": None,
    },
    "graphviz": {
        "binario": "dot",
        "comando_verificacion": ["dot", "-V"],
        "proposito": "diagramas de grafos (alternativa a matplotlib)",
        "instalacion": "apt install graphviz",
    },
    "moldex3d": {
        "binario": "Moldex3D",
        "comando_verificacion": ["Moldex3D", "--version"],
        "proposito": "simulacion de moldeo por inyeccion/espumado de polimeros (reologia, comercial)",
        "instalacion": "software comercial (licencia)",
        "alternativa": "openfoam",
    },
    "openfoam": {
        "binario": "/home/charasky/Documentos/HMC/.deps/openfoam/usr/bin/blockMesh",
        "comando_verificacion": None,  # verified via fixed path + WM_PROJECT_DIR
        "proposito": "CFD (Computational Fluid Dynamics) para simulacion de flujo/espumado de polimeros",
        "instalacion": None,  # already installed in .deps/openfoam
        "alternativa": "solver_numerico_cinetica_curado",
        "env": {"WM_PROJECT_DIR": "/home/charasky/Documentos/HMC/.deps/openfoam/usr/share/openfoam"},
    },
    "lean": {
        "binario": "lean",
        "comando_verificacion": ["lean", "--version"],
        "proposito": "verificacion formal de teoremas (proof assistant, kernel de Lean 4)",
        "instalacion": ("curl https://raw.githubusercontent.com/leanprover/elan/master/"
                        "elan-init.sh -sSf | sh  (deja ~/.elan/bin/lean)"),
        "alternativa": "verificacion_simbolica",
        # elan installs into ~/.elan/bin, which is NOT in the PATH of a non-interactive shell:
        # we add it explicitly so the deterministic verification finds it.
        "env": {"PATH": os.path.expanduser("~/.elan/bin") + ":"
                         + os.environ.get("PATH", "/usr/bin:/bin")},
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# DOMAIN TYPE -> required tools mapping (without depending on the LLM)
# ─────────────────────────────────────────────────────────────────────────────
# The SYSTEM knows which tool each domain FAMILY requires, even if the LLM does not list it.
# Thus, for 'goma EVA' (foamed polymer material), it detects that the rheology/foaming simulator
# is missing and PROPOSES it, instead of delivering only text without warning about the gap.
DOMINIO_A_HERRAMIENTA = {
    # electronics / PCB -> kicad
    ("pcb", "circuito", "electronica", "electronico", "esquematico"): ["kicad", "ngspice"],
    # polymer materials / foaming / formulation -> polymer CFD
    ("polimero", "polimeros", "eva", "espuma", "espumado", "goma", "moldeo",
     "inyeccion", "formulacion", "compuesto"): ["openfoam", "moldex3d"],
    # formal proof / theorem verification -> lean
    # (without the 'lean' substring: it matched 'boolean'/'clean' — Fable P2)
    ("teorema", "demostracion", "demostrar", "verificacion formal", "prueba formal",
     "proof assistant", "asistente de pruebas", "logica formal"): ["lean"],
}


def _matchea_dominio(pedido_lower):
    """Returns the list of tool_ids required by the request according to its domain vocabulary."""
    tools = []
    for claves, tids in DOMINIO_A_HERRAMIENTA.items():
        if any(k in pedido_lower for k in claves):
            tools.extend(tids)
    # dedupe preserving order
    return list(dict.fromkeys(tools))


def herramientas_para_pedido(pedido):
    """Given a request (text), returns the tools that the domain REQUIRES (by mapping),
    regardless of whether the LLM declares them. It is the SYSTEM's gap awareness."""
    p = (pedido or "").lower()
    return _matchea_dominio(p)


def _verificar_binario(binario, comando, env=None):
    """Returns (available, detail). Runs the verification command if present (with the tool's own
    env), or looks for the binary in PATH/fixed path."""
    import copy
    entorno = dict(os.environ)
    if env:
        entorno.update(env)
    if comando is not None:
        try:
            out = subprocess.run(comando, capture_output=True, text=True, timeout=30,
                                 env=entorno)
            return out.returncode == 0, (out.stdout or out.stderr).strip()[:120]
        except FileNotFoundError:
            return False, "comando no encontrado"
        except Exception as e:
            return False, repr(e)
    # no verification command: check existence of the binary
    if os.path.isabs(binario):
        ok = os.path.exists(binario)
        return ok, (f"presente en {binario}" if ok else f"ausente ({binario})")
    return shutil.which(binario) is not None, f"PATH: {shutil.which(binario)}"


def verificar_herramienta(tool_id):
    """Deterministically verifies whether a tool is available. Returns a dict."""
    t = HERRAMIENTAS.get(tool_id)
    if t is None:
        return {"id": tool_id, "disponible": False, "detalle": "herramienta no registrada"}
    disp, detalle = _verificar_binario(t["binario"], t.get("comando_verificacion"),
                                       t.get("env"))
    return {"id": tool_id, "disponible": disp, "detalle": detalle,
            "proposito": t["proposito"], "instalacion": t.get("instalacion"),
            "alternativa": t.get("alternativa")}


def registrar_estado(m, tool_id):
    """Writes `tool:<id>:estado` as a DERIVED fact (calculadora). Returns (key, version)."""
    v = verificar_herramienta(tool_id)
    key = f"tool:{tool_id}:estado"
    version = m.write(key, v, unit="", fuente="calculadora", verified=True)
    return key, version, v


def proponer_herramienta_faltante(m, tool_id):
    """If the tool is not available, returns an installation PROPOSAL (dict)."""
    v = verificar_herramienta(tool_id)
    if v["disponible"]:
        return {"falta": False, "id": tool_id, "detalle": v["detalle"]}
    return {
        "falta": True,
        "id": tool_id,
        "proposito": v.get("proposito"),
        "instalacion": v.get("instalacion"),
        "alternativa": v.get("alternativa"),
        "mensaje": (f"Para este entregable necesito la herramienta '{tool_id}' "
                    f"({v.get('proposito')}), que NO está instalada. "
                    f"Instalación: {v.get('instalacion')}."),
    }


def herramientas_faltantes(tool_ids):
    """Verifies several and returns a list of proposals (only the missing ones)."""
    out = []
    for tid in tool_ids:
        p = proponer_herramienta_faltante(None, tid)
        if p["falta"]:
            out.append(p)
    return out


if __name__ == "__main__":
    for tid in ("kicad", "ngspice", "graphviz"):
        v = verificar_herramienta(tid)
        print(f"{tid}: disponible={v['disponible']}  {v['detalle']}")
    print()
    print("Herramientas faltantes:", [h["id"] for h in herramientas_faltantes(
        ["kicad", "ngspice", "graphviz"])])
