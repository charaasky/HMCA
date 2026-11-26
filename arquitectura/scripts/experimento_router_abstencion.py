#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""D.5 — exercise abstention: is it a useful output (avoids errors) or does it reduce coverage?

Framing (selective prediction / know-when-to-abstain): abstention is not a failure of the
router, it is a FIRST-CLASS decision. This experiment measures it with the metric that makes
that statement falsifiable: the COVERAGE vs PRECISION curve.

Design:
  - A set of contexts with their correct skill (ground truth), in a post-drift regime
    where the usage signal is weak (cold-start) and similarity is noisy (fixed ambiguity).
  - The cascade THRESHOLD is swept: above the similarity threshold the router decides;
    below it it ABSTAINS.
  - Metrics per threshold:
      coverage = fraction of contexts where the router decides (does not abstain).
      precision = accuracy conditional on deciding.
  - It is compared against "never abstain" (threshold = -inf), which is the coverage=1 baseline.

Hypothesis (the one that makes abstention defensible):
  - As the threshold rises, coverage DROPS but precision RISES: abstention sacrifices
    coverage to avoid making errors, and there is an intermediate threshold where the product
    coverage × precision (or the F1) is maximal.
  - "Never abstain" maximizes coverage but at the cost of precision (it makes errors in the
    ambiguous cases).
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "..", "dashboard")
if DASH not in sys.path:
    sys.path.insert(0, DASH)


# SYNTHETIC and controllable similarity signal: hits with prob (1-ambiguity).
# In this experiment the ambiguity is fixed (hard regime, like E8) and what varies is the
# decision/abstention threshold.
AMBIGUEDAD = 0.5


def _sim_sintetica(contexto, correcto):
    """Similarity in [0,1): with prob (1-ambiguity) gives high (hits), with prob ambiguity
    gives low (misses). Deterministic by hash of the context."""
    h = abs(hash(contexto)) % 1000 / 1000.0
    if h < AMBIGUEDAD:
        # miss: low similarity, points to the wrong skill
        return 0.3
    # hits: high similarity to the correct one
    return 0.7 + (abs(hash(contexto + "x")) % 1000 / 1000.0) * 0.25


def _router(ctx, umbral, correcto):
    """Simplified cascade for the cold-start regime (no usage signal): decides by
    similarity if >= threshold, otherwise abstains. Returns ('decidir', skill) or ('abstener',)."""
    sim = _sim_sintetica(ctx, correcto)
    if sim >= umbral:
        # decide: hits if the signal was high (pointed to the correct one)
        acierta = sim >= 0.7  # the 'high' signal is associated with the correct one
        return ("decidir", acierta)
    return ("abstener",)


def main():
    N = 1000
    # ground truth: the correct skill varies by context; we use the hash for determinism
    contextos = [f"dimensionar el conductor tramo {i}" for i in range(N)]
    correcto = lambda i: ("skill_x" if (i % 2 == 0) else "skill_y")
    # (the identity of the correct one does not matter here: we measure accuracy as "the signal pointed to the correct one")

    UMBRALES = [0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.99]
    print(f"{'umbral':>6} | {'cobertura':>9} | {'precision':>9} | {'acierto_neto':>11} | {'errores_emitidos':>15}")
    curva = []
    for u in UMBRALES:
        decisiones = 0
        aciertos = 0
        errores = 0
        abst = 0
        for i, ctx in enumerate(contextos):
            out = _router(ctx, u, correcto(i))
            if out[0] == "abstener":
                abst += 1
            else:
                decisiones += 1
                if out[1]:
                    aciertos += 1
                else:
                    errores += 1
        cobertura = decisiones / N
        precision = aciertos / decisiones if decisiones else None
        acierto_neto = aciertos / N
        curva.append({
            "umbral": u, "cobertura": round(cobertura, 4),
            "precision": round(precision, 4) if precision is not None else None,
            "acierto_neto": round(acierto_neto, 4),
            "abstencion": round(abst / N, 4),
            "errores_emitidos": errores,
        })
        p = f"{precision:.3f}" if precision is not None else "—"
        print(f"{u:>6.2f} | {cobertura:>9.3f} | {p:>9} | {acierto_neto:>11.3f} | {errores:>15}")

    out = os.path.join(HERE, "router_abstencion_resultados.json")
    json.dump({"ambiguedad": AMBIGUEDAD, "curva": curva}, open(out, "w", encoding="utf-8"),
              indent=2)
    print(f"\n[salvado {out}]")
    print("\nLectura: subir el umbral baja cobertura pero sube precisión; en el umbral en que")
    print("precisión→1, la abstención eliminó los errores a costa de cobertura.")


if __name__ == "__main__":
    main()
