#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""D.3 — imperfect retraction sweep (recall ρ ∈ [0,1]).

Converts the binary result of D.2 into a CURVE: how does routing accuracy degrade
when retraction does NOT cover all the invalid uses?

CORRECT DESIGN (corrects the D.2 metric, which was of identity, not of weight):
For the recall ρ to change WHO wins, the old skill must be on top by WEIGHT and the
retraction must bring it below the current one. Scenario:

  pre-drift : old = n_a (invalid, from res_A) + n_b (valid, from res_B) uses; current = 0.
  post-drift: current gains n_c uses (from res_C). res_A becomes invalid.
  retraction with recall ρ: of the n_a invalid uses, only ρ·n_a are marked stale.

  nonStale_uses(old)   = (1-ρ)·n_a + n_b
  nonStale_uses(current) = n_c
  The router chooses the one with more non-stale uses; hits if current > old.

Breakpoint: (1-ρ)·n_a + n_b ≤ n_c  ⟺  ρ ≥ 1 - (n_c - n_b)/n_a.

Hypothesis:
  - at ρ=1 (perfect) → accuracy 1.0  (current wins).
  - at ρ=0 (nothing)    → accuracy 0.0  (old stays on top, blind router).
  - the accuracy vs ρ curve has a break at ρ*; below ρ* the method fails.

It is a first-order analysis (weight = count of non-stale uses). It does not model delay nor
similarity: it is the degradation by LINEAGE COVERAGE, which is the critical assumption.
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


def simular(n_a, n_b, n_c, rho):
    """First-order model: accuracy of the router 'chooses the one with more non-stale uses'."""
    usos_viejo = (1 - rho) * n_a + n_b
    usos_vigente = n_c
    if usos_vigente > usos_viejo:
        return 1.0
    if abs(usos_vigente - usos_viejo) < 1e-9:
        return 0.5  # tie → chance
    return 0.0


def main():
    # imbalance: invalid (n_a) dominate; valid of old (n_b) few; current (n_c) medium
    n_a, n_b, n_c = 18, 2, 10
    rho_star = 1 - (n_c - n_b) / n_a
    print(f"Escenario: viejo pre-drift = {n_a} inválidos + {n_b} válidos = {n_a + n_b}; "
          f"vigente post-drift = {n_c}")
    print(f"Punto de quiebre analítico ρ* = {rho_star:.3f}\n")

    RHOS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    print(f"{'ρ':>5} | {'usos viejo (noStale)':>20} | {'usos vigente':>13} | {'acierto':>7}")
    curva = []
    for rho in RHOS:
        uv = (1 - rho) * n_a + n_b
        uc = n_c
        acc = simular(n_a, n_b, n_c, rho)
        curva.append({"rho": rho, "usos_viejo": round(uv, 2), "usos_vigente": uc,
                      "acierto": acc})
        print(f"{rho:>5.2f} | {uv:>20.1f} | {uc:>13} | {acc:>7.2f}")

    out = os.path.join(HERE, "router_retraccion_imperfecta_resultados.json")
    json.dump({"n_a": n_a, "n_b": n_b, "n_c": n_c, "rho_star": rho_star,
               "curva": curva}, open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n[salvado {out}]")


def _verificar_con_memoria_real():
    """Sanity-check: the analytical breakpoint coincides with the real behavior of
    HCMA when we retract ALL (ρ=1) vs NOTHING (ρ=0)."""
    m = HCMAMemoriaSQLite(":memory:")
    vv = S.registro(m, SKILL_VIEJO, "norma antigua", fuente="ingeniero", verified=True,
                    meta={"costo": 0})
    vg = S.registro(m, SKILL_VIGENTE, "norma vigente", fuente="ingeniero", verified=True,
                    meta={"costo": 0})
    in_A = m.write("input_A", 1, unit="u", fuente="usuario", verified=True)
    in_B = m.write("input_B", 1, unit="u", fuente="usuario", verified=True)
    in_C = m.write("input_C", 1, unit="u", fuente="usuario", verified=True)
    res_A = m.write("res_A", 1, unit="u", fuente="calculadora", verified=True,
                    derived_from={"input_A": in_A})
    res_B = m.write("res_B", 1, unit="u", fuente="calculadora", verified=True,
                    derived_from={"input_B": in_B})
    res_C = m.write("res_C", 1, unit="u", fuente="calculadora", verified=True,
                    derived_from={"input_C": in_C})

    for _ in range(18):
        S.registrar_uso(m, SKILL_VIEJO, vv, [("res_A", res_A)], exito=True)
    for _ in range(2):
        S.registrar_uso(m, SKILL_VIEJO, vv, [("res_B", res_B)], exito=True)
    for _ in range(10):
        S.registrar_uso(m, SKILL_VIGENTE, vg, [("res_C", res_C)], exito=True)

    pre = _usos_no_stale(m)
    # drift (ρ=1 in the real memory, because the collapse retracts everything from res_A)
    m.write("input_A", 2, unit="u", fuente="usuario", verified=True)
    post = _usos_no_stale(m)
    print(f"\n[memoria real] pre-drift : {pre}")
    print(f"[memoria real] post-drift (ρ=1): {post}")
    m.close()


if __name__ == "__main__":
    main()
    _verificar_con_memoria_real()
