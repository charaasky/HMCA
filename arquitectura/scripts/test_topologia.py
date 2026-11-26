#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_topologia.py — tests del gate de honestidad (abstención estructural).

Verifica el paso 1 del plan de Fable: sin topología DECLARADA, el sistema NO puede afirmar
'listo para fabricar' y debe DEGRADAR la afirmación, listando las premisas faltantes.

Casos:
  1. fórmulas sueltas (topología ausente) -> grado formula_suelta, no se puede afirmar fabricable.
  2. topología asumida (propuesta por modelo) -> derivado_bajo_supuesto, tampoco fabricable.
  3. topología declarada (ratificada por humano) -> conectado (pero aún no 'fabricable' sin
     realización a pieza).
  4. la propagación por mínimo: un valor con topología ausente arrastra todo el entregable a
     formula_suelta (no se puede "compensar" con un valor declarado).
  5. degradar_afirmacion devuelve la clase real + faltantes.

USO:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/test_topologia.py
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import topologia as T  # noqa: E402


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


print("[1] topología ausente (fórmulas sueltas) => formula_suelta")
g, falt = T.grado_entregable([T.AUSENTE, T.AUSENTE])
check("1a. grado real = formula_suelta", g == "formula_suelta", f"g={g}")
ok, _ = T.afirmacion_permitida(g, "fabricable")
check("1b. NO se puede afirmar 'fabricable'", not ok)
ok2, _ = T.afirmacion_permitida(g, "formula_suelta")
check("1c. SÍ se puede afirmar 'formula_suelta' (honesto)", ok2)

print("\n[2] topología asumida (propuesta por modelo) => no fabricable")
g2, _ = T.grado_entregable([T.ASUMIDA])
check("2a. grado = derivado_bajo_supuesto", g2 == "derivado_bajo_supuesto", f"g={g2}")
ok, _ = T.afirmacion_permitida(g2, "fabricable")
check("2b. asumida NO alcanza para fabricable", not ok)

print("\n[3] topología declarada (ratificada por humano) => conectado")
g3, _ = T.grado_entregable([T.DECLARADA])
check("3a. grado = conectado", g3 == "conectado", f"g={g3}")
ok, _ = T.afirmacion_permitida(g3, "fabricable")
check("3b. conectado aún NO es 'fabricable' (falta realización a pieza)", not ok)

print("\n[4] propagación por mínimo: un valor ausente arrastra todo")
g4, falt4 = T.grado_entregable([T.DECLARADA, T.DECLARADA, T.AUSENTE])
check("4a. con un solo ausente, el grado cae a formula_suelta",
      g4 == "formula_suelta", f"g={g4}")
check("4b. y lista la premisa faltante", any("topología" in f for f in falt4))

print("\n[5] degradar_afirmacion devuelve la clase real + faltantes")
d = T.degradar_afirmacion("formula_suelta", "fabricable", ["topología no declarada"])
check("5a. degradada=True", d["degradada"])
check("5b. clase real = formula_suelta (no fabricable)", d["afirmacion"] == "formula_suelta")
check("5c. faltantes no vacío", len(d["faltantes"]) > 0)
d2 = T.degradar_afirmacion("conectado", "conectado", [])
check("5d. si el grado alcanza, no degrada", not d2["degradada"] and d2["afirmacion"] == "conectado")

# ─────────────────────────────────────────────────────────────────────────────
# PASO 2: cobertura de mecanismo — el sistema se NIEGA a afirmar una función
# no cubierta por la clase de modelo (P0 de grado inflado, D2 generalizada)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] cobertura de mecanismo (modelo lineal no cubre saturación/regulación)")
cubre, motivo = T.cobertura_modelo(T.MODELO_LINEAL, "regulacion")
check("6a. modelo lineal NO cubre 'regulacion' (no-lineal)", not cubre, motivo)
cubre2, _ = T.cobertura_modelo(T.MODELO_PARAMETRICO, "regulacion")
check("6b. modelo parametrico SÍ cubre el mecanismo (con grado topeado)", cubre2)
cubre3, _ = T.cobertura_modelo(T.MODELO_LINEAL, "lineal")
check("6c. modelo lineal SÍ cubre un mecanismo lineal", cubre3)

print("\n[7] el sistema se NIEGA a promover 'el CVT regula' con modelo lineal")
r = T.negarse_a_promover("el CVT regula ±1%", "regulacion", T.MODELO_LINEAL)
check("7a. promueve=False (abstiene)", not r["promueve"])
check("7b. grado queda 'conectado' (no 'funcional')", r["grado"] == "conectado", f"g={r['grado']}")
check("7c. motivo explica que el modelo no cubre el mecanismo",
      "no cubierto" in r["motivo"].lower() or "no cubre" in r["motivo"].lower() or
      "no cubierto" in r["motivo"], r["motivo"])

print("\n[8] con modelo parametrico, SÍ puede afirmar funcional (con grado topeado)")
r2 = T.negarse_a_promover("el CVT regula", "regulacion", T.MODELO_PARAMETRICO)
check("8a. promueve=True", r2["promueve"])
check("8b. grado = funcional", r2["grado"] == "funcional", f"g={r2['grado']}")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
