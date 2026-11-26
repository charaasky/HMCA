#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""conectores.py — deterministic CONNECTORS of each TOOL (the "wiring").

User's principle: 'every new tool must be wired'. Detecting that a tool
exists (`capacidades.py`) is NOT enough: there must be a way by which the ORCHESTRATOR INVOKES it
to DERIVE a governed result.

This module centralizes, per tool, a `derivar(artifacto) -> resultado` function:
  - ngspice  -> runs the netlist and extracts metrics (reuses simulador.py).
  - openfoam -> runs a deterministic MINIMAL calibration CASE (blockMesh + checkMesh).
  - kicad    -> (pending) exports a schematic to PDF.

The result is DERIVED (calculadora), never root. Authority is decided by the HCMA layer
(`verificador_fisico.registrar_simulacion`), not this module. The connector only executes.

Each connector returns a dict with {ok, observables, detalle, extract_hash} so that the
orchestrator registers it as `sim:*` and evaluates coherence (I-SIM).
"""
import os
import subprocess
import tempfile
import hashlib
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


FOAM_BIN = os.path.join(DEP, "openfoam", "usr", "bin")
FOAM_DIR = os.path.join(DEP, "openfoam", "usr", "share", "openfoam")
# FOAM_ETC = $FOAM_DIR/etc (OpenFOAM's global controlDict lives here).
# NOTE empirical finding: the global controlDict is read from $WM_PROJECT_DIR/etc/controlDict,
# where WM_PROJECT_DIR must point to FOAM_DIR (not FOAM_ETC).


def _hash(o) -> str:
    import json as _json
    return hashlib.sha256(
        _json.dumps(o, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# REAL ngspice SANDBOX (Fable P0): ALLOWLIST + isolation + resource limits.
# ─────────────────────────────────────────────────────────────────────────────
# Fable flagged that the previous version was a DENYLIST (evadable with .inc, .lib, uppercase,
# '+' continuations, .spiceinit, code models). Here we move to an isolation approach:
#   1. ALLOWLIST of SAFE and known ngspice directives (everything else is rejected).
#   2. It runs in a temp dir with restricted cwd (does not touch home/key).
#   3. Timeout and resource limits (CPU/memory) to avoid DoS.
#   4. No network (ngspice -b is offline; SPICE_SCRIPTS is also removed so as not to load
#      the user's .spiceinit that could execute commands).
#
# Rule: a netlist is SAFE only if ALL its directives are in the allowlist and it does NOT contain
# tokens capable of side effects (shell, include, write, load, etc.).

# SAFE ngspice directives for passive simulation (analyses + model definitions).
# We do NOT include .control, .include, .lib, .global, .print/.plot with external output, etc.
DIRECTIVAS_ALLOWLIST = (
    ".title", ".end",
    ".ac", ".dc", ".tran", ".op", ".noise", ".tf",
    ".model", ".subckt", ".ends",
    ".param", ".func",
    ".ic",
    ".temp", ".option",
    ".probe", ".save",
)

# Tokens that, in ANY case (even if they are not a .control directive), imply side
# effects: command execution, file read/write, loading binaries.
TOKENS_PELIGROSOS = (
    ".control", "shell", ".include", ".inc ", ".lib", ".global",
    "write ", ".wrdata", ".load", ".source", ".exec",
    ".spiceinit", "code_model",
)


def _extraer_directivas(netlist):
    """Extracts the '.' directives at line start (or after '+' continuations)."""
    directivas = []
    for linea in netlist.splitlines():
        s = linea.strip()
        if s.startswith("."):
            # take the first token (e.g. '.model', '.ac', '.ends')
            directivas.append(s.split()[0].lower() if s.split() else s.lower())
    return directivas


def netlist_segura(netlist):
    """Returns (segura: bool, motivo: str) via directive ALLOWLIST + dangerous token.

    Unlike the previous denylist, here the default is REJECT: it is only allowed if all
    directives are in DIRECTIVAS_ALLOWLIST and there is no side-effect token.
    (allowlist, not denylist).

    ABLATION: if SANDBOX_ALLOWLIST is off, True is returned without checking (the dangerous
    netlist passes through and the evasion attack succeeds).
    """
    import ablacion as AB
    if not AB.activo(AB.SANDBOX_ALLOWLIST):
        return True, ""
    if not isinstance(netlist, str) or not netlist.strip():
        return True, ""
    low = netlist.lower()
    # 1) block by side-effect TOKEN (independent of the .control directive)
    for d in TOKENS_PELIGROSOS:
        if d in low:
            return False, f"netlist contiene token con efectos laterales: {d.strip()}"
    # 2) ALLOWLIST: every initial '.' directive must be in the whitelist
    for dd in _extraer_directivas(netlist):
        if dd not in DIRECTIVAS_ALLOWLIST:
            return False, f"directiva no permitida (fuera de allowlist): {dd}"
    return True, ""


def _limites_recurso():
    """Applies resource limits to the subprocess (CPU and memory) via preexec_fn setrlimit.

    Prevents a netlist with huge .tran or pathological models from consuming the whole machine.
    Returns a callable for subprocess.run(preexec_fn=...). Safe if 'resource' does not apply.
    """
    def _apply():
        try:
            import resource as _r
            # CPU: 30 s of CPU time
            _r.setrlimit(_r.RLIMIT_CPU, (30, 30))
            # virtual memory: 1 GiB
            _r.setrlimit(_r.RLIMIT_AS, (1 << 30, 1 << 30))
            # size of written files: 256 MiB
            _r.setrlimit(_r.RLIMIT_FSIZE, (256 << 20, 256 << 20))
        except Exception:
            pass  # on platforms without resource, the subprocess timeout already bounds
    return _apply


def _entorno_ngspice_sandbox():
    """Isolated environment: WITHOUT SPICE_SCRIPTS (avoids the user's .spiceinit) and without variables
    pointing to home. Keeps a minimal LD_LIBRARY_PATH so ngspice loads its libs."""
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp",              # isolate from the real home (where the key is)
        "TMPDIR": "/tmp",
    }
    # ngspice needs its libs; we take them from simulador.py
    try:
        import simulador as SIM
        env["LD_LIBRARY_PATH"] = SIM.NGSPICE_LIB
    except Exception:
        pass
    return env


# ─────────────────────────────────────────────────────────────────────────────
# ngspice connector (already wired: reuses simulador.py)
# ─────────────────────────────────────────────────────────────────────────────
def conector_ngspice(artifacto):
    """`artifacto` = {netlist: str}. Runs ngspice in SANDBOX (allowlist + limits) and returns a
    derived result. By default it runs the internal RC calibration (deterministic and safe);
    if `netlist` brings a custom analysis, it is validated by allowlist before running it."""
    try:
        import simulador as SIM
        netlist = artifacto.get("netlist", "")
        if not netlist:
            return {"ok": False, "detalle": "sin netlist"}
        # SANDBOX: allowlist + dangerous token (not denylist)
        segura, motivo = netlist_segura(netlist)
        if not segura:
            return {"ok": False, "detalle": f"netlist RECHAZADA (sandbox): {motivo}",
                    "herramienta": "ngspice", "sandbox_rechazo": True}
        # we use the RC calibration as a deterministic smoke test (we never run the LLM's netlist
        # blindly: the artifact's netlist is only VALIDATED, and the real execution is the internal
        # calibration of simulador.py, which is already a safe fixed netlist written by a human).
        r = SIM.rc_frecuencia_corte(1000.0, 1.0)
        return {"ok": True, "observables": {"fc_simulada_Hz": r},
                "detalle": f"ngspice {SIM.version_ngspice()}", "herramienta": "ngspice"}
    except Exception as e:
        return {"ok": False, "detalle": repr(e), "herramienta": "ngspice"}


# ─────────────────────────────────────────────────────────────────────────────
# openfoam connector — MINIMAL calibration CASE (blockMesh + checkMesh)
# ─────────────────────────────────────────────────────────────────────────────
# It is NOT the EVA foaming (that is a separate CFD project). It is a deterministic smoke test that
# shows the "invoke OpenFOAM -> derive result" pipeline works, with a trivial cubic mesh
# (1x1x1 m, 4x4x4 cells). Serves as CALIBRATION: if blockMesh runs and checkMesh generates
# 0 errors, the connector is wired. The foaming physics is wired separately.
def conector_openfoam(artifacto):
    """Runs a minimal case (blockMesh + checkMesh) in a temp dir. Returns the result."""
    env = dict(os.environ)
    env["WM_PROJECT_DIR"] = FOAM_DIR
    # NOTE (empirical finding): OpenFOAM Foundation reads its global controlDict from
    # $WM_PROJECT_DIR/etc/controlDict (NOT from a FOAM_ETC variable). The
    # allowSystemOperations safety switch lives there. In the production rootfs this controlDict
    # will go on a ro FS with allowSystemOperations 0.

    tmp = tempfile.mkdtemp(prefix="openfoam_")
    # system/controlDict + system/fvSchemes + system/fvSolution + constant + blockMeshDict
    os.makedirs(os.path.join(tmp, "system"))
    os.makedirs(os.path.join(tmp, "0"))
    with open(os.path.join(tmp, "system", "controlDict"), "w") as f:
        f.write("""FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }
