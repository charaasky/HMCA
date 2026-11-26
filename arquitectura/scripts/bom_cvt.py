#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""bom_cvt.py — Bill of materials (BOM) of the ferroresonant stabilizer with STANDARD
COMMERCIAL components (not ideal values).

Closes the gap: the sizing gives IDEAL values (C=328.8 µF); this maps them to
components that can be OBTAINED (E6/E12/E24 series, real catalog), with their parallel
arrangement when a single component does not exist.

The key finding: there is NO 328.8 µF capacitor at ≥630 V as a single standard part;
the real resonant tank is built with a parallel arrangement of metallized PP film.
"""

import math


# Standard value series (mantissas)
E6 = [1.0, 1.5, 2.2, 3.3, 4.7, 6.8]
E12 = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]
E24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
       3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]


def _mantisa_exp(v):
    exp = math.floor(math.log10(v))
    return v / (10 ** exp), exp


def valor_estandar(ideal, serie=E24):
    m, e = _mantisa_exp(ideal)
    best = min(serie, key=lambda x: abs(x - m))
    return round(best * (10 ** e), 4)


# Available metallized PP film capacitor values at 630 VDC / ~400 VAC
# (real catalog: 100uF handles ~9 A RMS; smaller parts proportionally).
CAPACITORES_630V = [100, 68, 47, 33, 22, 10, 4.7, 2.2, 1.0]


def arreglo_capacitores(C_ideal_uF, disponibles=CAPACITORES_630V):
    """Packs `C_ideal_uF` as a parallel sum of commercial parts (greedy)."""
    restante = C_ideal_uF
    sel = []
    for p in disponibles:
        while restante >= p - 0.005:
            sel.append(p)
            restante -= p
            if restante < 0.005:
                break
        if restante < 0.005:
            break
    return sel, sum(sel)


def bom(cvt=None):
    """Computes the full BOM of the CVT. `cvt` = dict from `cvt_dimensionamiento.dimensionar()`."""
    if cvt is None:
        import cvt_dimensionamiento
        cvt = cvt_dimensionamiento.dimensionar()

    S = cvt["S_VA"]
    V = cvt["Vin"]
    f = cvt["f_Hz"]

    # 1. Resonant capacitor: commercial arrangement
    C_ideal = cvt["C_uF"]
    piezas, C_total = arreglo_capacitores(C_ideal)
    # 3 pieces of 100uF -> 300uF; add to reach 328.8
    # with 100+100+100+22+4.7+1+1 = 328.7
    omega = 2 * math.pi * f
    L_real = 1 / (omega ** 2 * C_total * 1e-6)  # H

    Ic_total = cvt.get("Qc_var", 0) / V  # total reactive current ~22.7 A
    Ic_por_pieza = Ic_total / len(piezas) if piezas else 0

    return {
        "capacitor_resonante": {
            "valor_ideal_uF": C_ideal,
            "valor_comercial_uF": round(C_total, 1),
            "arreglo": piezas,
            "n_piezas": len(piezas),
            "tension_nominal": "630 VDC / 400 VAC",
            "dielectrico": "polipropileno metalizado (PP)",
            "corriente_total_A": round(Ic_total, 2),
            "corriente_por_pieza_A": round(Ic_por_pieza, 2),
            "capacidad_corriente_pieza_A": 9.0,  # 100uF handles ~9A RMS
        },
        "inductancia_resonante": {
            "valor_ideal_mH": cvt["L_mH"],
            "valor_real_mH": round(L_real * 1e3, 2),
            "nucleo": "con shunt magnético (secreto industrial: no dimensionable aquí)",
        },
        "nucleo_principal": {
            "area_cm2": cvt["area_nucleo_cm2"],
            "seccion_cm": cvt["seccion_nucleo_cm"],
            "material": "FeSi laminado (grano orientado)",
            "Bmax_T": cvt["Bmax_T"],
        },
        "bobinados": {
            "primario_vueltas": cvt["vueltas_primario"],
            "secundario_resonante_vueltas": cvt["vueltas_secundario_resonante"],
            "alambre_mm2": cvt["seccion_alambre_mm2"],
        },
        "protecciones": [
            {"item": "MOV 275 VAC / 14 mm", "rol": "clamp transitorios (1.5kV -> ~710V)"},
            {"item": "GDT 350-470 V", "rol": "deriva energía de rayo indirecto"},
            {"item": "Fusible 1.25 x I_nom (11.4 A)", "rol": "sobrecorriente sostenida"},
        ],
    }


if __name__ == "__main__":
    b = bom()
    import json
    print(json.dumps(b, ensure_ascii=False, indent=2))
