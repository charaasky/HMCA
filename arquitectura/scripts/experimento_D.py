#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""experimento_D.py — EXECUTABLE FALSIFICATION BATTERY: governance vs retrieval.

This is NOT an "experiment" nor a "performance benchmark": it is an executable specification.
Each property (P1/P2/P3) states what a governed memory MUST guarantee and demonstrates,
deterministically, that:

  (i)  HCMA satisfies the property BY CONSTRUCTION, and
  (ii) a baseline without that governance VIOLATES it.

This is what Fable (veredicto_paper) explicitly asked for: NOT to present "0.00 vs 1.00" as an
empirical result (it is tautological, the mechanism enforces it), but rather as "property → test →
baseline violates it / HCMA satisfies it". Therefore:
  - It is DETERMINISTIC (a single run, without "3 seeds": in a deterministic system the seeds
    mean nothing and a strict reviewer uses them to discredit the rigor).
  - The baseline is an "associative memory without lineage" (our abstraction), NEVER called
    Mem0 / A-MEM / Graphiti (they were not run; see the Related Work section of the paper).
  - It exits with code 0 only if ALL properties hold (HCMA satisfied + baseline
    violated). It serves as a CI gate.

The three perturbations (falsifiable):
  P1. Source revision  -> stale-use rate.      HCMA ≈ 0 (collapse by lineage); associative ≈ 1.
  P2. Plausible contamination (co-activation)     HCMA ≈ 0 (without lineage it is not canonical);
                                                  associative ≈ 1 (Hebbian reinforcement lets everything through).
  P3. Deontic horizon  -> omissions/false closures. With deontics 0/0; without deontics (B1) omits.

