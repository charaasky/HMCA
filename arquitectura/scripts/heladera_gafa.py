#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""heladera_gafa.py — real case: ferroresonant stabilizer for the Gafa HGW311P refrigerator.

Checks the complete HCMA flow end-to-end on a concrete case:
  1. Input of real specs (annual consumption 370 kWh @ 220V; compressor 150-250W nominal,
     start peak 800-1200W — OpenRouter web search).
  2. Deterministic sizing (cvt_dimensionamiento.dimensionar) with S chosen by margin.
  3. Component selector (selector_componentes.bom_capacitor) -> commercial BOM.
  4. Resonance and THD verification (simulator, if present) + confidence summary by class.

The power CHOICE is a DESIGN SELECTION (not canonical): from the 42W average one moves to
a design power that covers the compressor START PEAK (800-1200W). We choose
1500 VA to cover the peak with headroom and absorb regulation. It is documented as a SELECTION.

Usage:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/heladera_gafa.py
"""
import os
import sys
import json
import math

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from cvt_dimensionamiento import dimensionar  # noqa: E402
from selector_componentes import bom_capacitor  # noqa: E402


# ── REAL SPECS (OpenRouter web search, 2026-11-10) ────────────────────────
SPECS_HELADERA = {
    "modelo": "Gafa HGW311P Top Freezer 319L Plata (WD)",
    "voltaje_V": 220.0,
    "consumo_anual_kWh": 370.0,
    "potencia_nominal_W": "150-250",     # compressor running (source: wattbunker et al.)
    "pico_arranque_W": "800-1200",        # compressor LRA/start
}

# Design power choice (SELECTION, not canonical):
#   - average = 370 kWh / 8760 h ≈ 42 W (almost negligible).
#   - what matters is the START PEAK (~1000 W) + margin.
#   We choose 1500 VA to cover the peak with headroom and tolerate regulation/transients.
S_DISENO_VA = 1500.0


def correr():
    print("=" * 74)
    print("ESTABILIZADOR FERRORESONANTE — Heladera Gafa HGW311P (319L)")
    print("=" * 74)

    # 0. input specs
    print("\n[0] Especificaciones de la carga (búsqueda web):")
    for k, v in SPECS_HELADERA.items():
        print(f"    {k}: {v}")

    # average power (canonical: definition)
    prom_W = SPECS_HELADERA["consumo_anual_kWh"] * 1000 / 8760
    print(f"    potencia promedio = 370 kWh/año / 8760 h = {prom_W:.1f} W  [canónico]")
    print(f"    potencia de diseño elegida S = {S_DISENO_VA:.0f} VA  [SELECCIÓN: cubre pico "
          f"de arranque {SPECS_HELADERA['pico_arranque_W']} W con margen]")

    # 1. sizing
    d = dimensionar(Vin=220.0, Vout=220.0, S=S_DISENO_VA, f=50.0)
    print("\n[1] Dimensionamiento (50 Hz, red Argentina):")
    claves_mostrar = [
        "corriente_A", "area_nucleo_cm2", "seccion_nucleo_cm", "Et_v_vuelta",
        "vueltas_primario", "seccion_alambre_mm2", "resistencia_primario_ohm",
        "perdida_cobre_W", "perdida_hierro_W", "masa_hierro_kg", "eficiencia_pct",
        "Qc_var", "C_uF", "L_mH", "resonancia_verificada",
        "vueltas_secundario_resonante", "vout_saturacion_V", "regulacion_exactitud_pct",
        "THD_pct",
    ]
    for k in claves_mostrar:
        v = d.get(k)
        clase = d.get(k + "_clase", "?")
        if v is None:
            print(f"    {k:<30} {'(depende de simulación/secreto)':>28}  [{clase}]")
        else:
            print(f"    {k:<30} {v!s:>18}  [{clase}]")

    # 2. component selector (BOM of the resonant capacitor)
    print("\n[2] Selector de componentes (capacitor resonante):")
    I_rms = d["corriente_A"]
    bom = bom_capacitor(d["C_uF"], 220.0, I_rms)
    print(f"    C ideal = {bom['valor_ideal_uF']} µF")
    print(f"    C comercial = {bom['valor_comercial_uF']} µF  (arreglo {bom['arreglo']})")
    print(f"    n piezas = {bom['n_piezas']}, error relativo = {bom['error_relativo']}")
    print(f"    dentro de tolerancia = {bom['dentro_de_tolerancia']}")
    print(f"    tensión nominal capacitor = {bom['tension_nominal_VAC']} VAC, "
          f"pico diseño = {bom['tension_pico_diseno_V']} V, ok = {bom['tension_ok']}")

    # 3. THD by simulation (if available)
    print("\n[3] THD (verificable por simulación ngspice):")
    try:
        from simulador import thd_tanque_resonante
        # the simulator receives L in mH and C in uF directly (normalizes internally)
        thd_in, thd_out = thd_tanque_resonante(d["L_mH"], d["C_uF"])
        print(f"    THD entrada (con 3er+5to armónico)  : {thd_in}%")
        print(f"    THD salida filtrada por el tanque LC: {thd_out}%")
        print(f"    reducción de armónicos: {thd_in/thd_out:.1f}x")
    except Exception as e:
        print(f"    (simulador no disponible o falló: {e}) — THD queda [verificable_por_simulacion]")

    # 4. confidence summary
    print("\n[4] Resumen de confianza por clase:")
    clases = {}
    for k in claves_mostrar:
        c = d.get(k + "_clase", "?")
        clases.setdefault(c, []).append(k)
    for c, ks in sorted(clases.items()):
        print(f"    [{c}] -> {', '.join(ks[:6])}{'...' if len(ks)>6 else ''}")

    print("\n" + "=" * 74)
    print("CONCLUSIÓN: dimensionamiento + BOM + verificación corren el flujo HCMA completo.")
    print("Sigue la regla de oro: lo canónico se calculó; lo SELECCIÓN (Bmax, Qc, S de diseño)")
    print("queda marcado UNVERIFIED; lo no-calculable (regulación ±1% exacta) depende del shunt.")
    print("=" * 74)

    # save result
    out = os.path.join(SCRIPTS, "heladera_gafa_resultado.json")
    json.dump({
        "specs": SPECS_HELADERA, "S_diseno_VA": S_DISENO_VA,
        "dimensionamiento": {k: d[k] for k in claves_mostrar},
        "clases": {k: d[k + "_clase"] for k in claves_mostrar},
        "bom_capacitor": bom,
    }, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"\n[salvado {out}]")
    return 0


if __name__ == "__main__":
    sys.exit(correr())
