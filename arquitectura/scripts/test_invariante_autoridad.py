#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_invariante_autoridad.py — test de FALSACIÓN del invariante de autoría (P0).

Fable 5.2 (cierre_sesion + pedir_cualquier_cosa): el invariante de la tesis es

    "no existe camino de autor=modelo a un resultado canónico (ni a un PDF autoritativo)
     sin un evento de registro HUMANO."

Este test verifica que la capa HCMA + skill_lib cumplen ese invariante de forma ESTRUCTURAL
(no por convención): ningún hecho producido por el modelo puede auto-promoverse a autoridad.

Qué se falsa (cada caso intenta romper el invariante y debe NO poder):
  1. proponer_impl (modelo) NUNCA queda verified, aunque la spec esté ok.
  2. registrar_spec rechaza fuente='modelo' (solo humano 'ingeniero'/'usuario').
  3. Un hecho 'calculadora' derivado SOLO de una impl unverified no gana autoridad.
  4. 'modelo' no está en FUENTE_CONFIABLE -> write verified=False siempre.

USO:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/test_invariante_autoridad.py
"""
import os
import sys
import tempfile

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite, FUENTE_CONFIABLE  # noqa: E402
import skill_lib  # noqa: E402
import linaje  # noqa: E402


def _mk():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_autoridad_")
    os.close(fd)
    return HCMAMemoriaSQLite(path)


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


# ─────────────────────────────────────────────────────────────────────────────
print("[1] 'modelo' NO está en FUENTE_CONFIABLE")
check("1a. FUENTE_CONFIABLE no contiene 'modelo'", "modelo" not in FUENTE_CONFIABLE,
      f"FUENTE_CONFIABLE={FUENTE_CONFIABLE}")
m = _mk()
# intentar escribir un hecho como el modelo (fuente='modelo', verified=True) -> debe degradar
v = m.write("dato:modelo", {"x": 1}, unit="", fuente="modelo", verified=True)
f = m.current("dato:modelo")
check("1b. write fuente='modelo' con verified=True NO gana autoridad",
      f is None, f"f={f is not None}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] registrar_spec rechaza autor no-humano")
m2 = _mk()
rechazado = False
try:
    skill_lib.registrar_spec(m2, "x", {"nombre": "x", "inputs": [], "outputs": [],
                                       "tests_dorados": [{"id": "t", "inputs": {},
                                                          "esperado": {}}]},
                             fuente="modelo")
except ValueError:
    rechazado = True
check("2a. registrar_spec fuente='modelo' lanza ValueError", rechazado)

# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] proponer_impl es SIEMPRE unverified (aunque la spec sea humana)")
SPEC_VALIDA = {
    "nombre": "cv", "inputs": [],
    "outputs": [{"nombre": "x", "tolerancia": {"tipo": "abs", "valor": 1e-6}}],
    "tests_dorados": [{"id": "t1", "inputs": {}, "esperado": {"x": 0}}],
}
IMPL_SIMPLE = "def calcular(inputs):\n    return {'x': 0}\n"

m3 = _mk()
skill_lib.registrar_spec(m3, "cv", SPEC_VALIDA)
v_impl, h = skill_lib.proponer_impl(m3, "cv", IMPL_SIMPLE,
                                    fuente="modelo", entrypoint="calcular")
f3 = m3.get_version("skill:cv:impl", v_impl)
check("3a. impl propuesta por modelo queda unverified (get_version directo)",
      f3 is not None and not f3["verified"], f"verified={f3['verified'] if f3 else None}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] el grado computado degrada lo derivado de impl unverified")
m4 = _mk()
skill_lib.registrar_spec(m4, "cv", SPEC_VALIDA)
v_impl, h = skill_lib.proponer_impl(m4, "cv", IMPL_SIMPLE,
                                    fuente="modelo", entrypoint="calcular")
# intento ilegítimo de 'resultado' derivado solo de la impl (sin oráculo)
v_r = m4.write("res:0", {"r": 1}, unit="", fuente="calculadora", verified=True,
               derived_from={"skill:cv:impl": v_impl})
fr = m4.current("res:0")
check("4a. resultado derivado de impl unverified NO es canónico",
      fr is None or not fr["verified"], f"verified={(fr or {}).get('verified')}")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
