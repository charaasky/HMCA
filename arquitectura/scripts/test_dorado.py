#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Tests dorados (calculados a mano) para TODOS los dominios + núcleo.

Sin llamadas a LLM: corre local y gratis. Cada dominio declara sus propios tests;
agregar un dominio nuevo trae sus tests sin tocar este runner (más allá de importarlo).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prototipo import DOMINIOS, verificar, HCMAMemoria, FUENTE_CONFIABLE


def qd(qs):
    return {q["name"]: q["value"] for q in qs}


def check(cond, msg):
    if not cond:
        print(f"  ❌ FALLA: {msg}")
        sys.exit(1)
    print(f"  ✅ {msg}")


print("=== Tests dorados de dominios (calculados a mano) ===\n")

for dom_id, dom in DOMINIOS.items():
    print(f"── {dom_id} ──")
    for inputs, expected in dom["tests"]:
        # `calcular` recibe el dict de inputs declarados (calc_inputs), no el HCMA entero.
        qs = dom["calcular"](inputs)
        got = qd(qs)
        for kname, exp in expected.items():
            tol = 0.01
            check(abs(got[kname] - exp) < tol,
                  f"{dom_id} {inputs} → {kname}={exp} (got {got[kname]})")
    print()

print("=== Tests del núcleo ===\n")

# Verificación: rechaza valor absurdo sin unidad (el bug viejo "360")
ok, errs = verificar([{"name": "momento_maximo_kNm", "value": 360, "unit": ""}],
                     {"momento_maximo_kNm": (0.1, 10.0)})
check(not ok, f"rechaza 360 sin unidad ({errs})")

# Verificación: acepta valor correcto con unidad
ok, _ = verificar([{"name": "momento_maximo_kNm", "value": 1.765, "unit": "kN·m"}],
                  {"momento_maximo_kNm": (0.1, 10.0)})
check(ok, "acepta 1.765 kN·m")

# Verificación: rechaza valor fuera de rango
ok, errs = verificar([{"name": "momento_maximo_kNm", "value": 20.0, "unit": "kN·m"}],
                     {"momento_maximo_kNm": (0.1, 10.0)})
check(not ok, f"rechaza 20 kN·m fuera de rango ({errs})")

print()

# stale-use: versión corregida supersede a la vieja (calculadora exige derived_from)
mem = HCMAMemoria()
mem.write("in_factor_dinamico", 2.0, fuente="usuario", verified=True)
v_fd = mem.current("in_factor_dinamico")["version"]
mem.write("momento_maximo_kNm", 1.765, unit="kN·m", fuente="calculadora", verified=True,
          derived_from={"in_factor_dinamico": v_fd})
mem.write("momento_maximo_kNm", 2.648, unit="kN·m", fuente="calculadora", verified=True,
          derived_from={"in_factor_dinamico": v_fd})
check(abs(mem.current("momento_maximo_kNm")["value"] - 2.648) < 0.001,
      "stale-use=0: la versión corregida es la vigente")

# unverified NO colapsa la versión canónica (raíz confiable vs LLM)
mem2 = HCMAMemoria()
mem2.write("x", 1.0, fuente="usuario", verified=True)
mem2.write("x", 2.0, fuente="especialista", verified=False)  # LLM, no confirmado
check(mem2.current("x")["value"] == 1.0, "unverified (LLM) no se vuelve canónico")

# procedencia: LLM no confirma, determinista/humano sí
check("especialista" not in FUENTE_CONFIABLE, "fuente 'especialista' NO confirma")
check("calculadora" in FUENTE_CONFIABLE and "ingeniero" in FUENTE_CONFIABLE,
      "fuente 'calculadora'/'ingeniero' SÍ confirman")

print()

# ── Mejoras (Opus): propagación de contaminación + unidades pint + conflicto ──
from prototipo import _hay_conflicto

# Propagación de contaminación: un input unverified => el output NO es verified
mem3 = HCMAMemoria()
mem3.write("in_masa_kg", 180.0, fuente="busqueda", verified=False)  # input NO verificado
mem3.write("in_luz_m", 2.0, fuente="usuario", verified=True)
mem3.write("in_factor_dinamico", 2.0, fuente="usuario", verified=True)
mem3.write("in_fy_mpa", 235.0, fuente="usuario", verified=True)
check(not mem3.all_verified(["in_masa_kg", "in_luz_m", "in_factor_dinamico", "in_fy_mpa"]),
      "propagación: all_verified=False cuando un input es unverified")