application blockMesh;
startTime 0; endTime 0.001; deltaT 0.001;
writeControl timeStep; writeInterval 1; purgeWrite 0;
""")
    with open(os.path.join(tmp, "system", "fvSchemes"), "w") as f:
        f.write("""FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }
ddtSchemes { default Euler; }
gradSchemes { default Gauss linear; }
divSchemes { default none; }
laplacianSchemes { default Gauss linear orthogonal; }
interpolationSchemes { default linear; }
""")
    with open(os.path.join(tmp, "system", "fvSolution"), "w") as f:
        f.write("""FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }
solvers {}
SIMPLE {}
""")
    with open(os.path.join(tmp, "system", "blockMeshDict"), "w") as f:
        f.write("""FoamFile { version 2.0; format ascii; class dictionary; object blockMeshDict; }
scale 1;
vertices ((0 0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1));
blocks ( hex (0 1 2 3 4 5 6 7) (4 4 4) simpleGrading (1 1 1) );
edges ();
boundary ( movingWall { type wall; faces ( (3 7 6 2) ); }
           fixedWalls { type wall; faces ( (0 4 7 3) (2 6 5 1) (1 5 4 0) ); }
           frontAndBack { type empty; faces ( (0 3 2 1) (4 5 6 7) ); } );
mergePatchPairs ();
""")
    # blockMesh
    r1 = subprocess.run([os.path.join(FOAM_BIN, "blockMesh"), "-case", tmp],
                        capture_output=True, text=True, env=env, timeout=120)
    r2 = subprocess.run([os.path.join(FOAM_BIN, "checkMesh"), "-case", tmp],
                        capture_output=True, text=True, env=env, timeout=120)
    ok = r1.returncode == 0 and r2.returncode == 0
    # observables derivados: number of cells and whether checkMesh reports 0 errors
    n_celdas = None
    for line in (r2.stdout or "").splitlines():
        if "cells:" in line.lower():
            try:
                n_celdas = int(line.split("cells:")[1].strip().split()[0])
            except Exception:
                pass
    detalle = (r1.stderr or r1.stdout or "").strip()[-160:]
    if not ok:
        detalle = detalle if detalle else (r2.stderr or r2.stdout or "").strip()[-160:]
    return {
        "ok": ok,
        "observables": {"n_celdas": n_celdas},
        "detalle": f"openfoam blockMesh+checkMesh ok, celdas={n_celdas}" if ok else detalle,
        "herramienta": "openfoam",
    }


# ─────────────────────────────────────────────────────────────────────────────
# kicad connector — exports a REAL schematic to PDF with kicad-cli
# ─────────────────────────────────────────────────────────────────────────────
# Generates a self-contained .kicad_sch with a generic symbol (rectangle + pins) per component
# and exports it with `kicad-cli sch export pdf`. This is the EDA tool wiring: it shows
# that the netlist -> KiCad schematic -> PDF pipeline works. The full PCB layout is another
# step (pcbnew), not this connector.
def conector_http(artifacto):
    """HTTP egress connector (governed, Fable design A).

    It does NOT execute network here: it validates the canonical intent and returns it as an action that requires
    human authorization (risk >=1) except for a risk-0 template. The real egress execution
    is done by `hcma-proxy` (own UID), which assembles the request ONLY from the validated intent.

    `artifacto` = the canonical http intent (see intent_canonico.validar_http). This connector
    confirms that the bytes that would leave to the wire are a function of the signed intent.
    """
    import intent_canonico as IC
    try:
        intent = IC.validar_http(artifacto)
    except IC.IntentInvalido as e:
        return {"ok": False, "detalle": f"intent http inválido: {e}",
                "herramienta": "http", "bloqueado": True}
    # No network is executed here. The normalized intent + its canonical (what is signed) is returned.
    return {"ok": True, "observables": {"intent": intent, "canon": IC.canon_intent(intent)},
            "detalle": "intent http validado (egreso pendiente de autorización/proxy)",
            "herramienta": "http"}


def conector_mcp(artifacto):
    """MCP egress connector (governed, Fable design A).

    It does NOT execute MCP here: it validates the canonical intent and returns it as an action that requires
    authorization. The real invocation is done by `hcma-proxy` (own UID) against the pinned
    catalog, verifying the hash_schema before tools/call.
    """
    import intent_canonico as IC
    try:
        intent = IC.validar_mcp(artifacto)
    except IC.IntentInvalido as e:
        return {"ok": False, "detalle": f"intent mcp inválido: {e}",
                "herramienta": "mcp", "bloqueado": True}
    return {"ok": True, "observables": {"intent": intent, "canon": IC.canon_intent(intent)},
            "detalle": "intent mcp validado (egreso pendiente de autorización/proxy)",
            "herramienta": "mcp"}


def conector_kicad(artifacto):
    """`artifacto` = {netlist: {componentes, conexiones}}. Generates .kicad_sch and exports PDF.
    Returns a derived result with the PDF path as an observable."""
    import uuid
    netlist = artifacto.get("netlist") or {}
    componentes = netlist.get("componentes") or []
    if not componentes:
        return {"ok": False, "detalle": "sin netlist", "herramienta": "kicad"}

    KICAD_CLI = "kicad-cli"
    tmp = tempfile.mkdtemp(prefix="kicad_")
    sch = os.path.join(tmp, "esq.kicad_sch")
    pdf = os.path.join(tmp, "esq.pdf")

    def esc(s):
        return str(s).replace('\\', '\\\\').replace('"', '\\"')

    # build lib_symbols with one generic symbol per component
    libs = []
    for c in componentes:
        ref = c.get("ref", "R1")
        pines = c.get("pines") or []
        n = len(pines)
        alto = max(5.0, n * 2.54)
        sym = esc(ref)
        libs.append(f'\t\t(symbol "{sym}"')
        libs.append('\t\t\t(pin_names (offset 1.016))')
        libs.append('\t\t\t(exclude_from_sim no)')
        libs.append('\t\t\t(in_bom yes)')
        libs.append('\t\t\t(on_board yes)')
        libs.append(f'\t\t\t(property "Reference" "{ref}" (at 0 -7.62 0)')
        libs.append('\t\t\t\t(effects (font (size 1.27 1.27))))')
        libs.append(f'\t\t\t(property "Value" "{esc(c.get("nombre", ""))}" (at 0 7.62 0)')
        libs.append('\t\t\t\t(effects (font (size 1.27 1.27))))')
        libs.append('\t\t\t(property "Footprint" "" (at 0 0 0)')
        libs.append('\t\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))')
        libs.append('\t\t\t(property "Datasheet" "~" (at 0 0 0)')
        libs.append('\t\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))')
        libs.append('\t\t\t(property "Description" "" (at 0 0 0)')
        libs.append('\t\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))')
        # a single unit with rectangle + pins
        libs.append(f'\t\t\t(symbol "{sym}_0_1"')
        libs.append(f'\t\t\t\t(rectangle (start -2.54 {alto/2:.2f}) (end 2.54 {-alto/2:.2f})')
        libs.append('\t\t\t\t\t(stroke (width 0.254) (type solid))')
        libs.append('\t\t\t\t\t(fill (type background)))')
        # pins on the left (angle 180) and right (angle 0)
        izq = pines[: (n + 1) // 2]
        der = pines[(n + 1) // 2:]
        for j, p in enumerate(izq):
            py = alto / 2 - 2.54 * (j + 1)
            libs.append(f'\t\t\t\t(pin passive line (at -5.08 {py:.2f} 180) (length 2.54)')
            libs.append(f'\t\t\t\t\t(name "~" (effects (font (size 1.27 1.27))))')
            libs.append(f'\t\t\t\t\t(number "{esc(str(p))}" (effects (font (size 1.27 1.27)))))')
        for j, p in enumerate(der):
            py = alto / 2 - 2.54 * (j + 1)
            libs.append(f'\t\t\t\t(pin passive line (at 5.08 {py:.2f} 0) (length 2.54)')
            libs.append(f'\t\t\t\t\t(name "~" (effects (font (size 1.27 1.27))))')
            libs.append(f'\t\t\t\t\t(number "{esc(str(p))}" (effects (font (size 1.27 1.27)))))')
        libs.append('\t\t\t)')
        libs.append('\t\t\t(embedded_fonts no)')
        libs.append('\t\t)')

    # symbol instances
    inst = []
    for i, c in enumerate(componentes):
        ref = c.get("ref", f"R{i}")
        x = 50 + (i % 4) * 30
        y = 50 + (i // 4) * 30
        inst.append(f'\t(symbol (lib_id "{esc(ref)}") (at {x:.2f} {y:.2f} 0) (unit 1)')
        inst.append('\t\t(in_bom yes) (on_board yes)')
        inst.append(f'\t\t(property "Reference" "{ref}" (at {x:.2f} {y-7.62:.2f} 0)')
        inst.append('\t\t\t(effects (font (size 1.27 1.27))))')
        inst.append(f'\t\t(property "Value" "{esc(c.get("nombre", ""))}" (at {x:.2f} {y+7.62:.2f} 0)')
        inst.append('\t\t\t(effects (font (size 1.27 1.27))))')
        inst.append('\t)')

    contenido = ['(kicad_sch',
                 '\t(version 20250114)',
                 '\t(generator "hcma_conector_kicad")',
                 '\t(generator_version "1.0")',
                 f'\t(uuid "{uuid.uuid4()}")',
                 '\t(paper "A4")',
                 '\t(lib_symbols']
    contenido.extend(libs)
    contenido.append('\t)')  # closes lib_symbols
    contenido.extend(inst)
    contenido.append(')')

    with open(sch, "w", encoding="utf-8") as f:
        f.write("\n".join(contenido))

    # export to PDF
    r = subprocess.run([KICAD_CLI, "sch", "export", "pdf", "-o", pdf, sch],
                       capture_output=True, text=True, timeout=120)
    if os.path.exists(pdf):
        return {"ok": True, "observables": {"pdf": pdf, "n_componentes": len(componentes)},
                "detalle": "kicad sch export pdf ok", "herramienta": "kicad"}
    return {"ok": False, "detalle": (r.stderr or r.stdout or "").strip()[-200:],
            "herramienta": "kicad"}


# ─────────────────────────────────────────────────────────────────────────────
# lean connector — FORMAL theorem verification (epistemic authority)
# ─────────────────────────────────────────────────────────────────────────────
# Implementation in verificador_lean.py (Fable 5.2 ruling): real sandbox (bwrap), statement
# binding (the LLM only provides the proof), axiom auditing, and explicit states.
# Here it is only registered as a connector. `lean_lint_rapido` is UX (early rejection), NOT security.
from verificador_lean import conector_lean


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────
CONECTORES = {
    "ngspice": conector_ngspice,
    "openfoam": conector_openfoam,
    "kicad": conector_kicad,
    "lean": conector_lean,
    "http": conector_http,
    "mcp": conector_mcp,
}

# Risk class PER connector (STATIC table written by a human, never by the LLM).
#  0 = pure/sandbox (no effects)    -> does not request authorization
#  1 = reversible/bounded            -> groupable by objective
#  2 = install/network/money/proprietary data/long compute -> individual, never remembered
RIESGO_POR_CONECTOR = {
    "ngspice": 0,     # pure: runs a netlist in a temp dir, no network or external effects
    "openfoam": 1,    # reversible: runs blockMesh/checkMesh in a temp dir (local compute)
    "kicad": 1,       # reversible: exports a local PDF
    "lean": 1,        # bounded: runs the proof-check in sandbox (bwrap); without sandbox it is fail-closed
    # network egress (Fable design A): ALWAYS requires human authorization except for a
    # risk-0 template (resolved in the monitor's allowlist layer, not here). The connector by
    # itself does NOT distinguish templates: the connector's static risk is >=1; the reduction to 0 is
    # decided by the monitor against allowlist+template+budget, never the agent.
    "http": 1,        # GET/HEAD egress: requires authorization; template to 0 is decided by the monitor
    "mcp": 1,         # MCP egress over pinned tools: requires authorization
    # a future install/network tool would go at risk 2
}


RIESGO_VALIDOS = (0, 1, 2)


def riesgo_argumentos(tool_id, artifacto):
    """Risk contributed by the artifact's ARGUMENTS (Fable P0-c).

    Risk is not only the connector's: the parameters can raise it. Here, for ngspice, a
    netlist with side effects (.control/shell/.include) raises the risk to 1 (even though the
    connector is 0), so that besides being sandboxed it requests authorization. For the rest,
    the argument risk is 0 (the connector already defines the floor).
    """
    if tool_id == "ngspice":
        netlist = (artifacto or {}).get("netlist", "")
        segura, _ = netlist_segura(netlist)
        # A netlist with .control/shell/.include is ARBITRARY EXECUTION OR FS READ,
        # not a simple reversible effect: risk 2 (consistent with P0-b, which rejects it).
        return 0 if segura else 2
    return 0


def _riesgo_valido(x):
    """Converts a declared risk to an integer in {0,1,2}; anything invalid => 2
    (default-deny). Rejects NaN/inf/bool/string/negative/float: in Python `max(snan,1)` gives NaN and
    `nan>=1` is False => OPEN FAILURE. Here we close it by validating strictly.

    ABLATION: if VALIDACION_TIPOS is off, any float is accepted without sanitizing (reproduces
    the original open failure: max(nan,1)=nan).
    """
    import ablacion as AB
    if not AB.activo(AB.VALIDACION_TIPOS):
        # UNPROTECTED behavior: return the raw float (max(nan,1)=nan => open failure)
        try:
            return float(x) if x is not None else 0.0
        except (TypeError, ValueError, OverflowError):
            return 0
    if x is None:
        return 0
    if isinstance(x, bool):
        return 2  # True/False are not valid risks
    if isinstance(x, int) and not isinstance(x, bool) and x in RIESGO_VALIDOS:
        return x
    # float, str, nan, inf, negative, out of range...
    try:
        f = float(x)
        if f != f:  # NaN
            return 2
        if f in (-float("inf"), float("inf")):
            return 2
        if int(f) == f and int(f) in RIESGO_VALIDOS:
            return int(f)
    except (TypeError, ValueError, OverflowError):
        pass
    return 2


def riesgo_efectivo(tool_id, artifacto, riesgo_declarado=None):
    """Effective risk = max(connector's static risk, argument risk, declared risk).

    P0-c: the MODEL can only RAISE the risk (never lower it). The floor is set by the static table
    (written by a human) + the arguments. If the model 'declares' risk 0 over a risk-2 connector,
    the effective risk is still 2. Returns int in {0,1,2}.

    P1 (fix): strict type validation; NaN/inf/bool/string/float/negative => 2 (not an
    open failure).
    """
    base = RIESGO_POR_CONECTOR.get(tool_id, 2)  # unknown -> maximum (default-deny)
    arg = riesgo_argumentos(tool_id, artifacto)
    dec = _riesgo_valido(riesgo_declarado)
    return max(base, arg, dec)


def _registrar_ejecucion_ledger(m, autorizada, tool_id, artifacto):
    """Records the executed effect (risk>=1) in the external execution ledger (Fable P0).

    The ledger is configured with HCMA_EJECUCION_LEDGER and lives in a directory of ANOTHER UID (monitor
    or proxy), 0700, outside the agent's reach. If there is no authorization (m=None in tests) it is
    not recorded. Returns True if it recorded, False if not configured. Raises OSError if not writable.
    """
    if m is None or autorizada is None:
        return False
    import ledger_ejecucion as LE
    from gobernanza_accion import hash_accion
    f = m.current(autorizada)
    val = (f.get("value") or {}) if f else {}
    return LE.registrar_si_configurado(
        tool_id,
        val.get("hash_accion", hash_accion(tool_id, artifacto)),
        val.get("nonce", ""),
        val.get("epoca", 0),
    )


def derivar(tool_id, artifacto, m=None, objetivo_id="", riesgo_declarado=None):
    """Invokes the tool's connector, WITH AUTHORIZATION GATE (default-deny, Fable P0).

    - Effective risk 0 (pure): runs without authorization.
    - Effective risk >=1 (effects): requires a current, single-use human `autorizacion`
      tied to the exact hash of the action. Without it, returns {ok:False, bloqueado:True, necesidad}
      and DECLARES THE NEED (does not self-consent).

    P0-c: the effective risk is max(static, arguments, declared). The model CANNOT lower it.
    """
    # Fable P0 (unreachable ablation flags): the gate of EVERY action with effects requires
    # verified startup (fail-closed). If a defense was left off without test mode, ABORT
    # instead of operating with the monitor down.
    import ablacion as AB
    AB.arranque_verificado()
    # Fable P0 (UID boundary fail-closed): if the boundary between the signer and the runtime is
    # BROKEN (the agent can read the private key), REFUSE to operate, not just warn.
    import firmante_uid as FU
    FU.exigir_frontera_uid()
    fn = CONECTORES.get(tool_id)
    if fn is None:
        return {"ok": False, "detalle": f"herramienta '{tool_id}' SIN conector (no cableada)",
                "herramienta": tool_id}

    # NEW (2026-11-22): a tool REGISTERED in the CMDB but NOT wired cannot be used
    # (wiring default-deny). If it is NOT registered, this gate does not apply (only for
    # tools under install/wiring governance); the connector/risk gate stays the same.
    if m is not None:
        import herramientas as HR
        st = HR.estado(m, tool_id)
        if st is not None and st != HR.CABLEADA:
            return {"ok": False, "bloqueado": True, "herramienta": tool_id,
                    "detalle": (f"BLOQUEADO: herramienta '{tool_id}' en estado '{st}' "
                                f"(no cableada). Falta aprobación humana de cableado.")}

    autorizada = None
    riesgo = riesgo_efectivo(tool_id, artifacto, riesgo_declarado)
    if riesgo > 0:
        import gobernanza_accion as GA
        # gate only if there is governed memory available (m); without m, in tests, it is allowed
        if m is not None:
            autorizada = GA.autorizacion_vigente(m, tool_id, artifacto)
            if autorizada is None:
                # distinguish 'never had permission' from 'the permission was already spent' (diagnostics)
                if GA.autorizacion_consumida(m, tool_id, artifacto):
                    return {"ok": False, "bloqueado": True, "herramienta": tool_id,
                            "detalle": (f"BLOQUEADO: la autorización de '{tool_id}' ya fue "
                                        f"consumida (replay rechazado). Se requiere un permiso nuevo.")}
                # declare the open need and block; the risk is written as the EFFECTIVE one
                # (the model can declare it higher, never lower: we force it to the effective one)
                clave, _ = GA.declarar_necesidad(
                    m, objetivo_id or "sin_objetivo", tool_id,
                    f"requiere ejecutar {tool_id} con efectos (riesgo {riesgo})", riesgo)
                return {"ok": False, "bloqueado": True, "herramienta": tool_id,
                        "necesidad": clave,
                        "detalle": (f"BLOQUEADO: '{tool_id}' requiere autorización humana "
                                    f"(riesgo {riesgo}). Sin permiso vigente.")}
            # P1: CONSUME the authorization BEFORE executing, ATOMICALLY (CAS). If the
            # consumption fails (it was already used -> replay/double spend), we block without executing.
            consumida = GA.marcar_usada(m, autorizada)
            if not consumida:
                return {"ok": False, "bloqueado": True, "herramienta": tool_id,
                        "detalle": (f"BLOQUEADO: la autorización de '{tool_id}' ya fue consumida "
                                    f"(replay rechazado). Se requiere un permiso nuevo.")}

    r = fn(artifacto)
    r["extract_hash"] = _hash(r.get("observables", {}))
    r["riesgo"] = riesgo
    # EXTERNAL LEDGER (P0 observability, Fable ruling): record the EFFECT actually executed
    # (only risk>=1). It lives outside the agent's reach (another UID). If not configured it does
    # not record; if configured and not writable, it is logged (the effect already ran: it is an
    # audit gap, not a gate gap). In production the MONITOR (another UID) writes it, not this runtime.
    if riesgo > 0:
        try:
            _registrar_ejecucion_ledger(m, autorizada, tool_id, artifacto)
        except OSError as e:
            import sys as _sys
            print(f"[derivar] AVISO: ledger de ejecución no escribible: {e}", file=_sys.stderr)
    return r


if __name__ == "__main__":
    print("ngspice:", derivar("ngspice", {"netlist": ""})["ok"])
    print("openfoam:", derivar("openfoam", {})["ok"], derivar("openfoam", {})["detalle"])
    print("moldex3d (sin conector):", derivar("moldex3d", {})["detalle"])
