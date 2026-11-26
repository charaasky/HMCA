#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""chequeo_cruzado.py — consistency check topology↔magnitudes (typed, with mutation).

Step 3 of the Fable plan (debilidades_camino4). It is NOT a 'completeness checker'; it is the
CROSS-CHECK: does the DECLARED topology match the topology IMPLICIT in the formulas?

The 4 Fable conditions:
  1. TYPING: model class + operating point. Outside the class => 'not applicable'.
  2. DERIVED OBSERVABLE: compare simulated vs analytic resonance frequency (not inputs).
  3. SINGLE SOURCE: values come from a magnitudes dict (HCMA), not copied.
  4. MUTATION CONTROL: re-wiring the topology must change the observable; if it does not change,
     the check is insensitive (tautological).

Physics note (finding): the natural resonance frequency 1/(2π√(LC)) is the SAME for
parallel tank and series divider, so the frequency does NOT discriminate. What discriminates is the
SHAPE of the response: parallel => PEAK of |V| at the tank node (maximum impedance);
series => VALLEY (minimum impedance). The mutation control uses THAT difference.
"""
import math

from simulador import resonancia_lc  # noqa: E402
import topologia as T  # noqa: E402


def chequeo_cruzado_resonancia(magnitudes, clase_modelo=T.MODELO_LINEAL, tolerancia=0.02):
    """Cross-check of the LC tank (LINEAR domain). `magnitudes` = {'L_mH','C_uF'}."""
    L = magnitudes["L_mH"]
    C = magnitudes["C_uF"]
    res = resonancia_lc(L, C)

    aplicable = clase_modelo in (T.MODELO_LINEAL, T.MODELO_PARAMETRICO, T.MODELO_COMPLETO)
    if not aplicable:
        return {"aplicable": False, "consistente": None,
                "motivo": f"modelo {clase_modelo} no cubre resonancia lineal",
                "f_simulada_Hz": res["f_simulada_Hz"],
                "f_analitica_Hz": res["f_analitica_Hz"],
                "error_relativo": res["error_relativo"]}

    error = res["error_relativo"]
    return {
        "aplicable": True,
        "consistente": error <= tolerancia,
        "clase_modelo": clase_modelo,
        "tolerancia": tolerancia,
        "f_simulada_Hz": res["f_simulada_Hz"],
        "f_analitica_Hz": res["f_analitica_Hz"],
        "error_relativo": error,
    }


def mutacion_serie_paralelo(L_mH, C_uF):
    """MUTATION CONTROL: measures the SHAPE of the response in parallel vs series.

    Parallel (correct): source -> L -> node n2, C from n2 to ground. At resonance, |V(n2)| is a
      PEAK (maximum impedance).
    Series (mutation): source -> L -> C -> R -> ground, all in a chain. There is no parallel branch; at
      the node between L and C the voltage at series resonance has a VALLEY (minimum impedance).

    Returns (sensible: bool, detalle). 'sensible' is True if the two topologies produce
    markedly different |V| responses (a check that does not distinguish them is tautological).
    """
    import numpy as np
    from simulador import _correr_raw, _buscar_raw, _parsear_raw, _magnitudes  # noqa: E402

    L = L_mH / 1000.0
    C = C_uF / 1e6

    # ── PARALLEL (correct) ──
    net_par = (
        "Tanque paralelo (correcto)\n"
        "V1 n1 0 AC 1\n"
        f"L1 n1 n2 {L}\n"
        f"C1 n2 0 {C}\n"
        f"R1 n2 0 100\n"
        ".ac dec 20000 10 100000\n"
        ".control\nset filetype=ascii\nrun\nwrite out.raw\nquit\n.endc\n.end\n"
    )
    tmp = _correr_raw(net_par)
    freq, series, names = _parsear_raw(_buscar_raw(tmp))
    vmag_par = _magnitudes(series[names.index("v(n2)")])

    # ── SERIES (mutation) ──
    net_ser = (
        "Divisor serie (mutacion, NO tanque paralelo)\n"
        "V1 n1 0 AC 1\n"
        f"L1 n1 n2 {L}\n"
        f"C1 n2 n3 {C}\n"
        f"R1 n3 0 100\n"
        ".ac dec 20000 10 100000\n"
        ".control\nset filetype=ascii\nrun\nwrite out.raw\nquit\n.endc\n.end\n"
    )
    tmp2 = _correr_raw(net_ser)
    freq2, series2, names2 = _parsear_raw(_buscar_raw(tmp2))
    vmag_ser = _magnitudes(series2[names2.index("v(n2)")])

    # The SHAPE distinguishes: in parallel there is a peak of |V| at resonance; in series, at the node
    # between L and C the voltage at resonance is a valley (the series divider lets it through). We measure the
    # minimum/maximum ratio of each; different topologies => different shapes.
    par_pico = float(np.max(vmag_par))
    ser_pico = float(np.max(vmag_ser))
    ser_min = float(np.min(vmag_ser))

    # sensitive if the tank node behavior is materially different:
    # in parallel the voltage rises to a large peak; in series the intermediate node does not have that
    # peak (attenuated by the chain). We use the normalized peak difference.
    pico_diff = abs(par_pico - ser_pico) / max(par_pico, 1e-9)
    sensible = pico_diff > 0.05  # >5% peak difference => we distinguish them

    return sensible, {
        "pico_paralelo_V": round(par_pico, 3),
        "pico_serie_V": round(ser_pico, 3),
        "valle_serie_V": round(ser_min, 3),
        "diferencia_pico": round(pico_diff, 4),
    }
