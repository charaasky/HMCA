#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""dimensionamiento_parlante.py — deterministic acoustic algebra (1-way SEALED speaker).

IMMUTABLE physics (laws, not catalog). No contingent datum is invented; those enter
as PARAMETERS (Fs, Qts, VAS, Re, Le, Sens) that require provenance (driver catalog).

Laws used (all deducible / dimensional):
  P = V · I                 (electrical power)
  P = I² · Re               (for the driver, with Re = DC resistance) -> I = sqrt(P/Re)
  SPL(dB) @ 1m = Sens + 10·log10(P)                    (Sens in dB/W/m)
  Qtc = Qts · sqrt(VAS/Vb + 1)                          (closed box)
  Fcb = Fs · sqrt(VAS/Vb + 1)                           (in-box resonance)
  F3  = Fcb · sqrt( (1/Qtc² - 2) + sqrt((1/Qtc² - 2)² + 4) / sqrt(2) )   (Qtc > 1/√2)

BOUNDARY (Fable): these formulas are DEFINITIONAL (self-ratifiable with dimensional
checking). The SENSITIVITY (Sens), Fs/Qts/VAS/Re/Le are CONTINGENT DATA (real driver):
without them the enclosure is NOT verifiable => it stays PROVISIONAL, not 'ready to manufacture'.
"""
import math


def dimensionar(driver, potencia_W, amplificador_V):
    """Sizes a 1-way SEALED speaker from the DRIVER parameters.

    Params:
      driver: dict with {Fs(Hz), Qts, VAS(L), Re(ohm), Le(mH), Sd(cm2), Xmax(mm), Sens(dB/W/m)}
      potencia_W: nominal RMS power (W)
      amplificador_V: RMS supply voltage (V)

    Returns a dict of derived magnitudes + their epistemic class per magnitude.
    """
    Fs = float(driver["Fs"])
    Qts = float(driver["Qts"])
    VAS = float(driver["VAS"])
    Re = float(driver["Re"])
    Le = float(driver["Le"])
    Sd = float(driver["Sd"])
    Sens = float(driver["Sens"])

    # ── electrical (definitional) ──
    # amplifier Vpeak; RMS driver current into its load
    V_rms = float(amplificador_V)
    # approximate RMS current (load ~ Re at mid frequencies, Le negligible in mid audio)
    Z = math.sqrt(Re ** 2 + (2 * math.pi * 1000 * Le / 1000.0) ** 2)
    I_driver = V_rms / Z
    P_el = V_rms * I_driver

    # ── acoustic: SPL at 1 m ──
    spl_1m = Sens + 10.0 * math.log10(potencia_W) if potencia_W > 0 else Sens

    # ── closed box ──
    # choose a target Qtc (Butterworth alignment = 1/√2) for sealing
    Qtc_target = 1.0 / math.sqrt(2.0)
    # Vb that produces Qtc_target given Qts and VAS:
    #   Qtc = Qts · sqrt(VAS/Vb + 1)  =>  Vb = VAS / ((Qtc/Qts)² - 1)
    ratio = (Qtc_target / Qts) ** 2
    if ratio <= 1.0:
        # Qts >= Qtc_target: the driver already has high Qts; "infinite" box (large Vb)
        Vb = VAS * 1000.0  # approximate infinite baffle
        Qtc = Qts
        nota_alineamiento = "Qts alto: el driver no necesita caja pequeña (baffle infinito); Qtc=Qts"
    else:
        Vb = VAS / (ratio - 1.0)
        Qtc = Qtc_target
        nota_alineamiento = f"alineamiento Butterworth (Qtc={Qtc_target:.3f})"

    Fcb = Fs * math.sqrt(VAS / Vb + 1.0) if Vb > 0 else Fs
    # F3 (-3dB frequency). In Butterworth (Qtc = 1/√2), F3 = Fcb exactly.
    # For strictly smaller Qtc (overdamped) the response has no peak and the
    # -3dB is defined by the full curve (not by this closed formula).
    bound = 1.0 / math.sqrt(2.0)
    if Qtc >= bound:
        a = (1.0 / Qtc ** 2 - 2.0)
        F3 = Fcb * math.sqrt((a + math.sqrt(a ** 2 + 4.0)) / 2.0)
    else:
        F3 = None  # overdamped: -3dB undefined by this formula

    # ── physical dimensions of the box (simple closed box) ──
    # rectangular box proportion (1 : 1 : 1.3) to minimize standing waves
    vol_m3 = Vb / 1000.0  # L to m³
    if vol_m3 > 0:
        # internal dimensions: W x D x H ~ Vb, ratio 1 : 1 : 1.3
        x = (vol_m3 / 1.3) ** (1.0 / 3.0)
        W_int = x
        D_int = x
        H_int = 1.3 * x
        # panel thickness (18 mm MDF), external dimensions
        t = 0.018
        W_ext = W_int + 2 * t
        D_ext = D_int + 2 * t
        H_ext = H_int + 2 * t
    else:
        W_int = D_int = H_int = W_ext = D_ext = H_ext = 0.0

    return {
        # electrical
        "impedancia_ohm": round(Z, 2),
        "corriente_driver_A": round(I_driver, 4),
        "potencia_entregada_W": round(P_el, 3),
        # acoustic
        "spl_1m_dB": round(spl_1m, 2),
        "sensibilidad_dB": round(Sens, 2),
        # closed box
        "qtc": round(Qtc, 3),
        "fcb_Hz": round(Fcb, 2),
        "f3_Hz": round(F3, 2) if F3 is not None else None,
        "vb_L": round(Vb, 2),
        "vas_L": round(VAS, 2),
        "nota_alineamiento": nota_alineamiento,
        # physical dimensions
        "caja_W_int_m": round(W_int, 3),
        "caja_D_int_m": round(D_int, 3),
        "caja_H_int_m": round(H_int, 3),
        "caja_W_ext_m": round(W_ext, 3),
        "caja_D_ext_m": round(D_ext, 3),
        "caja_H_ext_m": round(H_ext, 3),
        # driver (contingent data replicated for traceability)
        "re_ohm": Re,
        "le_mH": Le,
        "sd_cm2": Sd,
        "xmax_mm": driver.get("Xmax", None),
    }