# Unidad no reconocida se rechaza (pint)
ok, errs = verificar([{"name": "momento_maximo_kNm", "value": 1.765, "unit": "foobar"}],
                     {"momento_maximo_kNm": (0.1, 10.0)})
check(not ok, f"rechaza unidad no reconocida 'foobar' ({errs})")
ok, _ = verificar([{"name": "momento_maximo_kNm", "value": 1.765, "unit": "kN·m"}],
                  {"momento_maximo_kNm": (0.1, 10.0)})
check(ok, "acepta unidad válida 'kN·m' (pint)")

# Conflicto modelo vs web (precio: rango del modelo vs punto de la web)
check(_hay_conflicto({"precio": "3.5 - 4.5"}, {"precio": 14.25}),
      "detecta conflicto modelo (rango) vs web (14.25)")
check(not _hay_conflicto({"x": 1.0}, {"x": 1.02}), "no conflicto si difieren <25%")
check(_hay_conflicto({"a": 1}, {"b": 2}) is None, "comparison_unknown cuando no hay claves coincidentes")

# Iteración 2: `verified` es derivado, no declarado
mem4 = HCMAMemoria()
mem4.write("x", 1.0, fuente="busqueda", verified=True)  # intenta auto-certificar web
check(mem4.current("x") is None, "write(verified=True, fuente=busqueda) NO auto-certifica")
check("calculadora(frontera)" not in FUENTE_CONFIABLE, "'calculadora(frontera)' ya no es fuente confiable")

# Stale transitivo: A -> B -> C; al reescribir A, B y C quedan stale
mem5 = HCMAMemoria()
mem5.write("a", 1, fuente="usuario", verified=True)
vA = mem5.current("a")["version"]
mem5.write("b", 10, fuente="calculadora", verified=True, derived_from={"a": vA})
vB = mem5.current("b")["version"]
mem5.write("c", 100, fuente="calculadora", verified=True, derived_from={"b": vB})
mem5.write("a", 2, fuente="usuario", verified=True)  # reescribe A
check(mem5.current("a")["value"] == 2, "A actualizado")
check(mem5.current("b") is None, "B quedó stale (hijo directo)")
check(mem5.current("c") is None, "C quedó stale (transitivo)")

# Iteración 3 (Opus): los 3 tests de convergencia sobre `derived_from`
from prototipo import _ejecutar_calculo

barra = DOMINIOS["barra_dominadas"]

# (1) input con fuente LLM -> resultado NO verified
m_a = HCMAMemoria()
m_a.write("in_masa_kg", 180.0, fuente="especialista", verified=True)  # write deriva False
for n, v in {"luz_m": 2.0, "factor_dinamico": 2.0, "fy_mpa": 235.0}.items():
    m_a.write("in_" + n, v, fuente="usuario", verified=True)
_, verified_a, _, _, _, _ = _ejecutar_calculo(m_a, barra)
check(not verified_a, "(1) input LLM → resultado NO verified")

# (2) inputs confiables -> resultado verified
m_b = HCMAMemoria()
for n, v in {"masa_kg": 180.0, "luz_m": 2.0, "factor_dinamico": 2.0, "fy_mpa": 235.0}.items():
    m_b.write("in_" + n, v, fuente="usuario", verified=True)
qs_b, verified_b, _, df_b, _, _ = _ejecutar_calculo(m_b, barra)
check(verified_b, "(2) inputs confiables → resultado verified")

# (3) reescribir input -> resultado stale
for q in qs_b:
    m_b.write(q["name"], q["value"], unit=q["unit"], fuente="calculadora",
              verified=verified_b, derived_from=df_b)
m_b.write("in_masa_kg", 200.0, fuente="ingeniero", verified=True)
check(m_b.current("momento_maximo_kNm") is None, "(3) reescribir input → resultado stale")

# P2/P3: ambigüedad dimensional + no exponer verified al LLM
ok, errs = verificar([{"name": "momento_maximo_kNm", "value": 1.765, "unit": "J"}],
                     {"momento_maximo_kNm": (0.1, 10.0)})
check(not ok, f"rechaza momento en J (energía, misma dimensión): {errs}")
ok, _ = verificar([{"name": "momento_maximo_kNm", "value": 1.765, "unit": "kN·m"}],
                  {"momento_maximo_kNm": (0.1, 10.0)})
check(ok, "acepta momento en kN·m")

m_c = HCMAMemoria()
m_c.write("x", 1.0, fuente="usuario", verified=True)
check("verified" not in m_c.state()["x"], "state() NO expone `verified` al LLM")

print("\n✅ Todos los tests dorados pasaron (dominios + núcleo + mejoras).")
