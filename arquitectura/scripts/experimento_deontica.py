#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""experimento_deontica.py — does the deontic layer reduce forgotten deliverables?

Hypothesis (paper insight): an agent that ONLY optimizes epistemic confidence suffers
Goodhart — it verifies indefinitely and forgets the goal. The deontic layer (deliverable:
pending + end-of-turn gate) corrects this.

Controlled and deterministic design (no LLM, to be reproducible), hardened after the
query `cierre_deontico_drift` (2026-11-10):

  - The METRIC measures PRODUCTS with authority at the end, read by an independent AUDITOR
    (only queries `current` on the memory), NOT the deontic bookkeeping. Using
    `entregable:*` to measure the success of the deontics is tautological.
  - A deliverable's requirement is an in-progress fact (`prod:<tid>`) that the real
    work must PRODUCE with lineage to an oracle (`cota:*`) to reach authority. Only
    then does the deontic gate (invariant A) allow closing it.
  - Two conditions:
      WITHOUT deontics (B1, free closure): the agent writes `prod:<tid>` as 'done' without
        lineage nor gate -> the epistemic rule E (calculadora without derived_from → unverified)
        leaves it WITHOUT authority -> the audit counts it as an omission.
      WITH deontics: the agent produces `prod:<tid>` with lineage to an oracle and closes by
        predicate; the gate guarantees 0 omissions and 0 false closures.
  - Metrics: (1) omissions = products without authority at the end; (2) false closures =
    `satisfecho` deliverables with some requirement without authority (must be 0).

HONEST LIMITATION (important for the paper): this experiment is SYNTHETIC and DETERMINISTIC
— the agent is hardcoded. It is NOT an evaluation of a real LLM agent making decisions.
It demonstrates the MECHANISM (why the end-of-turn gate is necessary), not that a real agent
measurably improves. To go from workshop to conference/TMLR it would be necessary to run the same
protocol with a real LLM agent (N tasks, completeness metrics and spurious verifications),
which is the next step.

Usage:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/experimento_deontica.py
"""

import os
import sys
import random

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import deontica  # noqa: E402


def _mk():
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_exp_")
    os.close(fd)
    return HCMAMemoriaSQLite(path)


def _auditar(m, N):
    """Independent auditor (does NOT import deontica.py): counts products with real authority.

    Returns (omissions, false_closures). Only queries `current` on the memory.
    """
    omisiones = 0
    for tid in range(N):
        f = m.current(f"prod:{tid}")
        if f is None or not f["verified"]:
            omisiones += 1
    # false closures: deliverables satisfied with a requirement without authority
    falsos = 0
    for tid in range(N):
        ent = m.current(f"entregable:entregable_{tid}")
        if ent is not None and ent["value"].get("estado") == "satisfecho":
            for (r, g) in ent["value"].get("requiere", []):
                rf = m.current(r)
                if rf is None or not rf["verified"]:
                    falsos += 1
    return omisiones, falsos


def _tarea_sin_deontica(m, tid):
    """B1 (free closure): without instantiating a deliverable, the agent writes 'done' without lineage.

    The epistemic rule E leaves `prod:<tid>` unverified (calculadora without derived_from)."""
    m.write(f"prod:{tid}", {"hecho": True}, fuente="calculadora", verified=True,
            derived_from={})


def _tarea_con_deontica(m, tid, v_orac):
    """WITH deontics: instantiates the deliverable and produces the product with lineage to an oracle."""
    m.write(f"prod:{tid}", {"hecho": False}, fuente="base", verified=False)
    deontica.registrar_entregable(m, f"entregable_{tid}", "producto", f"tarea {tid}",
                                  predicado_ref=f"pred_{tid}",
                                  requiere=[(f"prod:{tid}", "conforme")])
    # the real work produces the product and gives it lineage to an oracle -> authority
    m.write(f"prod:{tid}", {"hecho": True}, fuente="calculadora", verified=True,
            derived_from={f"cota:producto:norma": v_orac})
    deontica.cerrar_si_predicado(m, f"entregable_{tid}", lambda mem: (True, "producto entregado"))


def run():
    N = 100
    seeds = [0, 1, 2]

    print("=" * 68)
    print("EXPERIMENTO: la capa deóntica reduce entregables olvidados")
    print("=" * 68)

    for seed in seeds:
        random.seed(seed)

        # WITHOUT deontics (B1, free closure)
        m_sin = _mk()
        for tid in range(N):
            _tarea_sin_deontica(m_sin, tid)
        om_sin, fc_sin = _auditar(m_sin, N)

        # WITH deontics
        m_con = _mk()
        v_orac = m_con.write("cota:producto:norma", {"conforme": True},
                             fuente="tabla", verified=True)
        for tid in range(N):
            _tarea_con_deontica(m_con, tid, v_orac)
        om_con, fc_con = _auditar(m_con, N)

        print(f"\nseed={seed}:")
        print(f"  SIN deóntica (B1): omisiones {om_sin}/{N}  ({om_sin/N*100:.0f}%), "
              f"falsos_cierres {fc_sin}")
        print(f"  CON deóntica:     omisiones {om_con}/{N}  ({om_con/N*100:.0f}%), "
              f"falsos_cierres {fc_con}")

    print("\n" + "=" * 68)
    print("Conclusión (esperada):")
    print("  SIN deóntica (B1), el agente declara 'hecho' sin linaje -> la regla E lo deja")
    print("  sin autoridad -> la auditoría reporta omisiones (Goodhart sobre la métrica).")
    print("  CON deóntica, el gate fuerza producir el producto con linaje a oráculo ->")
    print("  0 omisiones y 0 falsos cierres. La diferencia es un GATE, no retrieval.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(run())
