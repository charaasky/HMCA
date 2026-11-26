#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""dominio_luminaria_led.py — CANDIDATE domain 'luminaria_led' (autor=modelo, NOT canonical).

Demonstrates the 'ask for anything' flow per Fable (post-authorize, not pre-declare):
  - The request '100-lumen LED luminaire' does NOT match any registered domain.
  - The system DETECTS it (does not lie), looks up the efficacy (lm/W) in a catalog as SOURCE,
    and proposes a domain candidate with the formulas (which do NOT change: P=Φ/η, I=P/V, section).
  - The candidate stays `autor=modelo` / `asumida` (unverified), NEVER canonical, until
    a human ratifies it (registrar_spec with engineer/user source).

Formulas (physics, do not change):
  potencia_W = flujo_lm / eficacia_lm_por_W        (P = Φ/η)
  corriente_A = potencia_W / tension_V               (I = P/V)
  cable section = current / density             (as in winding/cabling)
The EFFICACY (η) is the datum looked up in a catalog; it is NOT invented.
The 100 lumens are LUMINOUS FLUX (case input), not power.

This file is NOT an authoritative domain: it is the CANDIDATE that a human reviews.
"""
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# catalog source (confidence: web search, no human verification yet)
FUENTE_LED = {
    "fuente_id": "led_eficacia_lm_w",
    "tipo": "tabla_fabricante",
    "ref": "eficacia luminosa típica de LED ~100 lm/W (catálogo/hoja de datos)",
    "urls": [
        "https://www.iea.org/commentaries/the-next-wave-of-led-lighting-smarter-circular-and-more-efficient",
    ],
    "eficacia_lm_por_W": 100.0,
}

# CANDIDATE DOMAIN (autor=modelo). It is NOT registered as canonical by itself.
DOMINIO_LUMINARIA_LED = {
    "nombre": "luminaria LED (dimensionado básico)",
    "autor": "modelo",  # <- candidacy mark, NEVER 'ingeniero'
    "inputs": [
        {"name": "flujo_lm", "question": "flujo luminoso requerido (lúmenes)", "unit": "lm"},
        {"name": "tension_V", "question": "tensión de alimentación (V)", "unit": "V"},
        {"name": "densidad_corriente_A_mm2", "question": "densidad de corriente (A/mm²)", "unit": "A/mm2"},
    ],
    "demo_inputs": {
        "flujo_lm": 100.0, "tension_V": 12.0, "densidad_corriente_A_mm2": 4.0,
    },
    "constantes": [
        {"name": "eficacia_lm_por_W", "valor": 100.0,
         "fuente": "fuente:led_eficacia_lm_w", "porque_no_input": "dato de catálogo del LED, no parámetro del caso"},
    ],
}


def calcular(inputs, eficacia_lm_por_W=100.0):
    """Deterministic deduction (the formulas do NOT change)."""
    flujo = float(inputs["flujo_lm"])
    V = float(inputs["tension_V"])
    J = float(inputs["densidad_corriente_A_mm2"])
    potencia_W = flujo / eficacia_lm_por_W          # P = Φ/η
    corriente_A = potencia_W / V                     # I = P/V
    seccion_mm2 = corriente_A / J                    # S = I/J
    return {
        "potencia_W": round(potencia_W, 3),
        "corriente_A": round(corriente_A, 4),
        "seccion_cable_mm2": round(seccion_mm2, 4),
    }


if __name__ == "__main__":
    r = calcular(DOMINIO_LUMINARIA_LED["demo_inputs"])
    print("Luminaria LED 100 lúmenes — candidata (autor=modelo):")
    for k, v in r.items():
        print(f"  {k}: {v}")
    print("\nEsta es CANDIDATA: falta ratificar la especificación y la fuente por un humano.")
