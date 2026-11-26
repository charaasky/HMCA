#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Calibration of the router's τ_alto / τ_bajo (step 5, Fable 5.2 opinion).

REPRESENTATIVE golden set (query -> expected decision). Builds a TF-IDF index over a fixed set of
skills, prints the top-1 score of each query (to see the distribution) and does a grid search of
(τ_alto, τ_bajo) maximizing accuracy against the labels.

This is not a runtime test: it is the tool for choosing the default thresholds.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

import router_skills as RS  # noqa: E402
import indice_skills as IDX  # noqa: E402

# Canonical skills (cards) used for calibration.
CARDS = {
    "luminaria_led": "dimensionar luminaria led: potencia flujo eficacia corriente tension seccion cable",
    "corriente": "corriente electrica: potencia tension ley de ohm amperios voltaje",
    "cargador_bateria": "tiempo de carga bateria: capacidad corriente horas amperios",
    "multimetro": "medicion corriente shunt: voltaje resistencia ohmios",
}

# Golden set: (query, expected decision, expected skill or None).
GOLDEN = [
    # use (strong match)
    ("dimensionar una luminaria led", "usar", "luminaria_led"),
    ("calcula la corriente del motor", "usar", "corriente"),
    ("cuanto tarda en cargar la bateria", "usar", "cargador_bateria"),
    ("medir corriente con shunt", "usar", "multimetro"),
    ("potencia de una luminaria led", "usar", "luminaria_led"),
    ("cual es la corriente electrica", "usar", "corriente"),
    ("tiempo de carga de una bateria", "usar", "cargador_bateria"),
    ("dimensionar el cable de la luminaria", "usar", "luminaria_led"),
    # suggest (weak match / gray band)
    ("diseñame una lampara led", "sugerir", "luminaria_led"),
    ("cuanta potencia necesito", "sugerir", "corriente"),
    ("cuanto dura la carga", "sugerir", "cargador_bateria"),
    ("que es un shunt", "sugerir", "multimetro"),
    # abstain (no match)
    ("actualizá el ledger", "abstener", None),
    ("escribí un poema", "abstener", None),
    ("hola", "abstener", None),
    ("resumen de la teoria de la relatividad", "abstener", None),
    ("que hora es", "abstener", None),
    ("traduce esto al ingles", "abstener", None),
]


def _decidir(idx, q, ta, tb):
    # returns the simulated decision (without store or uses): only the semantic band
    top = idx.top_k(q, k=1)
    if not top:
        return "abstener", None
    sid, s = top[0]
    if s >= ta:
        return "usar", sid
    if s >= tb:
        return "sugerir", sid
    return "abstener", sid


def main():
    idx = IDX.IndiceSkills(CARDS)
    print("=" * 72)
    print("Distribución de scores (top-1) para el golden set:")
    print("=" * 72)
    for q, exp, sid in GOLDEN:
        top = idx.top_k(q, k=1)
        s = top[0][1] if top else 0.0
        sk = top[0][0] if top else "-"
        print(f"  {exp:8s} {s:.3f}  {sk:14s}  {q!r}")

    print("\n" + "=" * 72)
    print("Grid search (τ_alto, τ_bajo) -> exactitud:")
    print("=" * 72)
    best = None
    for ta in [round(x / 100, 2) for x in range(15, 71, 5)]:
        for tb in [round(x / 100, 2) for x in range(0, 26, 5)]:
            if tb >= ta:
                continue
            acc = 0
            for q, exp, sid in GOLDEN:
                d, sk = _decidir(idx, q, ta, tb)
                ok = (d == exp) and ((d != "abstener" and sk == sid) or d == "abstener")
                if ok:
                    acc += 1
            acc /= len(GOLDEN)
            if best is None or acc > best[0]:
                best = (acc, ta, tb)
    acc, ta, tb = best
    print(f"\nMejor: τ_alto={ta} τ_bajo={tb} -> exactitud {acc:.0%} ({int(acc*len(GOLDEN))}/{len(GOLDEN)})")
    print(f"Actuales default: τ_alto={RS.UMBRAL_ALTO} τ_bajo={RS.UMBRAL_BAJO}")


if __name__ == "__main__":
    main()
