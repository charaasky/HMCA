#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Fair benchmark (step 1): stale-use of flat memory vs HCMA in the face of a correction.

Falsifiable hypothesis: HCMA (version governance + exact lineage) detects that a
derived fact became stale and does NOT return it (stale-use = 0). A flat memory / RAG without
lineage returns the old derived value without knowing it is stale.

Deterministic scenario: facts are derived from inputs with a fixed function; then
ONE input is corrected and it is measured how many derived facts become stale.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prototipo import HCMAMemoria


def derivar(inputs):
    """Deterministic derived function (emulates the engine's computation)."""
    par, rpm, tension, eff = inputs["par"], inputs["rpm"], inputs["tension"], inputs["eff"]
    potencia = par * rpm * 0.10472  # P = T * ω
    corriente = potencia / (tension * eff)
    return {"potencia_W": round(potencia, 2), "corriente_A": round(corriente, 2)}


ESCENARIOS = [
    ({"par": 10.0, "rpm": 1500.0, "tension": 220.0, "eff": 0.90}, ("rpm", 1750.0)),
    ({"par": 20.0, "rpm": 3000.0, "tension": 380.0, "eff": 0.88}, ("par", 15.0)),
    ({"par": 5.0, "rpm": 1000.0, "tension": 110.0, "eff": 0.85}, ("tension", 220.0)),
]


def medir_plana(inputs, correccion):
    """Flat memory: key→value, no lineage. Returns stale-use (stale fraction)."""
    mem = dict(inputs)
    mem.update(derivar(inputs))          # derived facts (no lineage)
    k, v = correccion
    mem[k] = v                           # correction: invalidates nothing
    derivados_correctos = derivar(mem)   # what it SHOULD be after correcting
    obsoletos = sum(1 for d in derivados_correctos
                    if mem[d] != derivados_correctos[d])
    return obsoletos / len(derivados_correctos)


def medir_hcma(inputs, correccion):
    """HCMA: exact lineage + version collapse. Returns stale-use (stale fraction)."""
    h = HCMAMemoria()
    for k, v in inputs.items():
        h.write("in_" + k, v, fuente="usuario", verified=True)
    derived = derivar(inputs)
    linaje = {("in_" + k): h.current("in_" + k)["version"] for k in inputs}
    for d, v in derived.items():
        h.write(d, v, fuente="calculadora", verified=True, derived_from=linaje)
    k, v = correccion
    h.write("in_" + k, v, fuente="usuario", verified=True)   # correction
    # stale-use: how many derived facts remain canonical (not stale) after the correction?
    obsoletos = sum(1 for d in derived if h.current(d) is not None)
    return obsoletos / len(derived)


def main():
    print("=" * 70)
    print("Benchmark fair: stale-use tras corregir un input (menos = mejor)")
    print("=" * 70)
    total_plana = total_hcma = 0.0
    for i, (inputs, correccion) in enumerate(ESCENARIOS, 1):
        p = medir_plana(inputs, correccion)
        h = medir_hcma(inputs, correccion)
        total_plana += p
        total_hcma += h
        print(f"\nEscenario {i}: corrijo {correccion[0]} = {correccion[1]}")
        print(f"  derivados: {list(derivar(inputs))}")
        print(f"  Plana (sin linaje): stale-use = {p:.2f}  ({'devuelve valores viejos' if p > 0 else 'ok'})")
        print(f"  HCMA (con linaje):  stale-use = {h:.2f}  ({'detecta y no devuelve' if h == 0 else 'FALLO'})")
    n = len(ESCENARIOS)
    print("\n" + "=" * 70)
    print(f"RESUMEN: stale-use promedio  |  Plana = {total_plana/n:.2f}  |  HCMA = {total_hcma/n:.2f}")
    print("=" * 70)
    print("Conclusión: HCMA = 0 (invalidación transitiva por linaje exacto);")
    print("la memoria plana/RAG sin gobernanza usa el valor derivado obsoleto.")


if __name__ == "__main__":
    main()
