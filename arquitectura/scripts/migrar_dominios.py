#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""One-shot adapter to migrate a declarative domain from `prototipo.py` to skill_lib.

NOT a permanent layer: it is the "dominio_a_hechos" of Fable's migration (one-shot).
It is run ONCE per domain; afterwards the dict in `prototipo.py` stops being the source of
truth for that skill. Does NOT touch `_run_tests` nor the core, and does NOT touch paper6_osf/.

The critical point (Fable gap 4): the impl MUST be SELF-CONTAINED source code. The
`calcular` functions in prototipo use module constants/helpers (`G`, `RHO_CU`, etc.), so
the adapter injects them as parameters of the compilation environment, NOT via `inspect`.
For `barra_dominadas` the only external dependency is `G` (gravity).

Output format: skill_lib.evaluar expects `fn(inputs) -> dict {output: value}`. prototipo's
`calcular` returns `list of dicts {name, value, unit}`. The adapter wraps
that into a dict to evaluate, and the `tests` dict (tuples) is converted to `tests_dorados`.

Usage:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps \
      python3 arquitectura/scripts/migrar_dominios.py
"""

import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# G (standard gravity) — only external constant of calc_barra_dominadas.
G = 9.80665

import skill_lib  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# SELF-CONTAINED impl of "barra de dominadas" (simply supported beam).
# Copies the logic of prototipo.calc_barra_dominadas + calcular_barra, but:
#   - G is INLINE (self-contained, does not depend on the module)
#   - returns dict {output: value} (not a list of dicts)
#   - keeps the original round() calls so the golden tests match
# ─────────────────────────────────────────────────────────────────────────────
CODIGO_BARRA = '''\
def calcular(inputs):
    G = 9.80665
    m = float(inputs["masa_kg"])
    L = float(inputs["luz_m"])
    k = float(inputs.get("factor_dinamico", 2.0))
    fy = float(inputs.get("fy_mpa", 235.0))
    P = m * G * k
    M_nm = P * L / 4.0
    V_n = P / 2.0
    fb = 0.66 * fy
    W_mm3 = M_nm * 1000.0 / fb
    return {
        "carga_diseno_kN": round(P / 1000.0, 3),
        "momento_maximo_kNm": round(M_nm / 1000.0, 3),
        "cortante_kN": round(V_n / 1000.0, 3),
        "seccion_requerida_cm3": round(W_mm3 / 1000.0, 2),
    }
'''

# Spec in skill_lib format: tests_dorados with explicit id, outputs with tolerance.
# Expected values copied from DOMINIO_BARRA["tests"] (and _run_tests' 0.01 default
# matches the abs tolerance we declare per output).
SPEC_BARRA = {
    "nombre": "barra de dominadas",
    "inputs": [
        {"nombre": "masa_kg", "unidad": "kg"},
        {"nombre": "luz_m", "unidad": "m"},
        {"nombre": "factor_dinamico", "unidad": "1"},
        {"nombre": "fy_mpa", "unidad": "MPa"},
    ],
    "outputs": [
        {"nombre": "carga_diseno_kN", "unidad": "kN", "tolerancia": {"tipo": "abs", "valor": 0.01}},
        {"nombre": "momento_maximo_kNm", "unidad": "kN*m", "tolerancia": {"tipo": "abs", "valor": 0.01}},
        {"nombre": "cortante_kN", "unidad": "kN", "tolerancia": {"tipo": "abs", "valor": 0.01}},
        {"nombre": "seccion_requerida_cm3", "unidad": "cm3", "tolerancia": {"tipo": "abs", "valor": 0.05}},
    ],
    "tests_dorados": [
        {"id": "t1", "inputs": {"masa_kg": 180, "luz_m": 2, "factor_dinamico": 2.0, "fy_mpa": 235},
         "esperado": {"momento_maximo_kNm": 1.765, "seccion_requerida_cm3": 11.38}},
        {"id": "t2", "inputs": {"masa_kg": 180, "luz_m": 2, "factor_dinamico": 3.0, "fy_mpa": 235},
         "esperado": {"momento_maximo_kNm": 2.648, "seccion_requerida_cm3": 17.07}},
        {"id": "t3", "inputs": {"masa_kg": 250, "luz_m": 3, "factor_dinamico": 2.0, "fy_mpa": 235},
         "esperado": {"momento_maximo_kNm": 3.677, "seccion_requerida_cm3": 23.71}},
    ],
}


def migrar_barra(m):
    """One-shot: register spec + propose impl + evaluate. Returns the evaluation result."""
    skill_lib.registrar_spec(m, "barra_dominadas", SPEC_BARRA, fuente="ingeniero")
    v_impl, h = skill_lib.proponer_impl(m, "barra_dominadas", CODIGO_BARRA)
    res = skill_lib.evaluar(m, "barra_dominadas", v_impl)
    return res, h


# ─────────────────────────────────────────────────────────────────────────────
# Domain 2: electric motor (basic sizing).
# calc_potencia_motor + calc_corriente_motor: only depend on `math` (injected into the
# isolated namespace of _compilar). IMPORTANT (migration rule): the impl may NOT
# use `import` (I15 blocks __import__), so constants/helpers go INLINE in the
# code or rely on `math`/`cmath` + already-injected safe builtins.
# ─────────────────────────────────────────────────────────────────────────────
CODIGO_MOTOR = '''\
def calcular(inputs):
    par = float(inputs["par_nm"])
    rpm = float(inputs["rpm"])
    V = float(inputs["tension_V"])
    eta = float(inputs.get("eficiencia", 0.9))
    omega = 2.0 * math.pi * rpm / 60.0
    P_w = par * omega
    I = P_w / (eta * V)
    return {
        "potencia_W": round(P_w, 1),
        "potencia_kW": round(P_w / 1000.0, 3),
        "corriente_A": round(I, 2),
    }
'''

SPEC_MOTOR = {
    "nombre": "motor eléctrico (dimensionado básico)",
    "inputs": [
        {"nombre": "par_nm", "unidad": "N*m"},
        {"nombre": "rpm", "unidad": "rpm"},
        {"nombre": "tension_V", "unidad": "V"},
        {"nombre": "eficiencia", "unidad": "1"},
    ],
    "outputs": [
        {"nombre": "potencia_W", "unidad": "W", "tolerancia": {"tipo": "abs", "valor": 0.1}},
        {"nombre": "potencia_kW", "unidad": "kW", "tolerancia": {"tipo": "abs", "valor": 0.001}},
        {"nombre": "corriente_A", "unidad": "A", "tolerancia": {"tipo": "abs", "valor": 0.01}},
    ],
    "tests_dorados": [
        {"id": "t1", "inputs": {"par_nm": 10, "rpm": 1500, "tension_V": 220, "eficiencia": 0.9},
         "esperado": {"potencia_kW": 1.571, "corriente_A": 7.93}},
        {"id": "t2", "inputs": {"par_nm": 20, "rpm": 1750, "tension_V": 380, "eficiencia": 0.9},
         "esperado": {"potencia_kW": 3.665, "corriente_A": 10.72}},
    ],
}


def migrar_motor(m):
    """One-shot: register spec + propose impl + evaluate for motor_electrico."""
    skill_lib.registrar_spec(m, "motor_electrico", SPEC_MOTOR, fuente="ingeniero")
    v_impl, h = skill_lib.proponer_impl(m, "motor_electrico", CODIGO_MOTOR)
    res = skill_lib.evaluar(m, "motor_electrico", v_impl)
    return res, h


# ─────────────────────────────────────────────────────────────────────────────
# Domain 3: motor winding (copper wire + bushing).
# calc_seccion_cable + calc_resistencia_bobina + calc_peso_cobre + calc_buje_pv.
# INLINE constants (rule: no `import`): RHO_CU, DENS_CU. Only `math` injected.
# ─────────────────────────────────────────────────────────────────────────────
CODIGO_BOBINA = '''\
def calcular(inputs):
    RHO_CU = 0.0171
    DENS_CU = 0.00896
    I = float(inputs["corriente_A"])
    J = float(inputs["densidad_corriente_A_mm2"])
    L = float(inputs["largo_alambre_m"])
    d_mm = float(inputs["diametro_eje_mm"])
    n = float(inputs["velocidad_rpm"])
    W = float(inputs["carga_radial_N"])
    Lb = float(inputs["longitud_buje_mm"])
    A = I / J
    d = math.sqrt(4.0 * A / math.pi)
    R = RHO_CU * L / A
    m = A * L * DENS_CU
    P = W / (d_mm * Lb)
    V = math.pi * (d_mm / 1000.0) * n / 60.0
    return {
        "seccion_cable_mm2": round(A, 3),
        "diametro_cable_mm": round(d, 3),
        "resistencia_ohm": round(R, 3),
        "peso_cobre_kg": round(m, 3),
        "presion_buje_MPa": round(P, 3),
        "velocidad_superficial_m_s": round(V, 3),
        "pv_MPa_m_s": round(P * V, 3),
    }
'''

SPEC_BOBINA = {
    "nombre": "bobinado de motor (alambre de cobre + buje)",
    "inputs": [
        {"nombre": "corriente_A", "unidad": "A"},
        {"nombre": "densidad_corriente_A_mm2", "unidad": "A/mm2"},
        {"nombre": "largo_alambre_m", "unidad": "m"},
        {"nombre": "diametro_eje_mm", "unidad": "mm"},
        {"nombre": "velocidad_rpm", "unidad": "rpm"},
        {"nombre": "carga_radial_N", "unidad": "N"},
        {"nombre": "longitud_buje_mm", "unidad": "mm"},
    ],
    "outputs": [
        {"nombre": "seccion_cable_mm2", "unidad": "mm2", "tolerancia": {"tipo": "abs", "valor": 0.001}},
        {"nombre": "diametro_cable_mm", "unidad": "mm", "tolerancia": {"tipo": "abs", "valor": 0.001}},
        {"nombre": "resistencia_ohm", "unidad": "ohm", "tolerancia": {"tipo": "abs", "valor": 0.001}},
        {"nombre": "peso_cobre_kg", "unidad": "kg", "tolerancia": {"tipo": "abs", "valor": 0.001}},
        {"nombre": "presion_buje_MPa", "unidad": "MPa", "tolerancia": {"tipo": "abs", "valor": 0.001}},
        {"nombre": "velocidad_superficial_m_s", "unidad": "m/s", "tolerancia": {"tipo": "abs", "valor": 0.001}},
        {"nombre": "pv_MPa_m_s", "unidad": "MPa*m/s", "tolerancia": {"tipo": "abs", "valor": 0.001}},
    ],
    "tests_dorados": [
        {"id": "t1",
         "inputs": {"corriente_A": 7.93, "densidad_corriente_A_mm2": 4.0, "largo_alambre_m": 50.0,
                     "diametro_eje_mm": 20.0, "velocidad_rpm": 1500.0,
                     "carga_radial_N": 500.0, "longitud_buje_mm": 25.0},
         "esperado": {"seccion_cable_mm2": 1.982, "resistencia_ohm": 0.431,
                      "peso_cobre_kg": 0.888, "pv_MPa_m_s": 1.571}},
        {"id": "t2",
         "inputs": {"corriente_A": 15.0, "densidad_corriente_A_mm2": 5.0, "largo_alambre_m": 30.0,
                     "diametro_eje_mm": 25.0, "velocidad_rpm": 3000.0,
                     "carga_radial_N": 800.0, "longitud_buje_mm": 30.0},
         "esperado": {"seccion_cable_mm2": 3.0, "resistencia_ohm": 0.171,
                      "peso_cobre_kg": 0.806, "pv_MPa_m_s": 4.189}},
    ],
}


def migrar_bobinado(m):
    """One-shot: register spec + propose impl + evaluate for motor_bobinado."""
    skill_lib.registrar_spec(m, "motor_bobinado", SPEC_BOBINA, fuente="ingeniero")
    v_impl, h = skill_lib.proponer_impl(m, "motor_bobinado", CODIGO_BOBINA)
    res = skill_lib.evaluar(m, "motor_bobinado", v_impl)
    return res, h


if __name__ == "__main__":
    db = os.environ.get("ARQ_MEMORIA_DB", ":memory:")
    if db == ":memory:":
        # we use a temp file for inspection, but for the test in-memory is enough
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_mig_")
        os.close(fd)
        m = HCMAMemoriaSQLite(path)
    else:
        m = HCMAMemoriaSQLite(db)

    def reportar(nombre, skill_id, res, h):
        print("=" * 60)
        print(f"Migración de '{skill_id}' a skill_lib")
        print("=" * 60)
        print(f"  impl_hash    : {h[:16]}...")
        print(f"  todos_ok     : {res['todos_ok']}")
        print(f"  ok_version   : {res.get('ok_version')}")
        print(f"  spec_version : {res['spec_version']}")
        print(f"  tests        : {[t['caso'] + ':' + str(t['pass']) for t in res.get('tests', [])]}")
        ok = m.current(f"skill:{skill_id}:ok:{h}")
        print(f"  ok canónico  : {ok is not None}")
        if ok:
            print(f"    n_casos    : {ok['value']['n_casos']}")
            print(f"    spec_hash  : {ok['value']['spec_hash'][:16]}...")
            print(f"    derived_from #tests: {len(ok['derived_from'])}")

    res, h = migrar_barra(m)
    reportar("barra_dominadas", "barra_dominadas", res, h)

    res2, h2 = migrar_motor(m)
    reportar("motor_electrico", "motor_electrico", res2, h2)

    res3, h3 = migrar_bobinado(m)
    reportar("motor_bobinado", "motor_bobinado", res3, h3)

    print("=" * 60)
