#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""dominio_cvt.py — DECLARATIVE domain 'estabilizador_ferroresonante' (deliverable contract).

Fixes the bug: the ferroresonant stabilizer was a loose script without `entregable`, so
"request design → generate PDF" was NOT fulfilled. Here it is wrapped as a declarative domain of
the deliverable CONTRACT (INVARIANTES_DOMINIO.md), like room/mains/motor, so that
the generic generator (`generar_pdf.py`) produces the PDF by construction.

The deterministic calculation is the existing one (cvt_dimensionamiento.dimensionar + selector).
The design power S is an INPUT that is asked for (not presumed): 1500 VA for the refrigerator.
"""
import math
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from cvt_dimensionamiento import dimensionar  # noqa: E402
from selector_componentes import bom_capacitor, SERIE_MOTOR_RUN_UF  # noqa: E402
from circuito_cvt import netlist_spice, diagrama_conexiones  # noqa: E402
import topologia as T  # noqa: E402


# ── specification ────────────────────────────────────────────────────────────
# inputs: every CASE parameter is asked for, nothing is presumed.
DOMINIO_CVT = {
    "nombre": "estabilizador de tensión ferroresonante (CVT)",
    "inputs": [
        {"name": "tension_V", "question": "tensión nominal de entrada/salida (V)", "unit": "V"},
        {"name": "potencia_VA", "question": "potencia de diseño (VA); cubrir pico de arranque", "unit": "VA"},
        {"name": "frecuencia_Hz", "question": "frecuencia de red (50 ó 60 Hz)", "unit": "Hz"},
        {"name": "carga_desc", "question": "descripción de la carga (para el título)", "unit": "1", "tipo": "str"},
    ],
    "demo_inputs": {
        "tension_V": 220.0, "potencia_VA": 1500.0, "frecuencia_Hz": 50.0,
        "carga_desc": "Heladera Gafa HGW311P 319L",
    },
    "demo_lookup": {"proveedor_caps": "serie motor-run AC 440VAC (catálogo)"},

    "calcular": lambda inputs: _calcular(inputs),
    "calc_inputs": ["tension_V", "potencia_VA", "frecuencia_Hz"],

    # physical dimensions + ranges (outputs with their unit and legal range)
    "dimensiones": {
        "corriente_A": "A",
        "area_nucleo_cm2": "cm**2",
        "vueltas_primario": "1",
        "vueltas_secundario": "1",
        "C_uF": "uF",
        "L_mH": "mH",
        "eficiencia_pct": "%",
    },
    "ranges": {
        "corriente_A": (0.1, 50.0),
        "area_nucleo_cm2": (1.0, 500.0),
        "vueltas_primario": (10, 5000),
        "vueltas_secundario": (10, 5000),
        "C_uF": (1.0, 1000.0),
        "L_mH": (0.1, 1000.0),
        "eficiencia_pct": (50.0, 99.0),
    },
    "allowlist": {
        "area_nucleo_cm2": {"cm**2", "m**2"},
        "C_uF": {"uF", "F"},
        "L_mH": {"mH", "H"},
    },
    "clave": "C_uF",
    "procurement": True,
}


def _calcular(inputs):
    V = float(inputs["tension_V"])
    S = float(inputs["potencia_VA"])
    f = float(inputs["frecuencia_Hz"])
    d = dimensionar(Vin=V, Vout=V, S=S, f=f)
    I_rms = d["corriente_A"]
    bom = bom_capacitor(d["C_uF"], V, I_rms)
    # THD via simulator if available (optional, does not block)
    thd = None
    try:
        from simulador import thd_tanque_resonante
        thd = thd_tanque_resonante(d["L_mH"], d["C_uF"])
    except Exception:
        pass
    out = {
        "corriente_A": d["corriente_A"],
        "area_nucleo_cm2": d["area_nucleo_cm2"],
        "vueltas_primario": d["vueltas_primario"],
        "vueltas_secundario": d["vueltas_secundario_resonante"],
        "C_uF": d["C_uF"],
        "C_comercial_uF": bom["valor_comercial_uF"],
        "arreglo_caps": bom["arreglo"],
        "L_mH": d["L_mH"],
        "eficiencia_pct": d["eficiencia_pct"],
    }
    if thd is not None:
        out["thd_entrada_pct"] = thd[0]
        out["thd_salida_pct"] = thd[1]
    return out


# ── deliverable (Invariant 2 contract) ────────────────────────────────────────
def _esquema(inputs, Q):
    # reconstruct the sizing dict for the circuit (from Q + inputs)
    V = float(inputs["tension_V"])
    S = float(inputs["potencia_VA"])
    f = float(inputs["frecuencia_Hz"])
    d = dimensionar(Vin=V, Vout=V, S=S, f=f)
    bom = bom_capacitor(d["C_uF"], V, d["corriente_A"])
    return diagrama_conexiones(d, bom, carga_VA=S)


def _materiales(inputs, Q):
    caps = Q["arreglo_caps"]
    # core: E-I laminations, quantity proportional to area (units of 100 cm2 laminated)
    import collections
    piezas = collections.Counter(caps)
    mat = [
        ("chapa_FeSi_nucleo", round(Q["area_nucleo_cm2"] / 10.0, 1), "unidad (paquete 10cm2)"),
        ("alambre_cobre_primario", round(Q["vueltas_primario"] * 0.5, 1), "m"),
        ("alambre_cobre_secundario", round(Q["vueltas_secundario"] * 0.5, 1), "m"),
    ]
    for valor, n in sorted(piezas.items()):
        mat.append((f"capacitor_motor_run_{valor}uF_440VAC", n, "unidad"))
    return mat


# reference prices (drift_temporal, estimated ARS 2026)
PRECIOS_CVT = [
    ("chapa_FeSi_nucleo", "unidad (paquete 10cm2)", 4500.0, "ferretería industrial (2026, ARS, estimado)"),
    ("alambre_cobre_primario", "m", 3500.0, "casa eléctrica (2026, ARS, estimado)"),
    ("alambre_cobre_secundario", "m", 3500.0, "casa eléctrica (2026, ARS, estimado)"),
    ("capacitor_motor_run_100uF_440VAC", "unidad", 9000.0, "catálogo motor-run (2026, ARS, estimado)"),
    ("capacitor_motor_run_45uF_440VAC", "unidad", 7500.0, "catálogo motor-run (2026, ARS, estimado)"),
    ("capacitor_motor_run_30uF_440VAC", "unidad", 6500.0, "catálogo motor-run (2026, ARS, estimado)"),
    ("capacitor_motor_run_25uF_440VAC", "unidad", 6000.0, "catálogo motor-run (2026, ARS, estimado)"),
    ("capacitor_motor_run_5uF_440VAC", "unidad", 4000.0, "catálogo motor-run (2026, ARS, estimado)"),
]


def _notas(inputs, Q):
    notas = [
        f"Regulación por saturación: Vout se clava en {inputs['tension_V']} V; la exactitud ±1% depende de la curva B-H del shunt (secreto industrial, NO calculable).",
        "El capacitor resonante puede ver sobretensión (~2x de pico); elegir tensión nominal de capacitor acorde o bajar Qc.",
    ]
    if "thd_salida_pct" in Q:
        notas.append(f"THD simulado (ngspice): {Q['thd_entrada_pct']}% -> {Q['thd_salida_pct']}% (el tanque LC atenúa armónicos).")
    # EPISTEMIC STATE marked per magnitude (P0: do not present a selection as canonical)
    V = float(inputs["tension_V"]); S = float(inputs["potencia_VA"]); f = float(inputs["frecuencia_Hz"])
    d = dimensionar(Vin=V, Vout=V, S=S, f=f)
    clases = {}
    for k in ("corriente_A", "area_nucleo_cm2", "vueltas_primario",
              "vueltas_secundario_resonante", "C_uF", "L_mH", "eficiencia_pct",
              "regulacion_exactitud_pct", "THD_pct"):
        clases[k] = d.get(k + "_clase", "?")
    notas.append(
        "ESTADO EPISTÉMICO (marcado): " +
        "; ".join(f"{k}={clases[k]}" for k in sorted(clases))
    )
    # HONESTY GATE (topology): the deliverable is NOT 'ready to manufacture'.
    # The topology (netlist) is ASSUMED (generated by code, not ratified by a human
    # nor verified by non-linear connectivity). The real degree degrades the assertion.
    grado, faltantes = T.grado_entregable([T.ASUMIDA])
    degradado = T.degradar_afirmacion(grado, "fabricable", faltantes)
    notas.append(
        "GATE DE TOPOLOGÍA: este entregable NO es 'listo para fabricar'. "
        f"Grado real = '{degradado['afirmacion']}'. Falta: " + "; ".join(faltantes) + "."
    )
    # STEP 2: mechanism coverage. The CVT model here is LINEAR (coupling K=0.98),
    # which does NOT cover the regulation by core SATURATION. The system REFUSES to assert
    # the function 'regulates ±1%' with that model.
    neg = T.negarse_a_promover("el CVT regula la tensión", "regulacion", T.MODELO_LINEAL)
    notas.append(
        f"GATE DE MECANISMO: {neg['motivo']}. "
        f"({ 'promueve' if neg['promueve'] else 'NO promueve' }, grado={neg['grado']})."
    )
    return notas


DOMINIO_CVT["entregable"] = {
    "titulo": lambda inputs: (
        f"Estabilizador ferroresonante — {inputs.get('carga_desc', 'CVT')} "
        f"({inputs.get('potencia_VA', 0):.0f} VA, {inputs.get('tension_V', 220):.0f} V, "
        f"{inputs.get('frecuencia_Hz', 50):.0f} Hz)"
    ),
    "moneda": "ARS",
    "etiquetas": {
        "corriente_A": "corriente nominal",
        "area_nucleo_cm2": "área de núcleo",
        "vueltas_primario": "vueltas primario",
        "vueltas_secundario": "vueltas secundario resonante",
        "C_uF": "C resonante (ideal)",
        "C_comercial_uF": "C resonante (comercial)",
        "L_mH": "L resonante",
        "eficiencia_pct": "eficiencia",
        "thd_salida_pct": "THD salida",
    },
    "esquema": _esquema,
    "materiales": _materiales,
    "precios": PRECIOS_CVT,
    "exclusiones": [
        "La regulación ±1% exacta NO es calculable sin la curva B-H del shunt magnético (secreto industrial). Se dimensiona el MECANISMO de corte por saturación, no la exactitud.",
        "THD verificado por simulación de principio (tanque LC); el valor exacto de un CVT comercial depende de la geometría del filtro.",
        "Bmax=1.0 T (FeSi) es SELECCIÓN de diseño, no dato canónico.",
        "Precios de referencia (drift_temporal): validar contra proveedor.",
    ],
    "notas": _notas,
}
