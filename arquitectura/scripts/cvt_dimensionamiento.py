#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""cvt_dimensionamiento.py — complete DETERMINISTIC calculation of the ferroresonant stabilizer.

Explicitly separates what is CANONICAL (physical/mathematical law, exactly computable) from what
is DESIGN SELECTION [UNVERIFIED] (no source) and what is NOT COMPUTABLE (industrial secret
= B-H curve of the magnetic shunt).

Values verified against a real product (SolaHD CVS 2000 VA, 220 V, 60 Hz, web search
OpenRouter):
  - Box 29 x 42.5 x 27.3 cm, weight 50.9 kg, regulation ±1%, THD 3%, efficiency up to 92%.
  (We size at 50 Hz — Argentina mains; the reference SolaHD is 60 Hz.)

Usage: python3 arquitectura/scripts/cvt_dimensionamiento.py
"""

import math


def dimensionar(Vin=220.0, Vout=220.0, S=2000.0, f=50.0, Bmax=1.0, k_nucleo=1.0, J=3.5,
                factor_Qc=2.5):
    """Returns a dict with all computed values + their confidence class.

    Classes:
      'canonico'        -> physical law/definition (Faraday, resonance, Ohm, S=VI definition)
      'seleccion'       -> reasoned design selection WITHOUT a canonical source [UNVERIFIED]
      'no_calculable'   -> depends on proprietary data (B-H curve of the magnetic shunt)
    """
    out = {}
    out["S_VA"] = S
    out["Vin"] = Vin
    out["Vout"] = Vout
    out["f_Hz"] = f

    # ── CANONICAL: definitions and physical laws ──────────────────────────────
    out["corriente_A"] = round(S / Vin, 2)                       # I = S/V (definition)
    out["corriente_A_clase"] = "canonico"
    out["omega_rad_s"] = round(2 * math.pi * f, 2)               # ω = 2πf
    out["omega_rad_s_clase"] = "canonico"
    # Core area by empirical rule Ac = k·√S (NOT a physical law: winder's rule)
    Ac_cm2 = k_nucleo * math.sqrt(S)
    out["area_nucleo_cm2"] = round(Ac_cm2, 2)
    out["area_nucleo_cm2_clase"] = "seleccion"
    out["seccion_nucleo_cm"] = round(math.sqrt(Ac_cm2), 2)       # side if square
    out["seccion_nucleo_cm_clase"] = "seleccion"

    # Volts per turn: Faraday (the formula is CANONICAL; Bmax is a selection)
    Et = 4.44 * f * Bmax * (Ac_cm2 / 10000.0)
    out["Et_v_vuelta"] = round(Et, 3)
    out["Et_v_vuelta_clase"] = "canonico"
    out["Bmax_T"] = Bmax
    out["Bmax_T_clase"] = "seleccion"

    # Primary turns: N = V/Et (Faraday rearrangement, canonical)
    out["vueltas_primario"] = round(Vin / Et, 0)
    out["vueltas_primario_clase"] = "canonico"

    # ── Copper and losses (canonical: Ohm / Joule) ─────────────────────────────
    seccion_alambre = (S / Vin) / J                              # mm2
    out["seccion_alambre_mm2"] = round(seccion_alambre, 2)
    out["seccion_alambre_mm2_clase"] = "canonico"
    long_prim = out["vueltas_primario"] * 0.5                     # ~0.5 m/turn (mean geometry)
    out["longitud_alambre_primario_m"] = round(long_prim, 1)
    out["longitud_alambre_primario_m_clase"] = "seleccion"
    rho_cu = 0.0171  # ohm·mm²/m @ ~20°C
    R_prim = rho_cu * long_prim / seccion_alambre
    out["resistencia_primario_ohm"] = round(R_prim, 3)
    out["resistencia_primario_ohm_clase"] = "canonico"
    P_cu = (S / Vin) ** 2 * R_prim
    out["perdida_cobre_W"] = round(P_cu, 1)
    out["perdida_cobre_W_clase"] = "canonico"

    # Iron losses: ~1.5 W/kg at 1.0 T / 50 Hz (M6 lamination) — SELECTION (depends on the material)
    masa_fe = Ac_cm2 / 10000.0 * 0.4 * 1.5 * 7850.0             # volume × density × factor
    out["masa_hierro_kg"] = round(masa_fe, 1)
    out["masa_hierro_kg_clase"] = "seleccion"
    P_fe = 1.5 * masa_fe
    out["perdida_hierro_W"] = round(P_fe, 0)
    out["perdida_hierro_W_clase"] = "seleccion"

    # Efficiency (definition, canonical, but uses losses that are a selection)
    P_total = P_cu * 2 + P_fe
    out["eficiencia_pct"] = round((S - P_total) / S * 100, 1)
    out["eficiencia_pct_clase"] = "canonico"

    # ── LC resonant tank (canonical: resonance ω²LC=1, reactive power Qc=V²ωC) ──
    Qc = factor_Qc * S                                            # selection
    out["Qc_var"] = round(Qc, 0)
    out["Qc_var_clase"] = "seleccion"
    C = Qc / (Vin ** 2 * out["omega_rad_s"])
    out["C_uF"] = round(C * 1e6, 1)
    out["C_uF_clase"] = "canonico"
    L = 1.0 / (out["omega_rad_s"] ** 2 * C)
    out["L_mH"] = round(L * 1e3, 2)
    out["L_mH_clase"] = "canonico"
    # resonance verification (identity: must be 1.0)
    out["resonancia_verificada"] = round(out["omega_rad_s"] ** 2 * L * C, 6)
    out["resonancia_verificada_clase"] = "canonico"

    # ── REGULATION (the principle IS computable; the ±1% exactness is NOT) ──────────
    # Saturation-clipping principle: Vout = 4.44 f N2 B_sat A. If B clamps at B_sat,
    # Vout clamps. N2 is chosen so that B_sat gives the nominal Vout. This is CANONICAL.
    B_sat = 1.8  # T (grain-oriented FeSi, public IEC 60404 value ~1.7–2.0 T)
    N2 = Vout / (4.44 * f * B_sat * (Ac_cm2 / 10000.0))
    out["vueltas_secundario_resonante"] = round(N2, 0)
    out["vueltas_secundario_resonante_clase"] = "canonico"
    Vout_sat = 4.44 * f * N2 * B_sat * (Ac_cm2 / 10000.0)
    out["vout_saturacion_V"] = round(Vout_sat, 1)
    out["vout_saturacion_V_clase"] = "canonico"
    # The EXACTNESS of clipping at ±1% (not ±0.5% or ±1.8%) depends on the STIFFNESS of the
    # B-H loop at the saturation knee and the magnetic shunt geometry → secret.
    out["regulacion_exactitud_pct"] = None
    out["regulacion_exactitud_pct_clase"] = "no_calculable"

    # ── THD (waveform quality) ──────────────────────────────────────────────
    # THD measures how much the output deviates from a pure sinusoid. The saturated core
    # generates harmonics; the LC tank filters them. The fine value depends on B-H and the filter:
    # it can be BOUNDED by simulation (I-SIM), not given an exact value without the material curve.
    # ── THD (total harmonic distortion) ─────────────────────────────────
    # THD IS measurable by SIMULATION (I-SIM): an input with harmonics is injected and
    # it is measured how much the LC tank attenuates them. It does NOT depend on the secret (it is passive filtering).
    # The fine value of a real CVT (SolaHD=3%) depends on the exact filter geometry,
    # but the PRINCIPLE (the LC attenuates harmonics) is deterministically verifiable.
    out["THD_pct"] = None
    out["THD_pct_clase"] = "verificable_por_simulacion"
    return out


if __name__ == "__main__":
    d = dimensionar()
    print("=" * 68)
    print("ESTABILIZADOR FERRORESONANTE — dimensionamiento 50 Hz (red Argentina)")
    print("=" * 68)
    for k, v in d.items():
        if k.endswith("_clase"):
            continue
        clase = d.get(k + "_clase", "?")
        print(f"  {k:<28} {v!s:>12}   [{clase}]")

    print()
    print("Clases de confianza:")
    print("  - CANONICO      : ley física / definición (Faraday/Ohm/resonancia/saturación)")
    print("  - SELECCIÓN     : regla de diseño razonada SIN fuente canónica [UNVERIFIED]")
    print("  - NO_CALCULABLE : depende de la dureza del lazo B-H / geometría del shunt (secreto)")
    print()
    print("Referencia producto real (SolaHD CVS 2000 VA, 220 V, 60 Hz, búsqueda web):")
    print("  caja 29 x 42.5 x 27.3 cm, peso 50.9 kg, regulación ±1%, THD 3%, eficiencia ≤92%")
