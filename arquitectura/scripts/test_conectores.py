#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_conectores.py — test del CABLEADO de herramientas.

Principio del usuario: 'toda nueva herramienta debe ser cableada'. Detectarla (capacidades.py)
NO alcanza: debe haber un conector que la invoque. Este test falsa que:

QUÉ LO FALSARÍA:
  - Si una herramienta DETECTADA pero SIN conector (ej. moldex3d) derivara 'ok=True', estaríamos
    fingiendo un cableado que no existe.
  - Si openfoam (disponible) no derivara un case mínimo determinista, el conector estaría roto.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import conectores as CNX  # noqa: E402
import capacidades as CAP  # noqa: E402

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


print("=" * 74)
print("[1] cada herramienta disponible con conector DERIVA un resultado")
print("=" * 74)
r_foam = CNX.derivar("openfoam", {})
check("1a. openfoam deriva case mínimo determinista", r_foam["ok"] is True, r_foam)
check("1b. openfoam extrae observable (n_celdas=64)", r_foam.get("observables", {}).get("n_celdas") == 64,
      r_foam.get("observables"))
check("1c. extract_hash presente", bool(r_foam.get("extract_hash")), r_foam.get("extract_hash"))

print("\n[2] FALSACIÓN: herramienta DETECTADA pero SIN conector NO deriva 'ok'")
r_moldex = CNX.derivar("moldex3d", {})
check("2a. moldex3d (sin conector) => ok=False", r_moldex["ok"] is False, r_moldex)
check("2b. declara 'no cableada'", "conector" in r_moldex["detalle"], r_moldex["detalle"])

print("\n[3] determinismo del conector openfoam (dos corridas = mismo extract_hash)")
r1 = CNX.derivar("openfoam", {})
r2 = CNX.derivar("openfoam", {})
check("3a. mismo extract_hash en dos corridas", r1["extract_hash"] == r2["extract_hash"],
      f"{r1['extract_hash']} vs {r2['extract_hash']}")

print("\n[4] coherencia con capacidades: lo disponible se puede cablear")
disp = CAP.verificar_herramienta("openfoam")["disponible"]
check("4a. openfoam disponible y con connector", disp is True and "openfoam" in CNX.CONECTORES,
      f"disponible={disp}")

print("\n[5] conector kicad: netlist -> .kicad_sch -> PDF (EDA cableado)")
netlist = {
    "componentes": [
        {"ref": "R1", "nombre": "10M", "pines": [1, 2]},
        {"ref": "U1", "nombre": "CD4060", "pines": [8, 9, 10, 12, 16]},
    ],
    "conexiones": [],
}
r_k = CNX.derivar("kicad", {"netlist": netlist})
check("5a. kicad deriva PDF", r_k["ok"] is True, r_k["detalle"])
check("5b. observable n_componentes=2", r_k.get("observables", {}).get("n_componentes") == 2,
      r_k.get("observables"))

print("\n[6] P0-b: SANDBOX de ngspice (allowlist + efectos laterales se RECHAZAN)")
r_ctl = CNX.derivar("ngspice", {"netlist": ".control\\nshell echo hack\\n.endc"})
check("6a. .control/shell => ok=False", r_ctl["ok"] is False, r_ctl)
check("6b. declara rechazo de sandbox", r_ctl.get("sandbox_rechazo") is True, r_ctl)
check("6c. no se ejecuta", "RECHAZADA" in r_ctl.get("detalle", ""), r_ctl.get("detalle"))
r_inc = CNX.derivar("ngspice", {"netlist": ".include /etc/passwd"})
check("6d. .include => ok=False (sandbox)", r_inc["ok"] is False, r_inc)

print("\n[6-bis] allowlist (no denylist): variantes/protección contra evasión")
r_inc2 = CNX.derivar("ngspice", {"netlist": ".inc /etc/passwd"})
check("6e. abreviatura .inc => rechazado", r_inc2["ok"] is False, r_inc2)
r_lib = CNX.derivar("ngspice", {"netlist": ".lib /etc/passwd"})
check("6f. .lib => rechazado", r_lib["ok"] is False, r_lib)
r_upper = CNX.derivar("ngspice", {"netlist": ".CONTROL\\nshell\\n.ENDC"})
check("6g. mayúsculas .CONTROL => rechazado", r_upper["ok"] is False, r_upper)
r_spiceinit = CNX.derivar("ngspice", {"netlist": ".spiceinit"})
check("6h. .spiceinit => rechazado", r_spiceinit["ok"] is False, r_spiceinit)
# directiva FUERA de allowlist (ej. .alter, .sens) también se rechaza
r_alter = CNX.derivar("ngspice", {"netlist": ".alter"})
check("6i. directiva fuera de allowlist (.alter) => rechazada", r_alter["ok"] is False, r_alter)

print("\n[7] P0-c: riesgo EFECTIVO = max(estático, argumentos, declarado)")
check("7a. ngspice netlist limpia => riesgo 0", CNX.riesgo_efectivo("ngspice", {"netlist": "R1 1 2 1k"}) == 0)
check("7b. ngspice netlist .control => riesgo 2 (ejecución arbitraria)",
      CNX.riesgo_efectivo("ngspice", {"netlist": ".control"}) == 2)
check("7c. openfoam conector riesgo 1, declarado 0 => sigue 1 (modelo no baja)",
      CNX.riesgo_efectivo("openfoam", {}, riesgo_declarado=0) == 1)
check("7d. openfoam declarado 2 => sube a 2 (modelo solo puede subir)",
      CNX.riesgo_efectivo("openfoam", {}, riesgo_declarado=2) == 2)
check("7e. desconocido => riesgo 2 (default-deny)",
      CNX.riesgo_efectivo("herramienta_que_no_existe", {}) == 2)

print("\n[7-bis] P1: validación estricta de tipos (NO fallo abierto por NaN/bool/float)")
import math
check("7f. riesgo_declarado=NaN => 2 (no fallo abierto)",
      CNX.riesgo_efectivo("openfoam", {}, riesgo_declarado=float("nan")) == 2)
check("7g. riesgo_declarado=inf => 2",
      CNX.riesgo_efectivo("openfoam", {}, riesgo_declarado=float("inf")) == 2)
check("7h. riesgo_declarado=True (bool) => 2 (no 1)",
      CNX.riesgo_efectivo("openfoam", {}, riesgo_declarado=True) == 2)
check("7i. riesgo_declarado='algo' (str) => 2",
      CNX.riesgo_efectivo("openfoam", {}, riesgo_declarado="algo") == 2)
check("7j. riesgo_declarado=-1 => 2",
      CNX.riesgo_efectivo("openfoam", {}, riesgo_declarado=-1) == 2)
check("7k. riesgo_declarado=1.5 (float no entero) => 2",
      CNX.riesgo_efectivo("openfoam", {}, riesgo_declarado=1.5) == 2)
check("7l. riesgo_declarado=0 entero => respeta (sigue piso 1 de openfoam)",
      CNX.riesgo_efectivo("openfoam", {}, riesgo_declarado=0) == 1)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
