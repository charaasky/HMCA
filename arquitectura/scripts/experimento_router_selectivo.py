#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Experiment D.2 — SELECTIVE invalidation (the one that truly motivates lineage).

We falsify the only defensible contribution: version lineage selectively retracts
the descendant uses of ONE specific input, while a blind temporal decay
(window/decay without lineage) throws everything or nothing.

Scenario (deterministic):
  - The 'old' skill has uses justified by TWO results: res_A (derived from input_A)
    and res_B (derived from input_B). Both valid pre-drift.
  - DRIFT: ONLY input_A changes (it is corrected). → res_A becomes stale, but res_B REMAINS VALID.
  - The 'current' skill (post-drift correct for what depended on A) has uses of res_A2.

What it measures:
  - LINEAGE: retracts only the uses of 'old' that depended on res_A@v1 (those of res_B@v1
    remain intact). The current-use count of 'old' drops selectively.
  - Blind DECAY (baseline): without lineage, it cannot distinguish; it either retires all uses of
    'old' (throws away the valid evidence from B) or retires none (remains contaminated by A).

Metrics:
  - retraction precision: did it retract exactly the uses of A (neither more nor less)?
  - false stale: valid use (of B) discarded unduly.
  - false current: invalid use (of A) that kept counting.
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
import router_skills as R  # noqa: E402


SKILL_VIEJO = "skill_viejo"
SKILL_VIGENTE = "skill_vigente"


def _usos_no_stale(m):
    out = {}
    for r in m.conn.execute("SELECT key, value FROM facts WHERE key LIKE ? AND stale=0",
                            ("skill_uso:%",)).fetchall():
        sid = r["key"][len("skill_uso:"):].rpartition(":")[0]
        try:
            out[sid] = out.get(sid, 0) + int(r["value"])
        except (TypeError, ValueError):
            pass
    return out


def _usos_totales(m):
    out = {}
    for r in m.conn.execute("SELECT key, value FROM facts WHERE key LIKE ?",
                            ("skill_uso:%",)).fetchall():
        sid = r["key"][len("skill_uso:"):].rpartition(":")[0]
        try:
            out[sid] = out.get(sid, 0) + int(r["value"])
        except (TypeError, ValueError):
            pass
    return out


def construir(n_a=6, n_b=6):
    """Builds the memory with uses of the old skill justified by res_A and res_B.

    Returns (m, versions, number of uses of A, number of uses of B)."""
    m = HCMAMemoriaSQLite(":memory:")
    v_viejo = S.registro(m, SKILL_VIEJO, "norma antigua", fuente="ingeniero",
                         verified=True, meta={"costo": 0})
    v_vigente = S.registro(m, SKILL_VIGENTE, "norma vigente", fuente="ingeniero",
                           verified=True, meta={"costo": 0})

    # TWO independent inputs
    in_A = m.write("input_A", 1, unit="u", fuente="usuario", verified=True)
    in_B = m.write("input_B", 1, unit="u", fuente="usuario", verified=True)
    # TWO derived results, each from its input
    res_A = m.write("res_A", 1, unit="u", fuente="calculadora", verified=True,
                    derived_from={"input_A": in_A})
    res_B = m.write("res_B", 1, unit="u", fuente="calculadora", verified=True,
                    derived_from={"input_B": in_B})

    # uses of the old skill: n_a justified by res_A, n_b by res_B
    for _ in range(n_a):
        S.registrar_uso(m, SKILL_VIEJO, v_viejo, [("res_A", res_A)], exito=True)
    for _ in range(n_b):
        S.registrar_uso(m, SKILL_VIEJO, v_viejo, [("res_B", res_B)], exito=True)

    return m, {"viejo": v_viejo, "vigente": v_vigente}, {"res_A": res_A, "res_B": res_B,
                                                         "in_A": in_A, "in_B": in_B}, n_a, n_b


def main():
    n_a, n_b = 6, 6
    m, ver, refs, n_a, n_b = construir(n_a, n_b)

    # PRE-drift state
    pre = _usos_no_stale(m)
    print(f"PRE-drift : usos vigentes del skill viejo = {pre.get(SKILL_VIEJO, 0)} "
          f"(esperado {n_a + n_b}: {n_a} de res_A + {n_b} de res_B)")

    # ── SELECTIVE DRIFT: ONLY input_A changes ──
    # input_A is corrected → version collapse marks stale res_A@v1 (descendant of input_A@v1),
    # and transitively the uses of the old skill that depended on res_A@v1.
    # res_B@v1 (which depends on input_B@v1, which did NOT change) MUST remain intact.
    in_A2 = m.write("input_A", 2, unit="u", fuente="usuario", verified=True)

    post = _usos_no_stale(m)
    tot = _usos_totales(m)
    print(f"POST-drift: usos vigentes del skill viejo = {post.get(SKILL_VIEJO, 0)} "
          f"(esperado {n_b}: solo los de res_B sobreviven)")
    print(f"           usos totales (incl. stale) = {tot.get(SKILL_VIEJO, 0)}")

    # ── metric: correct selective retraction? ──
    # how many uses of the old skill became stale (retracted) = exactly n_a
    neto = tot.get(SKILL_VIEJO, 0) - post.get(SKILL_VIEJO, 0)
    violacion_stale = n_a - neto  # >0: did not retract something invalid; <0: retracted too much
    print(f"\nUso retractados: {neto} (esperado exactamente {n_a})")
    print(f"falsos vigentes (inválido que siguió contando): "
          f"{neto - n_a if neto > n_a else 0}")
    print(f"falsos stale (válido descartado): {n_b - post.get(SKILL_VIEJO, 0)}")

    # ── comparison with a blind DECAY ──
    # A blind temporal decay (recency window) does not know about input_A vs input_B:
    # it either throws away all the 'old' uses (including the valid ones from B) or throws none.
    # We show that THROWING AWAY ALL discards n_b valid uses.
    print(f"\n[baseline decay ciego, si retira TODOS los usos del skill viejo]")
    print(f"  - descarta {n_a + n_b} usos (tira evidencia válida de res_B = {n_b})")
    print(f"[baseline decay ciego, si NO retira ninguno]")
    print(f"  - queda contaminado por {n_a} usos inválidos de res_A")

    # ── save ──
    out = os.path.join(HERE, "router_selectivo_resultados.json")
    json.dump({
        "n_a": n_a, "n_b": n_b,
        "usos_pre": pre.get(SKILL_VIEJO, 0),
        "usos_post": post.get(SKILL_VIEJO, 0),
        "retractados": neto,
        "falsos_stale": n_b - post.get(SKILL_VIEJO, 0),
        "falsos_vigentes": max(0, neto - n_a),
    }, open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n[salvado {out}]")
    m.close()


if __name__ == "__main__":
    main()
