#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_verificacion_simbolica.py — controles positivos de la capa simbólica (sympy) + transformer.

Verifica que el verificador formal:
  - aprueba una ley física coherente (potencia P=V*I);
  - aprueba un transformador coherente (relación de vueltas + conservación de potencia);
  - RECHAZA el caso de alucinación clásico '220V 1A -> 9V 0.5A' (220W in vs 4.5W out);
  - resuelve la incógnita correcta de forma determinista;
  - descubre la plantilla desde un grafo con bloque `transformador`.

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

import verificacion_simbolica as VS      # noqa: E402
import verificador_fisico as VF          # noqa: E402

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (("  " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


# potencia coherente
r = VS.verificar_identidad(VS.ECUACIONES["potencia"], {VS.V: 220, VS.I: 1, VS.P: 220})
check("potencia 220V*1A=220W coherente", r["ok"] is True, r["detalle"])

# transformador coherente: 220V 1A -> 110V 2A, N 200:100
p = VF.plantilla_transformador(V_in=220, V_out=110, I_in=1, I_out=2, N_in=200, N_out=100)
v = VF.verificar(p)
check("transformador coherente (220->110, N 200:100)", v["coherente"] is True,
      f"fuente={v.get('fuente_simulacion')}")

# control negativo: 220V 1A -> 9V 0.5A (220W vs 4.5W) DEBE ser incoherente
p_mal = VF.plantilla_transformador(V_in=220, V_out=9, I_in=1, I_out=0.5)
v_mal = VF.verificar(p_mal)
check("transformador 220W in vs 4.5W out => INCOHERENTE (control negativo)",
      v_mal["coherente"] is False)

# resolver determinista
p_correcta = VS.resolver(VS.ECUACIONES["potencia"], VS.P, {VS.V: 9, VS.I: 0.5})
check("resolver P=V*I con V=9,I=0.5 => 4.5", abs(p_correcta - 4.5) < 1e-9, f"P={p_correcta}")

# descubrimiento por grafo
grafo = {"bloques": [{"tipo": "transformador", "V_in": 220, "V_out": 110,
                      "I_in": 1, "I_out": 2, "N_in": 200, "N_out": 100}]}
plantillas = VF.plantillas_para_grafo(grafo)
check("plantillas_para_grafo descubre transformador", len(plantillas) == 1,
      f"n={len(plantillas)}")

print("\n" + "=" * 74)
print(f"RESULTADO test_verificacion_simbolica: {len(FAIL)} fallos"
      + ("" if not FAIL else f" -> {FAIL}"))
print("=" * 74)
sys.exit(1 if FAIL else 0)
