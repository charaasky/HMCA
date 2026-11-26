#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""experimento_cobertura_gobernanza.py — Coverage / precision / recall of HCMA governance.

Measures (deterministic, no LLM, no logprobs) what the workshop paper declares NOT measured
("Cost, reported honestly" and Limitation 5): the cost of provisioning authority and the
degradation under incomplete lineage.

The honest metric of the "effective branching factor" of a pruning is COVERAGE
(fraction of branches that survive); the "survival rate of the ground-truth" is RECALL.
Both are measured here on the real core (memoria_sqlite.py), not on a distribution.

  1. Gate at full lineage: correct vs hallucinated -> coverage / precision / recall.
  2. Cost of provisioning authority: recall as a function of the fraction of sources marked
     as oracle (default-deny leaves everything non-traceable in "limbo").
  3. Degradation curve (T2.5): stale-use after revising a source, according to the fraction of
     declared lineage.

Usage:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/experimento_cobertura_gobernanza.py
Exit 0 = deterministic invariants verified.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402


def _mk():
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_cobertura_")
    os.close(fd)
    return HCMAMemoriaSQLite(path)


def resultado_1(n_correctas=100, n_alucinadas=100):
    """Coverage / precision / recall of the gate at full lineage."""
    m = _mk()
    v_orac = m.write("fuente:oraculo", {"ok": True}, fuente="tabla", verified=True)

    surv_correctas = 0
    for i in range(n_correctas):
        m.write(f"correcta:{i}", {"v": i}, fuente="calculadora", verified=True,
                derived_from={"fuente:oraculo": v_orac})
        if m.current(f"correcta:{i}") is not None:
            surv_correctas += 1

    surv_alucinadas = 0
    for i in range(n_alucinadas):
        # the agent "claims" a computed result WITHOUT lineage -> rule E1 forces it unverified
        m.write(f"alucinada:{i}", {"v": 999}, fuente="calculadora", verified=True,
                derived_from={})
        if m.current(f"alucinada:{i}") is not None:
            surv_alucinadas += 1

    total = n_correctas + n_alucinadas
    surv = surv_correctas + surv_alucinadas
    coverage = surv / total
    precision = (surv_correctas / surv) if surv else 0.0
    recall = surv_correctas / n_correctas
    return {
        "n_correctas": n_correctas, "n_alucinadas": n_alucinadas,
        "surviven": surv, "cobertura": round(coverage, 4),
        "precision": round(precision, 4), "recall": round(recall, 4),
        "correctas_surviven": surv_correctas, "alucinadas_surviven": surv_alucinadas,
    }


def resultado_2(n_fuentes=100):
    """Recall as a function of the fraction of sources marked as oracle."""
    filas = []
    for q in [0.0, 0.25, 0.5, 0.75, 1.0]:
        m = _mk()
        n_oraculos = int(q * n_fuentes)
        versiones = {}
        for j in range(n_fuentes):
            if j < n_oraculos:
                versiones[j] = m.write(f"fuente:{j}", {"v": 1.0}, fuente="tabla", verified=True)
            else:
                versiones[j] = m.write(f"fuente:{j}", {"v": 1.0}, fuente="base", verified=False)
        surv = 0
        for j in range(n_fuentes):
            m.write(f"derivada:{j}", {"v": 2.0}, fuente="calculadora", verified=True,
                    derived_from={f"fuente:{j}": versiones[j]})
            if m.current(f"derivada:{j}") is not None:
                surv += 1
        filas.append({"oraculos": round(q, 2), "recall": round(surv / n_fuentes, 4)})
    return filas


def resultado_3(n=100):
    """Degradation curve: stale-use after revising source B, according to declared lineage.

    Each derived fact actually depends on A and B, but declares only a fraction p of the
    lineage (when it does not declare B, revising B does not invalidate it -> stale leaks through)."""
    filas = []
    for p in [0.0, 0.25, 0.5, 0.75, 1.0]:
        m = _mk()
        vA = m.write("fuente:A", {"v": 1.0}, fuente="tabla", verified=True)
        vB = m.write("fuente:B", {"v": 1.0}, fuente="tabla", verified=True)
        for i in range(n):
            completo = (i / n) < p
            df = {"fuente:A": vA, "fuente:B": vB} if completo else {"fuente:A": vA}
            m.write(f"derivada:{i}", {"v": 2.0}, fuente="calculadora", verified=True,
                    derived_from=df)
        m.write("fuente:B", {"v": 2.0}, fuente="tabla", verified=True)  # revision
        vivos = sum(1 for i in range(n) if m.current(f"derivada:{i}") is not None)
        filas.append({"linaje_declarado": round(p, 2), "stale_use": round(vivos / n, 4)})
    return filas


def run():
    print("=" * 78)
    print("COBERTURA / PRECISIÓN / RECALL DE LA GOBERNANZA HCMA (determinista, sin LLM)")
    print("=" * 78)

    r1 = resultado_1()
    print("\n[1] Gate a linaje completo (correctas vs alucinadas):")
    print(f"    correctas: {r1['n_correctas']:3d}  -> {r1['correctas_surviven']:3d} sobreviven")
    print(f"    alucinadas: {r1['n_alucinadas']:3d} -> {r1['alucinadas_surviven']:3d} sobreviven")
    print(f"    COBERTURA (ramas que sobreviven)           = {r1['cobertura']}")
    print(f"    PRECISIÓN (correctas / sobrevivientes)      = {r1['precision']}")
    print(f"    RECALL (correctas sobrevivientes / correctas) = {r1['recall']}")

    r2 = resultado_2()
    print("\n[2] Costo de provisión de autoridad (recall vs fracción de oráculos):")
    print("      oráculos    recall")
    for f in r2:
        print(f"        {f['oraculos']:.2f}       {f['recall']:.3f}")

    r3 = resultado_3()
    print("\n[3] Degradación bajo linaje incompleto (stale-use tras revisión vs linaje declarado):")
    print("      linaje declarado    stale-use")
    for f in r3:
        print(f"           {f['linaje_declarado']:.2f}             {f['stale_use']:.3f}")

    ok = True
    p_ok = abs(r1["precision"] - 1.0) < 1e-9
    r_ok = abs(r1["recall"] - 1.0) < 1e-9
    c2_ok = all(abs(f["recall"] - f["oraculos"]) < 1e-9 for f in r2)
    c3_ok = all(abs(f["stale_use"] - (1.0 - f["linaje_declarado"])) < 1e-9 for f in r3)
    ok = p_ok and r_ok and c2_ok and c3_ok

    print("\n" + "=" * 78)
    print("VEREDICTO (invariantes deterministas):")
    print(f"  precisión = 1.0 (ninguna alucinación se cuela)   -> {'OK' if p_ok else 'FALLO'}")
    print(f"  recall = 1.0 a linaje completo                   -> {'OK' if r_ok else 'FALLO'}")
    print(f"  recall = fracción de oráculos (costo lineal)     -> {'OK' if c2_ok else 'FALLO'}")
    print(f"  stale-use = 1 - linaje declarado (T2.5)          -> {'OK' if c3_ok else 'FALLO'}")
    print("=" * 78)
    print("VERDE" if ok else "ROJO")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
