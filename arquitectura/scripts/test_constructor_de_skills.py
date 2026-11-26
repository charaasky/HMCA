#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_constructor_de_skills.py — test de falsación del orquestador de skills (P0 corregido).

Prueba la NO-ESCALADA: el orquestador fabrica un skill CANDIDATO (autor=modelo) y la física
se usa SOLO como VETO (refutar absurdos), NUNCA como promoción. El grado máximo SIN firma humana
es 1 (derivado_bajo_supuesto). La canonicidad (>=2) exige firma humana.

QUÉ LO FALSARÍA:
  - Si un diseño correcto quedara 'canonico' sin firma humana, habría auto-promoción (P0).
  - Si una impl MENTIROSA (devuelve 999 Hz) pasara la evaluación, el veto no funciona.
  - Si una impl CONSTANTE con nombre de observable spoofeado ('f_salida_Hz' devuelto por un
    PLL/teclado) quedara 'canónico', el binding de cota sería explotable (P1 de Fable).
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import constructor_de_skills as CS  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402

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


def mem():
    return HCMAMemoriaSQLite(":memory:")


DISENO_OK = {
    "nombre": "reloj_electronico",
    "inputs": [{"name": "f_cristal_Hz", "question": "frecuencia", "unit": "Hz"}],
    "formulas": ["f_salida_Hz = f_cristal_Hz / 2^15"],
    "codigo_impl": "def calcular(inputs):\n"
                   "    f = float(inputs['f_cristal_Hz'])\n"
                   "    return {'f_salida_Hz': f / (2 ** 15)}\n",
    "tests_dorados": [
        {"id": "t1", "inputs": {"f_cristal_Hz": 32768.0}, "esperado": {"f_salida_Hz": 1.0}},
    ],
    "esquema": "cristal -> divisor -> display",
    "materiales": [["cristal_32k768", 1, "unidad"]],
    "dimensiones": {"aprox_pcb_mm": "120 x 80"},
    "lookup_queries": [],
}

# impl que devuelve 999 Hz (físicamente inviable, viola la cota de 1 Hz)
DISENO_MENTIROSO = dict(DISENO_OK)
DISENO_MENTIROSO["codigo_impl"] = "def calcular(inputs):\n    return {'f_salida_Hz': 999.0}\n"

# impl CONSTANTE que devuelve exactamente 1.0 (hardcodea el valor, no calcula física)
DISENO_CONSTANTE = dict(DISENO_OK)
DISENO_CONSTANTE["codigo_impl"] = "def calcular(inputs):\n    return {'f_salida_Hz': 1.0}\n"

# impl que PASA el veto (1.0 Hz dentro de cota) pero NO reconcilia los tests (esperado 2.0)
DISENO_INCONSISTENTE = dict(DISENO_OK)
DISENO_INCONSISTENTE["codigo_impl"] = "def calcular(inputs):\n    return {'f_salida_Hz': 1.0}\n"
DISENO_INCONSISTENTE["tests_dorados"] = [
    {"id": "t1", "inputs": {"f_cristal_Hz": 32768.0}, "esperado": {"f_salida_Hz": 2.0}},
]


print("=" * 74)
print("[1] P0 corregido: diseño correcto queda CANDIDATO (grado 1), NO canónico")
print("=" * 74)
m = mem()
r = CS.construir_skill(m, "diseñame un reloj", diseño=DISENO_OK, usar_llm=False)
check("1a. estado no_vetado (pasó el veto, NO 'verificado')",
      r["estado"] == "no_vetado", r["estado"])
check("1b. evaluar consistencia todos_ok", r["evaluar"]["todos_ok"] is True, r["evaluar"])
check("1c. grado <= 1 (derivado_bajo_supuesto), NO canonico",
      r["grado_skill"] == "derivado_bajo_supuesto", r["grado_skill"])
check("1d. veto de física pasa (1.0 Hz dentro de cota)", r["veto_fisica"]["ok"] is True,
      r["veto_fisica"])

print("\n[2] FALSACIÓN: impl mentirosa (999 Hz) VIOLA el veto físico")
m2 = mem()
r2 = CS.construir_skill(m2, "reloj", diseño=DISENO_MENTIROSO, usar_llm=False)
check("2a. veto_fisica NO pasa (999 Hz fuera de cota)", r2["veto_fisica"]["ok"] is False,
      r2["veto_fisica"])
check("2b. estado = inviable_fisicamente (no fabrica)", r2["estado"] == "inviable_fisicamente",
      r2["estado"])
check("2c. grado = derivado_bajo_supuesto (procedencia), el veto NO degrada el grado",
      r2["grado_skill"] == "derivado_bajo_supuesto", r2["grado_skill"])

print("\n[3] ADVERSARIAL (P1): impl CONSTANTE no sube de grado (no hay promoción)")
m3 = mem()
r3 = CS.construir_skill(m3, "reloj", diseño=DISENO_CONSTANTE, usar_llm=False)
check("3a. impl constante NO queda 'canónico' (techo grado 1)",
      r3["grado_skill"] != "canonico", r3["grado_skill"])

print("\n[4] sin impl (abstención) => declara sin_impl, no inventa")
m4 = mem()
d4 = dict(DISENO_OK); d4["codigo_impl"] = ""
r4 = CS.construir_skill(m4, "x", diseño=d4, usar_llm=False)
check("4a. estado sin_impl", r4["estado"] == "sin_impl", r4["estado"])

print("\n[5] FALSACIÓN de coherencia: veto=True + evaluar=False => grado 1, NO 'desconocido'")
m5 = mem()
r5 = CS.construir_skill(m5, "reloj", diseño=DISENO_INCONSISTENTE, usar_llm=False)
check("5a. veto pasa (1.0 Hz dentro de cota)", r5["veto_fisica"]["ok"] is True, r5["veto_fisica"])
check("5b. evaluar consistencia False (esperado 2.0 != 1.0)",
      r5["evaluar"]["todos_ok"] is False, r5["evaluar"])
check("5c. grado = derivado_bajo_supuesto (NO 'desconocido')",
      r5["grado_skill"] == "derivado_bajo_supuesto", r5["grado_skill"])
check("5d. estado = candidato_con_inconsistencia_interna",
      r5["estado"] == "candidato_con_inconsistencia_interna", r5["estado"])

print("\n[6] ORTOGONALIDAD grado/veto (test que pide Fable): grado idéntico pase o falle el veto")
check("6a. grado idéntico con veto OK y veto FALLA (grado != f(veto))",
      r["grado_skill"] == r2["grado_skill"] == "derivado_bajo_supuesto",
      f"r={r['grado_skill']} r2={r2['grado_skill']}")
check("6b. lo que cambia con el veto es el ESTADO, no el grado",
      r["estado"] != r2["estado"], f"{r['estado']} vs {r2['estado']}")

print("\n[7] P0 'testbench no del agente': el veto se ata a FÍSICA CERRADA, no a tests_dorados")
check("7a. testbench_hash presente (derivado de cota de física, no del diseño)",
      bool(r.get("testbench_hash")), r.get("testbench_hash"))
check("7b. testbench_hash es ESTABLE (no depende de los tests_dorados del diseño)",
      r["testbench_hash"] == r2["testbench_hash"] == r5["testbench_hash"],
      f"{r['testbench_hash']} vs {r2['testbench_hash']}")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
