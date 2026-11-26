#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""HUB experiment — how much false invalidation does conservative lineage produce
(mark every descendant) when the core changes but most descendants
do NOT change their result?

This is the question Fable marked as the 'limiting factor': the blast radius.

Design:
  - N descendants of a core. Each descendant has result = k_nucleo * factor_i.
  - When correcting k_nucleo (1.0 -> 2.0), only a fraction `frac_rompe` of the descendants
    REALLY changes its result; the rest (1-frac_rompe) were independent of the numerical
    change (e.g. that descendant's factor made the change not affect its output,
    or they depended on another branch).
  - 'conservative lineage' marks stale ALL descendants (all have derived_from
    to the core), without distinguishing.
  - 'lineage + CI' re-tests and only re-writes what truly changed.

Metrics (sweep frac_rompe from 0 to 1):
  - false invalidation = fraction of VALID descendants (not changed) discarded.
  - stale-use = fraction that reports an old value.
  - cost = number of re-tests executed.

Expected conclusion (to verify, not assume):
  - at low frac_rompe (core changed little): CI wins (avoids discarding what is valid).
  - at high frac_rompe: they converge.
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "..", "dashboard")
if DASH not in sys.path:
    sys.path.insert(0, DASH)


def simular(n_desc, frac_rompe):
    """First-order model, deterministic.

    Returns a dict with the metrics of two policies under a correction to the core."""
    n_rompe = int(round(n_desc * frac_rompe))     # descendants that REALLY change
    n_no_rompe = n_desc - n_rompe                  # descendants that do NOT change

    # ── policy: conservative lineage (marks stale ALL descendants) ──
    #  - stale-use: 0 (none reports old, all become 'unverified')
    #  - invalid: ALL (n_desc) remain without an answer until re-test
    #  - false-inv: the n_no_rompe that were discarded unduly
    linaje = {
        "stale_use": 0.0,
        "invalidos": n_desc,
        "falsa_inv": n_no_rompe,
        "coste": 0.0,  # nothing re-verified yet
    }

    # ── policy: lineage + CI (re-tests and only re-writes what changed) ──
    #  - re-tests the n_desc (to know which change), cost = n_desc
    #  - only re-writes n_rompe; the n_no_rompe are re-certified without change → 0 false
    ci = {
        "stale_use": 0.0,
        "invalidos": 0,
        "falsa_inv": 0,
        "coste": n_desc,  # re-test of all to discriminate
    }

    # normalize false_inv as a fraction of valid (not-broken)
    linaje["falsa_inv_frac"] = linaje["falsa_inv"] / n_no_rompe if n_no_rompe else 0.0
    linaje["falsa_inv"] = linaje["falsa_inv"] / n_desc  # fraction of the total

    return linaje, ci


def main():
    N = 100  # hub descendants
    FRAC_ROMPE = [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0]

    print(f"Núcleo (hub) con {N} descendientes. Barrido de frac_rompe (fracción que DE VERDAD cambia).\n")
    print(f"{'frac_rompe':>10} | {'linaje falsa-inv':>17} {'linaje invalidos':>16} | "
          f"{'CI falsa-inv':>12} {'CI invalidos':>11} {'CI coste':>8}")

    filas = []
    for fr in FRAC_ROMPE:
        linaje, ci = simular(N, fr)
        filas.append({"frac_rompe": fr,
                      "linaje_falsa_inv": round(linaje["falsa_inv"], 3),
                      "linaje_invalidos": linaje["invalidos"],
                      "ci_falsa_inv": round(ci["falsa_inv"], 3),
                      "ci_invalidos": ci["invalidos"],
                      "ci_coste": ci["coste"]})
        print(f"{fr:>10.2f} | {linaje['falsa_inv']:>17.3f} {linaje['invalidos']:>16} | "
              f"{ci['falsa_inv']:>12.3f} {ci['invalidos']:>11} {ci['coste']:>8}")

    out = os.path.join(HERE, "router_hub_resultados.json")
    json.dump(filas, open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n[salvado {out}]")

    print("\nLectura honesta:")
    print("  - El linaje conservador SIEMPRE descarta TODO descendiente (invalidos = N),")
    print("    así que su falsa-invalidación crece cuando frac_rompe es BAJO (el cambio no")
    print("    afectaba a la mayoría, pero igual los invalida a todos).")
    print("  - El CI re-testa y NO descarta lo que no cambió (falsa-inv = 0 siempre),")
    print("    a costa de re-testear todos (coste = N).")
    print("  - Trade-off real: linaje = 0 coste inmediato pero máxima falsa-invalidación;")
    print("    CI = coste N pero 0 falsos. La decisión depende del coste relativo de")
    print("    re-test vs. re-computar un descendiente válido descartado.")


if __name__ == "__main__":
    main()
