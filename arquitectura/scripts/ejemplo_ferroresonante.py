#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Corrective example: the skills system detects that it CANNOT validate a CVT design.

This example replaces the previous `ejemplo_ferroresonante.py`, which was the FAILURE:
it declared a golden test with physically absurd values (Bmax=1.5 T, Et=0.5 => core
15 cm² for 2 kVA) with `fuente='ingeniero'`, and the system promoted it to canonical `ok`.

With option D implemented (computed lineage degree), the system now:
  1. Recognizes that the CVT skill has NO canonical source (manufacturer industrial secret),
     so its `ok` stays at degree `conforme` (it verifies the Faraday arithmetic, NOT the
     physical validity of the design).
  2. If a sanity bound is defined and the case violates it, the system MARKS it as
     physically implausible and does NOT promote it to canonical.
  3. Openly shows WHAT it knows and WHAT it does not know, instead of self-certifying a number.

This is the honest demonstration of the mechanism, not a CVT design "skill".

Usage:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps \
      python3 arquitectura/scripts/ejemplo_ferroresonante.py
"""

import os
import sys
import tempfile

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import skill_lib  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Self-contained impl: applies Faraday's law (4.44) + LC resonance + reactive power.
# It is CORRECT arithmetic given a set of inputs. What it CANNOT do is validate
# whether those inputs (Bmax, Et, factor_Qc) are physically correct for a real CVT.
# ─────────────────────────────────────────────────────────────────────────────
CODIGO_FERRORESONANTE = '''\
def calcular(inputs):
    Vin = float(inputs["Vin"])
    S = float(inputs["S"])
    f = float(inputs["f"])
    Bmax = float(inputs["Bmax"])
    Et = float(inputs.get("Et", 0.5))
    factor_Qc = float(inputs.get("factor_Qc", 3.0))
    omega = 2.0 * math.pi * f
    A = Et / (4.44 * f * Bmax)          # m^2
    N1 = Vin / Et
    I = S / Vin
    Qc = factor_Qc * S
    C = Qc / (Vin * Vin * omega)        # F
    L = 1.0 / (omega * omega * C)       # H
    return {
        "omega_rad_s": round(omega, 2),
        "area_nucleo_cm2": round(A * 10000.0, 2),
        "vueltas_primario": round(N1, 2),
        "corriente_A": round(I, 2),
        "Qc_var": round(Qc, 2),
        "C_uF": round(C * 1e6, 1),
        "L_mH": round(L * 1e3, 2),
    }
'''

# ─────────────────────────────────────────────────────────────────────────────
# Spec WITHOUT a canonical source. The golden cases do NOT carry `fuente`: therefore the
# resulting `ok` will stay at degree 'conforme' (arithmetic consistency), NOT 'canonico'.
# The design selections (Et, factor_Qc) are INPUTS [UNVERIFIED], not constants.
# ─────────────────────────────────────────────────────────────────────────────
SPEC_CVT = {
    "nombre": "dimensionado ferroresonante (Faraday + resonancia LC)",
    "inputs": [
        {"nombre": "Vin", "unidad": "V"},
        {"nombre": "S", "unidad": "VA"},
        {"nombre": "f", "unidad": "Hz"},
        {"nombre": "Bmax", "unidad": "T", "nota": "selección de diseño [UNVERIFIED]"},
        {"nombre": "Et", "unidad": "V", "nota": "tensión/vuelta, selección de diseño [UNVERIFIED]"},
        {"nombre": "factor_Qc", "unidad": "1", "nota": "Qc/S, selección de diseño [UNVERIFIED]"},
    ],
    "outputs": [
        {"nombre": "omega_rad_s", "unidad": "rad/s", "tolerancia": {"tipo": "abs", "valor": 0.01}},
        {"nombre": "area_nucleo_cm2", "unidad": "cm2", "tolerancia": {"tipo": "abs", "valor": 0.01}},
        {"nombre": "vueltas_primario", "unidad": "1", "tolerancia": {"tipo": "abs", "valor": 0.5}},
        {"nombre": "corriente_A", "unidad": "A", "tolerancia": {"tipo": "abs", "valor": 0.01}},
        {"nombre": "Qc_var", "unidad": "VAR", "tolerancia": {"tipo": "abs", "valor": 1.0}},
        {"nombre": "C_uF", "unidad": "uF", "tolerancia": {"tipo": "abs", "valor": 0.1}},
        {"nombre": "L_mH", "unidad": "mH", "tolerancia": {"tipo": "abs", "valor": 0.01}},
    ],
    # No `fuente` in any case => the oracle is absent by design.
    "tests_dorados": [
        {"id": "caso_nominal",
         "inputs": {"Vin": 220.0, "S": 2000.0, "f": 50.0, "Bmax": 1.5, "Et": 0.5, "factor_Qc": 3.0},
         "esperado": {"omega_rad_s": 314.16, "area_nucleo_cm2": 15.02,
                      "vueltas_primario": 440.0, "corriente_A": 9.09,
                      "Qc_var": 6000.0, "C_uF": 394.6, "L_mH": 25.68}},
    ],
}


def run():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_cvt_correccion_")
    os.close(fd)
    m = HCMAMemoriaSQLite(path)
    sid = "cvt_dimensionado"

    print("=" * 78)
    print("SISTEMA CORRIGIÉNDOSE: el CVT no puede auto-certificarse como diseño")
    print("=" * 78)

    # 1-3. spec + impl + evaluate
    v_spec = skill_lib.registrar_spec(m, sid, SPEC_CVT, fuente="ingeniero")
    v_impl, h = skill_lib.proponer_impl(m, sid, CODIGO_FERRORESONANTE)
    res = skill_lib.evaluar(m, sid, v_impl)

    # 4. The COMPUTED degree: no canonical source -> 'conforme', NOT 'canonico'
    grado = skill_lib.grado_skill(m, sid, h)
    print(f"[1] evaluar          -> todos_ok={res['todos_ok']} (la aritmética reprodujo el caso)")
    print(f"[2] grado computado  -> '{grado}'  ← NO 'canonico'")
    print()
    print("  ¿Por qué? Porque ningún test dorado tiene `fuente:`/`cota:*` en su linaje.")
    print("  Los valores esperados del 'caso_nominal' salieron de una SELECCIÓN DE DISEÑO")
    print("  (Et=0.5, factor_Qc=3) INVENTADA, no de una tabla/norma/medición canónica.")
    print("  El sistema ya no miente: verifica la aritmética, no la validez física.")
    print()

    # 5. Show the number that BEFORE was self-certified
    fn = skill_lib._compilar(CODIGO_FERRORESONANTE, "calcular")
    out = fn({"Vin": 220.0, "S": 2000.0, "f": 50.0, "Bmax": 1.5, "Et": 0.5, "factor_Qc": 3.0})
    print("-" * 78)
    print("  Lo que la aritmética produce (grado 'conforme', sin validez física):")
    print(f"     área núcleo = {out['area_nucleo_cm2']} cm²   (¡para 2 kVA!)")
    print(f"     vueltas N₁  = {out['vueltas_primario']}")
    print()

    # 6. Sanity bound: the empirical rule Ac ≈ sqrt(S) as a canonical cota:*.
    #    This bound DOES have a source (winder practice / core catalog).
    cota = skill_lib.registrar_cota(
        m, "nucleo", "area", "minima",
        minimo=40.0, maximo=60.0, unidad="cm2",
        fuente="tabla", ref="regla empírica de bobinado Ac≈sqrt(S); catálogo de núcleos EI")
    print(f"[3] cota registrada  -> cota:nucleo:area:minima = [40, 60] cm² (fuente=tabla)")
    area = out["area_nucleo_cm2"]
    if area < 40.0:
        print(f"     ⚠️  El área calculada {area} cm² VIOLA la cota [40,60] cm².")
        print("        => el 'diseño' es físicamente IMPLAUSIBLE (núcleo 3x menor de lo requerido).")
    print()

    # 7. Show that the system is HONEST: the bound does not "fix" the ok, it marks it.
    print("=" * 78)
    print("VEREDICTO del sistema:")
    print(f"  - El skill '{sid}' quedó en grado → '{grado}' (NO canónico).")
    print("  - La cota de sanidad detecta que el área es implausible.")
    print("  - NO hay fuente canónica de CVT (secreto industrial) => el sistema se")
    print("    ABSTIENE de afirmar que esto es un diseño construíble.")
    print()
    print("  Para que fuera 'canonico' haría falta:")
    print("    (a) una fuente:<fid> de catálogo (núcleo real de 2 kVA, hoja de datos), o")
    print("    (b) una medición sobre un CVT real.")
    print("  Sin eso, lo honesto es 'conforme' + advertencia. Nada de auto-certificar.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(run())
