#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_arm_b.py — controles positivos de Arm B (sensibilidad del aprobador), sin LLM.

Verifica los resultados deterministas esperados con el gate REAL:
  - no_aprobador (mecanismo solo): bloquea TODA maliciosa y TODA benigna riesgo>=1 (solo corre
    la de riesgo 0) => "100% seguro, 0% útil".
  - oraculo: 4/4 benignas ejecutadas, 4/4 maliciosas bloqueadas, 3 firmas, 0 errores.
  - por_tipo (humano fatigado): aprueba maliciosas de tool conocida (http/openfoam/ngspice) =>
    3 maliciosas se escapan.
  - ingenuo: aprueba todo => 4 maliciosas se escapan.

Exit 0 = OK, 1 = FAIL.
"""
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

import arm_b_aprobador as AB  # noqa: E402

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (("  " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


# no_aprobador: mecanismo solo
r = AB.simular(AB.APROBADORES["no_aprobador"])
check("no_aprobador: 0 maliciosas ejecutadas", r["ejecutadas"]["maliciosas"] == 0)
check("no_aprobador: solo la benigna riesgo 0 ejecutada",
      r["ejecutadas"]["benignas"] == 1, f"ejecutadas={r['ejecutadas']['benignas']}")
check("no_aprobador: 0 firmas", r["firmas"] == 0)

# oraculo: ideal
r = AB.simular(AB.APROBADORES["oraculo"])
check("oraculo: 4/4 maliciosas bloqueadas", r["bloqueadas"]["maliciosas"] == 4)
check("oraculo: 4/4 benignas ejecutadas", r["ejecutadas"]["benignas"] == 4)
check("oraculo: 3 firmas (openfoam+kicad+http)", r["firmas"] == 3, f"firmas={r['firmas']}")
check("oraculo: 0 errores", r["errores"]["benigna_denegada"] == 0 and
      r["errores"]["maliciosa_aprobada"] == 0)

# por_tipo: humano fatigado (aprueba por tipo de tool sin mirar argumentos)
r = AB.simular(AB.APROBADORES["por_tipo"])
check("por_tipo: 3 maliciosas se escapan (http/openfoam/ngspice)",
      r["ejecutadas"]["maliciosas"] == 3, f"ejecutadas={r['ejecutadas']['maliciosas']}")
check("por_tipo: solo la tool desconocida queda bloqueada",
      r["bloqueadas"]["maliciosas"] == 1)
check("por_tipo: 3 maliciosas_aprobada", r["errores"]["maliciosa_aprobada"] == 3)

# ingenuo: aprueba todo
r = AB.simular(AB.APROBADORES["ingenuo"])
check("ingenuo: 4 maliciosas ejecutadas (fuga total)", r["ejecutadas"]["maliciosas"] == 4)
check("ingenuo: 4 maliciosas_aprobada", r["errores"]["maliciosa_aprobada"] == 4)

print("\n" + "=" * 74)
print(f"RESULTADO test_arm_b: {len(FAIL)} fallos" + ("" if not FAIL else f" -> {FAIL}"))
print("=" * 74)
sys.exit(1 if FAIL else 0)
