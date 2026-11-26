#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Exploration #4 — iterative drift: does version collapse degrade after N consecutive
corrections, or does it stay stable?

Honest question: in production the world changes MANY times (not once). Does the system
accumulate `stale` garbage facts after each drift, or does collapse + CI keep them bounded?

Design (deterministic, real HCMA):
  - Core k_nucleo with descendants (mecanica, electro, economia) that depend on it.
  - Sequence of K consecutive corrections to the core (1.0 → 2.0 → 3.0 → ... → K).
  - After EACH correction, selective CI (reverificar_descendientes) is applied to re-certify
    the dependents with the new value.
  - Measured, after each drift:
      * number of total facts in facts (grows linearly = expected, each write creates a new version)
      * number of stale facts (does it accumulate garbage without limit?)
      * number of current facts (authority=1) — must be constant (core + 3 dependents + skills)
      * correctness of the current state (consistent with the last k)

Hypothesis to falsify:
  - The number of stale facts GROWS with each drift (each correction marks stale the previous version).
    That is EXPECTED (the history is auditable). The question is not whether it grows, but whether the CURRENT
    state stays correct and whether stale is only "history", not "operational garbage".
  - The number of CURRENT facts stays constant (not inflated with drifts).
  - The correctness of the current state is 100% after each drift (stale-use = 0 always).
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "..", "dashboard")
if DASH not in sys.path:
    sys.path.insert(0, DASH)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import skills as S  # noqa: E402


NUCLEO = "k_nucleo"
DEPENDIENTES = {"res_mecanica": 2.0, "res_electro": 3.0, "res_economia": 5.0}


def _calcular(clave, k):
    return {"res_mecanica": k * 2.0, "res_electro": k * 3.0, "res_economia": k * 5.0}[clave]


def _stats(m):
    """Memory state snapshots."""
    c = m.conn
    n_total = c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    n_stale = c.execute("SELECT COUNT(*) FROM facts WHERE stale=1").fetchone()[0]
    n_vig = c.execute("SELECT COUNT(*) FROM facts WHERE authority=1").fetchone()[0]
    return n_total, n_stale, n_vig


def _correctitud(m, k_esperado):
    """Fraction of dependents whose current value is consistent with k_esperado."""
    ok = 0
    for clave, factor in DEPENDIENTES.items():
        f = m.current(clave)
        if f is not None and abs(f["value"] - k_esperado * factor) < 1e-9:
            ok += 1
    return ok / len(DEPENDIENTES)


def main():
    K = 10  # number of consecutive corrections
    m = HCMAMemoriaSQLite(":memory:")
    vn = S.registro(m, "integral", "núcleo", fuente="ingeniero", verified=True,
                    meta={"costo": 0})
    k_ver = m.write(NUCLEO, 1.0, unit="", fuente="usuario", verified=True)
    for clave, factor in DEPENDIENTES.items():
        m.write(clave, 1.0 * factor, unit="", fuente="calculadora", verified=True,
                derived_from={NUCLEO: k_ver, "skill:integral": vn})

    print(f"{'drift':>5} | {'k':>5} | {'total facts':>11} | {'stale':>6} | {'vigentes':>8} | {'correctitud':>11}")
    filas = []
    # initial state
    tt, st, vig = _stats(m)
    filas.append({"drift": 0, "k": 1.0, "total": tt, "stale": st, "vigentes": vig,
                  "correctitud": _correctitud(m, 1.0)})
    print(f"{0:>5} | {1.0:>5} | {tt:>11} | {st:>6} | {vig:>8} | {_correctitud(m, 1.0):>11.3f}")

    for d in range(1, K + 1):
        k_nuevo = 1.0 + d * 0.5  # 1.5, 2.0, 2.5, ...
        # correct core (drift)
        m.write(NUCLEO, k_nuevo, unit="", fuente="usuario", verified=True)
        # selective CI: re-certify dependents
        S.reverificar_descendientes(m, NUCLEO, _calcular)
        tt, st, vig = _stats(m)
        corr = _correctitud(m, k_nuevo)
        filas.append({"drift": d, "k": k_nuevo, "total": tt, "stale": st,
                      "vigentes": vig, "correctitud": corr})
        print(f"{d:>5} | {k_nuevo:>5.1f} | {tt:>11} | {st:>6} | {vig:>8} | {corr:>11.3f}")

    out = os.path.join(HERE, "router_drift_iterativo_resultados.json")
    json.dump(filas, open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n[salvado {out}]")

    print("\nLectura honesta:")
    print("  - 'total' crece con cada drift (cada write crea versión nueva): historial auditable.")
    print("  - 'stale' crece porque cada corrección marca stale la versión previa de los")
    print("    descendientes: es HISTORIA, no basura operativa (queda para auditoría).")
    print("  - 'vigentes' debe mantenerse CONSTANTE (= núcleo + 3 dependientes + skills):")
    print("    no se infla con drifts → el estado operativo no degrada.")
    print("  - 'correctitud' debe ser 1.000 siempre → stale-use = 0 tras N drifts.")


if __name__ == "__main__":
    main()
