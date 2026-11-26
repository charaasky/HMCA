#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""selector_componentes.py — verifiable skill: map C_ideal -> BOM of commercial components.

It is a QUANTIZATION DERIVER with constraints (same pattern as the simulator): given the
IDEAL capacitance value and the constraints (voltage, current, technology family),
returns an array of standard COMMERCIAL parts of the correct manufacturer series.

Key correction (Fable §7duodecies): it is NOT the E24 series (resistors). The real series of
motor-run / AC film capacitors is: 5,10,15,20,25,30,35,40,45,50,55,60,70,80,100 µF
(at 370/440 VAC), and 100 µF is the standard maximum of the family.

Stopping rule: the selector MUST stop when the array error is < the set tolerance
(not add small parts to get close to 0.1 µF with ±5% parts).

This module is the skill's CODE (the impl). The spec + golden tests go in the registration.
"""

# Real motor-run AC capacitor series (µF), with their standard nominal voltage.
SERIE_MOTOR_RUN_UF = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 70, 80, 100]
TENSION_AC = 440.0          # VAC (standard maximum of the family)
TOLERANCIA_TIPICA = 0.05    # ±5% (typical in AC film)


def cuantizar(C_ideal, serie=None, tolerancia=0.05, max_piezas=8):
    """Packs C_ideal as a parallel array of parts from `serie`.

    Rule (Fable's correction): use the necessary LARGE parts and reserve the LAST
    position for the "adjustment part" (the series value CLOSEST to the remainder, which
    may slightly exceed it). That way unnecessary small parts are not accumulated.

    Example: C_ideal=328.8 -> 3x100 (=300) + adjustment part ~28.8 -> 30 => [100,100,100,30].
    """
    serie = serie or SERIE_MOTOR_RUN_UF
    serie_desc = sorted(serie, reverse=True)
    piezas = []
    restante = C_ideal
    # 1) large parts that fit WHOLE, ALWAYS leaving the last slot free
    #    for the "adjustment part". We only take LARGE parts: when the current part
    #    no longer fits (restante < p), we stop descending and leave the remainder to
    #    step 2 (the closest part), instead of continuing to insert smaller parts.
    for p in serie_desc:
        while restante >= p - 1e-9 and len(piezas) < max_piezas - 1:
            piezas.append(p)
            restante -= p
        # when the large part no longer fits, we do NOT go down to smaller parts: the remainder
        # is covered by the adjustment part (step 2).
        if restante < p - 1e-9:
            break
    # 2) last part: the series value closest to the remaining remainder (it may exceed it).
    if restante > 1e-9 and len(piezas) < max_piezas:
        ultima = min(serie, key=lambda x: abs(x - restante))
        piezas.append(ultima)
    C_total = sum(piezas)
    error = abs(C_total - C_ideal) / C_ideal
    return piezas, C_total, error, error <= tolerancia


def bom_capacitor(C_ideal, V_rms, I_rms_total, serie=None, tolerancia=0.05):
    """BOM of the resonant capacitor: array + per-part specs, with voltage guardrail.

    A CVT's resonant tank sees ~~2x the mains peak (resonance phenomenon): V_pico =
    V_rms·sqrt(2)·2. A typical 440 VAC motor-run capacitor does NOT withstand that peak in a single
    element. This function:

      1. Computes the design peak voltage.
      2. If a single capacitor of the family withstands it (V_nominal >= V_pico), returns the normal
         parallel array.
      3. If it does NOT withstand it, it REFUSES to return a capacitor that 'flies': returns `tension_ok=False`
         and proposes the correct MITIGATION (two capacitors in SERIES to double the voltage,
         adjusting the capacitance), marked as 'pending manufacturer validation'.

    It does NOT invent ratings: the manufacturer's voltage decision is contingent datum; here only the
    overvoltage rule (physical resonance datum) is APPLIED and the boundary is declared.
    """
    import math
    V_pico = V_rms * math.sqrt(2) * 2.0   # worst-case resonance (2x mains peak)

    # does a single element of the family withstand the peak?
    soporta_unico = TENSION_AC >= V_pico

    if soporta_unico:
        piezas, C_total, error, ok = cuantizar(C_ideal, serie=serie, tolerancia=tolerancia)
        n_piezas = len(piezas)
        Ic_pieza = I_rms_total / n_piezas if n_piezas else 0.0
        return {
            "valor_ideal_uF": C_ideal,
            "valor_comercial_uF": round(C_total, 2),
            "arreglo": piezas,
            "n_piezas": n_piezas,
            "error_relativo": round(error, 4),
            "dentro_de_tolerancia": ok,
            "tension_nominal_VAC": TENSION_AC,
            "tension_pico_diseno_V": round(V_pico, 0),
            "tension_ok": True,
            "corriente_total_A": round(I_rms_total, 2),
            "corriente_por_pieza_A": round(Ic_pieza, 2),
            "capacidad_corriente_pieza_A": 9.0,
            "mitigacion": None,
        }

    # ⚠️ OVERVOLTAGE: a single capacitor does NOT hold. Mitigate with 2 in SERIES.
    # In series, the voltage splits (2×440=880V > 622V) and the total capacitance is reduced to
    # C_serie/2; to recover the capacitance the array is built as parallel pairs in series.
    # C_equivalente_por_par = C_pieza / 2  => we need pairs in parallel.
    piezas_base, C_total_base, error_base, ok_base = cuantizar(C_ideal, serie=serie, tolerancia=tolerancia)
    # the series array doubles the voltage; the capacitance of each branch drops to half,
    # so to keep ~C_ideal each part's capacitance per branch must be DOUBLED.
    # We prefer to report the defect and the recipe rather than invent an exact combination:
    return {
        "valor_ideal_uF": C_ideal,
        "valor_comercial_uF": round(C_total_base, 2),
        "arreglo": piezas_base,
        "n_piezas": len(piezas_base),
        "error_relativo": round(error_base, 4),
        "dentro_de_tolerancia": ok_base,
        "tension_nominal_VAC": TENSION_AC,
        "tension_pico_diseno_V": round(V_pico, 0),
        "tension_ok": False,
        "corriente_total_A": round(I_rms_total, 2),
        "corriente_por_pieza_A": round(I_rms_total / len(piezas_base), 2) if piezas_base else 0.0,
        "capacidad_corriente_pieza_A": 9.0,
        "mitigacion": {
            "tipo": "serie_2x",
            "motivo": f"tensión de pico de resonancia {V_pico:.0f} V excede {TENSION_AC} V de un único capacitor",
            "receta": "Armar cada rama del tanque con DOS capacitores iguales EN SERIE "
                      "(2×440V AC nominal => 880V pico soportado). Cada rama serie aporta "
                      "C/2; armar tantas ramas EN PARALELO como haga falta para recuperar "
                      f"~{C_ideal:.0f} uF equivalentes. Validar corriente RMS por rama con "
                      "la hoja de datos del fabricante (dato contingente, NO inventado).",
            "estado": "pendiente de validación de fabricante",
        },
    }


# ─── DECLARATIVE SPEC of the skill (to register with skill_lib) ───────────────
SPEC_SELECTOR = {
    "nombre": "selector de capacitores resonantes",
    "inputs": [
        {"nombre": "C_ideal", "unidad": "uF"},
        {"nombre": "V_rms", "unidad": "V"},
        {"nombre": "I_rms_total", "unidad": "A"},
    ],
    "outputs": [
        {"nombre": "valor_comercial_uF", "unidad": "uF", "tolerancia": {"tipo": "abs", "valor": 0.5}},
        {"nombre": "n_piezas", "unidad": "1", "tolerancia": {"tipo": "abs", "valor": 0.5}},
    ],
    "tests_dorados": [
        # C_ideal=328.8 -> 330 uF commercial (motor-run series), within ±5% tolerance
        {"id": "cvt_328", "inputs": {"C_ideal": 328.8, "V_rms": 220.0, "I_rms_total": 22.73},
         "esperado": {"valor_comercial_uF": 330.0, "dentro_de_tolerancia": True}},
        # C_ideal=25 -> single exact 25uF part
        {"id": "exacto_25", "inputs": {"C_ideal": 25.0, "V_rms": 220.0, "I_rms_total": 5.0},
         "esperado": {"valor_comercial_uF": 25.0, "dentro_de_tolerancia": True}},
    ],
}


if __name__ == "__main__":
    b = bom_capacitor(328.8, 220.0, 22.73)
    import json
    print(json.dumps(b, ensure_ascii=False, indent=2))
    print()
    print("Arreglo correcto (serie motor-run, NO E24):")
    print("  3x100uF + 1x30uF = 330uF  (error 0.36%, ya dentro de ±5% -> PARA acá)")