Usage:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/experimento_D.py
"""

import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import deontica  # noqa: E402
from baseline_asociativo import MemoriaAsociativa  # noqa: E402


def _mk():
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_falsacion_")
    os.close(fd)
    return HCMAMemoriaSQLite(path)


# ─────────────────────────────────────────────────────────────────────────────
# P1 — source revision -> stale-use rate
# ─────────────────────────────────────────────────────────────────────────────
def perturbacion_1(n_hechos=30):
    """Canonical source changes version; n facts derive from it.

    Property: after the revision, NO derived fact may remain readable as current.
    HCMA collapses by lineage (stale-use=0); the associative one leaves them alive (stale-use=1)."""
    # --- HCMA ---
    m = _mk()
    v_fuente = m.write("fuente:tabla", {"valor": 1.0}, fuente="tabla", verified=True)
    for i in range(n_hechos):
        m.write(f"derivado:{i}", {"valor": i}, fuente="calculadora", verified=True,
                derived_from={"fuente:tabla": v_fuente})
    # correct the source (new version) -> collapse
    m.write("fuente:tabla", {"valor": 2.0}, fuente="tabla", verified=True)
    vivos = sum(1 for i in range(n_hechos) if m.current(f"derivado:{i}") is not None)
    stale_use_hcma = vivos / n_hechos  # fraction that would still be used incorrectly (must be 0)

    # --- Associative (our abstraction without lineage) ---
    ma = MemoriaAsociativa()
    ma.escribir("fuente:tabla", 1.0)
    for i in range(n_hechos):
        ma.escribir(f"derivado:{i}", i)
        ma.co_activar("fuente:tabla", f"derivado:{i}")
    ma.escribir("fuente:tabla", 2.0)  # in associative it only overwrites; there is no invalidation
    vivos_asoc = sum(1 for i in range(n_hechos) if f"derivado:{i}" in ma.hechos)
    stale_use_asociativo = vivos_asoc / n_hechos  # must be 1 (nothing is invalidated)

    return {"hcma": round(stale_use_hcma, 3), "asociativo": round(stale_use_asociativo, 3)}


# ─────────────────────────────────────────────────────────────────────────────
# P2 — plausible contamination -> non-canonical items into the deliverable
# ─────────────────────────────────────────────────────────────────────────────
def perturbacion_2(n_contaminantes=20, n_repeticiones=10):
    """Injects false-but-coherent facts repeated N times.

    Property: a fact without lineage to an oracle must NOT reach a canonical deliverable,
    no matter how many times it co-occurs with the canonical source (provenance beats
    popularity). The associative one reinforces them by co-activation and lets them through."""
    # --- HCMA ---
    m = _mk()
    m.write("fuente:canonica", {"v": 1.0}, fuente="tabla", verified=True)
    colados_hcma = 0
    for i in range(n_contaminantes):
        for _ in range(n_repeticiones):
            m.write(f"contaminante:{i}", {"v": 999.0}, fuente="base", verified=False)
        cur = m.current(f"contaminante:{i}")
        if cur is not None and cur["verified"]:
            colados_hcma += 1
    tasa_hcma = colados_hcma / n_contaminantes  # must be 0

    # --- Associative ---
    ma = MemoriaAsociativa()
    ma.escribir("fuente:canonica", 1.0)
    colados_asoc = 0
    for i in range(n_contaminantes):
        ma.escribir(f"contaminante:{i}", 999.0)
        for _ in range(n_repeticiones):
            ma.co_activar("fuente:canonica", f"contaminante:{i}")
        if f"contaminante:{i}" in ma.recuperar("fuente:canonica", top_k=n_contaminantes * 2):
            colados_asoc += 1
    tasa_asoc = colados_asoc / n_contaminantes  # must be 1 (everything gets through)

    return {"hcma": round(tasa_hcma, 3), "asociativo": round(tasa_asoc, 3)}


# ─────────────────────────────────────────────────────────────────────────────
# P3 — deontic horizon -> omissions / false closures
# ─────────────────────────────────────────────────────────────────────────────
# Honest design (Fable, cierre_deontico_drift §5): the metric measures PRODUCTS with authority at
# the end, read by an independent AUDITOR (only current+lineage, not importing deontica.py). Using
# the deontic bookkeeping to measure the success of the deontics would be circular. Baseline B1
# ("free closure") models the agent that marks "done" on its own without an authority gate.
# ─────────────────────────────────────────────────────────────────────────────
def perturbacion_3(k_entregables=100):
    """k deliverables under a long budget.

    With deontics: the gate forces each product to reach authority (lineage to an oracle) before
    closing -> 0 omissions, 0 false closures.
    Without deontics (B1): the agent writes "done" without lineage -> rule E1 leaves it unverified ->
    k omissions."""
    # ── WITH deontics ──
    m = _mk()
    v_orac = m.write("cota:cvt:producto", {"conforme": True}, fuente="tabla", verified=True)
    for k in range(k_entregables):
        m.write(f"prod:{k}", {"hecho": False}, fuente="base", verified=False)
        deontica.registrar_entregable(m, f"e{k}", "producto", f"tarea {k}",
                                      predicado_ref="p",
                                      requiere=[(f"prod:{k}", "conforme")])
        m.write(f"prod:{k}", {"hecho": True}, fuente="calculadora", verified=True,
                derived_from={f"cota:cvt:producto": v_orac})
        deontica.cerrar_si_predicado(m, f"e{k}", lambda mem: (True, "ok"))

    # independent auditor (does not import deontica.py): read the actual state of the products
    productos_con_autoridad = sum(
        1 for k in range(k_entregables)
        if (f := m.current(f"prod:{k}")) is not None and f["verified"])
    omisiones_con = k_entregables - productos_con_autoridad
    falsos_cierres_con = 0
    for k in range(k_entregables):
        ent = m.current(f"entregable:e{k}")
        if ent is not None and ent["value"].get("estado") == "satisfecho":
            for (r, g) in ent["value"].get("requiere", []):
                rf = m.current(r)
                if rf is None or not rf["verified"]:
                    falsos_cierres_con += 1

    # ── WITHOUT deontics (B1: free closure) ──
    m2 = _mk()
    for k in range(k_entregables):
        m2.write(f"prod:{k}", {"hecho": True}, fuente="calculadora", verified=True,
                 derived_from={})  # free closure: without lineage -> unverified (rule E1)
    productos_con_autoridad_b1 = sum(
        1 for k in range(k_entregables)
        if (f := m2.current(f"prod:{k}")) is not None and f["verified"])
    omisiones_sin = k_entregables - productos_con_autoridad_b1

    return {
        "con_deontica": {"omisiones": omisiones_con, "falsos_cierres": falsos_cierres_con},
        "sin_deontica": {"omisiones": omisiones_sin},
        "k": k_entregables,
    }


def run():
    print("=" * 78)
    print("BATERÍA DE FALSACIÓN EJECUTABLE: gobernanza vs retrieval")
    print("(especificación, no benchmark: HCMA cumple por construcción; el baseline la viola)")
    print("=" * 78)

    fallos = []

    # P1
    r1 = perturbacion_1()
    p1_hcma = r1["hcma"] == 0.0
    p1_base = r1["asociativo"] > 0.0
    print(f"\n[P1] Revisión de fuente → stale-use rate")
    print(f"  HCMA (colapso por linaje)   = {r1['hcma']:.3f}  → {'satisfecha' if p1_hcma else 'FALLO'}")
    print(f"  asociativo (sin linaje)     = {r1['asociativo']:.3f}  → {'violada' if p1_base else 'FALLO'}")
    if not (p1_hcma and p1_base):
        fallos.append("P1")

    # P2
    r2 = perturbacion_2()
    p2_hcma = r2["hcma"] == 0.0
    p2_base = r2["asociativo"] > 0.0
    print(f"\n[P2] Contaminación plausible → no-canónicos al entregable")
    print(f"  HCMA (procedencia > popularidad) = {r2['hcma']:.3f}  → {'satisfecha' if p2_hcma else 'FALLO'}")
    print(f"  asociativo (refuerzo hebbiano)   = {r2['asociativo']:.3f}  → {'violada' if p2_base else 'FALLO'}")
    if not (p2_hcma and p2_base):
        fallos.append("P2")

    # P3
    r3 = perturbacion_3()
    p3_hcma = (r3["con_deontica"]["omisiones"] == 0
               and r3["con_deontica"]["falsos_cierres"] == 0)
    p3_base = r3["sin_deontica"]["omisiones"] > 0
    k = r3["k"]
    print(f"\n[P3] Horizonte deóntico → omisiones / falsos cierres (k={k})")
    print(f"  CON deóntica: omisiones={r3['con_deontica']['omisiones']}, "
          f"falsos_cierres={r3['con_deontica']['falsos_cierres']}  → "
          f"{'satisfecha' if p3_hcma else 'FALLO'}")
    print(f"  SIN deóntica (B1, cierre libre): omisiones={r3['sin_deontica']['omisiones']}  → "
          f"{'violada' if p3_base else 'FALLO'}")
    if not (p3_hcma and p3_base):
        fallos.append("P3")

    # summary table (mirror of tab:expD from the paper)
    print("\n" + "-" * 78)
    print("RESUMEN (espejo de la Tabla 1 del workshop paper):")
    print("  P1  stale-use tras revisión de fuente        → HCMA satisfecha / baseline violada")
    print("  P2  no-canónico llega a entregable           → HCMA satisfecha / baseline violada")
    print("  P3  omisiones (de %d)                         → HCMA 0 / baseline %d" %
          (k, r3["sin_deontica"]["omisiones"]))
    print("  P3  falsos cierres                           → HCMA 0")
    print("=" * 78)

    if fallos:
        print(f"\n❌ BATERÍA ROJA: propiedades que no se cumplieron: {fallos}")
        return 1
    print("\n✅ BATERÍA VERDE: las 3 propiedades se cumplen (HCMA satisfecha, baseline violada).")
    return 0


if __name__ == "__main__":
    sys.exit(run())
