#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Hardened skill router experiment — ambiguity sweep + multi-seed.

Isolates the effect of the RETRACTION of use versus similarity, sweeping how
ambiguous the context is (superficial overlap between the old and new skill).

What it measures (deterministic, no network):
  - For each ambiguity level `a` (0=disjoint … 1=identical) and for each seed:
      * accuracy of 3 policies after a drift,
      * cost (number of invocations to level 2 = similarity / encode).

Policies:
  - cascada            : retractable-use → (cost tie) → similarity → abstention.
  - solo_similitud     : always similarity (never uses the history).
  - uso_sin_retractar  : uses the history BUT without marking stale (retraction off),
                         to isolate how much the retraction itself contributes.

Hypothesis we falsify with the sweep:
  H1: as ambiguity rises, solo_similitud degrades towards chance (0.5) while
      cascada holds (because it uses the retractable history).
  H2: without retraction (uso_sin_retractar), the router stays anchored to the old skill and
      does NOT recover post-drift — retraction is what unblocks recovery.
"""
import os
import sys
import json
import random

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "..", "dashboard")
if DASH not in sys.path:
    sys.path.insert(0, DASH)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import router_skills as R  # noqa: E402
import skills as S  # noqa: E402
from experimento_router import _TFIDFIndex  # noqa: E402


def _build_desc(ambiguedad):
    """Builds the descriptions of two skills with controlled overlap,
    indexed by their semantic ids.

    `ambiguedad` ∈ [0,1] controls how many terms X and Y share. The higher the value,
    the harder it is to distinguish them by superficial similarity.
    """
    comunes = ["dimensionar", "conductor", "cable", "caida", "tension", "instalacion"]
    k = int(round(len(comunes) * ambiguedad))
    base_comun = comunes[:k]
    X = "dimensionar_cable_norma_antigua"
    Y = "dimensionar_cable_norma_vigente"
    desc_x = " ".join(base_comun + ["norma", "antigua", "version"])
    desc_y = " ".join(base_comun + ["norma", "vigente", "actual"])
    return {X: desc_x, Y: desc_y}


def _run(seed, ambiguedad, fase1_n=20, post_n=5, n_eval=40):
    """Runs a drift scenario with a given ambiguity level and a seed.
    Returns a dict of metrics."""
    random.seed(seed)
    desc = _build_desc(ambiguedad)
    # semantic ids (they share tokens with the context, as in real life)
    X = "dimensionar_cable_norma_antigua"
    Y = "dimensionar_cable_norma_vigente"
    assert set(desc.keys()) == {X, Y}

    m = HCMAMemoriaSQLite(":memory:")
    vx = S.registro(m, X, desc[X], fuente="ingeniero", verified=True, meta={"costo": 0})
    vy = S.registro(m, Y, desc[Y], fuente="ingeniero", verified=True, meta={"costo": 0})

    in_v1 = m.write("in_c", 10, unit="A", fuente="usuario", verified=True)
    res_x = m.write("res", 1, unit="u", fuente="calculadora", verified=True,
                    derived_from={"in_c": in_v1})
    for _ in range(fase1_n):
        S.registrar_uso(m, X, vx, [("res", res_x)], exito=True)

    # DRIFT
    in_v2 = m.write("in_c", 12, unit="A", fuente="usuario", verified=True)
    res_x2 = m.write("res", 1, unit="u", fuente="calculadora", verified=True,
                     derived_from={"in_c": in_v2})
    for _ in range(post_n):
        S.registrar_uso(m, Y, vy, [("res", res_x2)], exito=True)

    # similarity index (TF-IDF over the two descriptions)
    INDICE = _TFIDFIndex({X: desc[X], Y: desc[Y]})

    # evaluation context: at LOWER ambiguity, the context contains the distinctive
    # term ('vigente'/'actual') that similarity can use to hit; at HIGHER
    # ambiguity, the context is superficially neutral and similarity does not distinguish.
    if ambiguedad < 0.5:
        ctx_base = ("dimensionar el conductor del cable por caida de tension "
                    "para la instalacion segun la norma vigente")
    else:
        ctx_base = "dimensionar el conductor del cable por caida de tension para la instalacion"

    def _simul(idx, c, sid):
        return R._similitud(c, sid, idx)

    # ── cascade policy ──
    def _cascada(c):
        out = R.elegir_skill(m, c, indice=INDICE)
        # cost: 0 if resolved by use/cost, 1 if it touched similarity
        cost = 0 if out[0] == "usar" and out[2] in ("uso", "costo") else (1 if out[2] == "similitud" else 0)
        return out, cost

    # ── similarity only ──
    def _solo_sim(c):
        s = [(sid, _simul(INDICE, c, sid)) for sid in [X, Y]]
        mejor = max(s, key=lambda x: x[1])
        return ("usar", mejor[0], "similitud"), 1

    acc = {"cascada": 0, "solo_sim": 0, "uso_sin_retractar": 0}
    cost = {"cascada": 0, "solo_sim": 0, "uso_sin_retractar": 0}
    abst = {"cascada": 0}

    for i in range(n_eval):
        c = f"{ctx_base} tramo {i}"

        # cascade
        out, ct = _cascada(c)
        cost["cascada"] += ct
        if out[0] == "usar" and out[1] == Y:
            acc["cascada"] += 1
        elif out[0] == "abstener":
            abst["cascada"] += 1

        # similarity only
        out, ct = _solo_sim(c)
        cost["solo_sim"] += ct
        if out[0] == "usar" and out[1] == Y:
            acc["solo_sim"] += 1

        # use without retracting: choose by number of POST+STALE uses (total history, without invalidating)
        usos_totales = _usos_totales(m)
        mejor_uso = max(usos_totales, key=lambda k: usos_totales.get(k, 0)) \
            if usos_totales else None
        cost["uso_sin_retractar"] += 0
        if mejor_uso == Y:
            acc["uso_sin_retractar"] += 1

    res = {}
    for k in acc:
        res[f"acc_{k}"] = acc[k] / n_eval
    for k in cost:
        res[f"cost_{k}"] = cost[k] / n_eval
    res["abst_cascada"] = abst["cascada"] / n_eval
    m.close()
    return res


def _usos_totales(m):
    """Usage view ALSO counting the stale ones (total history, without retracting)."""
    out = {}
    for r in m.conn.execute("SELECT key, value FROM facts WHERE key LIKE ?",
                            ("skill_uso:%",)).fetchall():
        sid = r["key"][len("skill_uso:"):].rpartition(":")[0]
        try:
            out[sid] = out.get(sid, 0) + int(r["value"])
        except (TypeError, ValueError):
            pass
    return out


def main():
    AMBIGUEDADES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    SEEDS = [0, 1, 2, 3, 4]
    print(f"{'ambig':>6} | {'acc_cascada':>12} {'acc_solo_sim':>12} "
          f"{'acc_sin_retractar':>16} {'cost_cascada':>12} {'abst_cascada':>12}")
    filas = []
    for a in AMBIGUEDADES:
        agg = {"acc_cascada": [], "acc_solo_sim": [], "acc_uso_sin_retractar": [],
               "cost_cascada": [], "abst_cascada": []}
        for s in SEEDS:
            r = _run(s, a)
            agg["acc_cascada"].append(r["acc_cascada"])
            agg["acc_solo_sim"].append(r["acc_solo_sim"])
            agg["acc_uso_sin_retractar"].append(r["acc_uso_sin_retractar"])
            agg["cost_cascada"].append(r["cost_cascada"])
            agg["abst_cascada"].append(r["abst_cascada"])

        def _mean_sem(v):
            mean = sum(v) / len(v)
            var = sum((x - mean) ** 2 for x in v) / len(v)
            sem = (var ** 0.5) / (len(v) ** 0.5)
            return mean, sem

        m_cascada, _ = _mean_sem(agg["acc_cascada"])
        m_sim, _ = _mean_sem(agg["acc_solo_sim"])
        m_ret, _ = _mean_sem(agg["acc_uso_sin_retractar"])
        m_cost, _ = _mean_sem(agg["cost_cascada"])
        m_abst, _ = _mean_sem(agg["abst_cascada"])
        filas.append({
            "ambiguity": a,
            "acc_cascada": round(m_cascada, 3), "acc_cascada_sem": round(_mean_sem(agg["acc_cascada"])[1], 3),
            "acc_solo_sim": round(m_sim, 3),
            "acc_uso_sin_retractar": round(m_ret, 3),
            "cost_cascada": round(m_cost, 3),
            "abst_cascada": round(m_abst, 3),
        })
        print(f"{a:>6.1f} | {m_cascada:>12.3f} {m_sim:>12.3f} {m_ret:>16.3f} "
              f"{m_cost:>12.3f} {m_abst:>12.3f}")

    out = os.path.join(HERE, "router_sweep_resultados.json")
    json.dump(filas, open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n[salvado {out}]")


if __name__ == "__main__":
    main()
