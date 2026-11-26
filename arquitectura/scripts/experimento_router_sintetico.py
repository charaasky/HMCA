#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Definitive router experiment: controllable synthetic similarity.

Separates the QUALITY of the similarity signal (independent variable, controlled by
`ambiguedad`) from the QUALITY OF THE ROUTER (dependent variable, what we evaluate).

Instead of a real vectorizer (TF-IDF/embeddings), the 'similarity' is a synthetic signal:
  score(c, skill) = 1  if skill == skill_sugerido(c)
                    controlled noise otherwise

where `ambiguedad` ∈ [0,1] defines the probability that the signal points to SUGERIDO_ERRONEO
(the old skill) instead of the correct one. At ambiguedad=0 similarity always hits; at
ambiguedad=1 it is pure chance (0.5).

Drift scenario (same as before):
  - pre-drift: the correct skill is the 'old' one (it accumulates uses).
  - post-drift: the regulation changes → the correct one becomes 'current', and the old's uses
    become stale (real retraction by version collapse).
  - synthetic similarity WILL keep pointing to the 'old' one with probability `ambiguedad`
    (it does not 'know' about the drift), while the retractable history does reflect the change.

Metrics per policy:
  - accuracy (mean ± SEM over seeds)
  - computation cost (number of calls to the similarity signal per decision)
  - abstention (cascade only) and precision conditional on not abstaining in cascade.

Policies:
  - cascada          : retractable-use → similarity → abstention.
  - solo_similitud   : always trusts similarity (with threshold).
  - uso_sin_retractar: history WITHOUT marking stale (retraction off).

Hypothesis:
  H1: solo_similitud degrades from 1.0 (ambig=0) → 0.5 (ambig=1).   (noisy signal)
  H2: cascada stays ≈1.0 over the whole range.                  (retractable history)
  H3: without retraction, the router stays anchored to the old skill (≈0). (retraction matters)
  H4: the cascade touches similarity (high cost) MUCH less than solo_similitud.
"""
import os
import sys
import json
import random
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "..", "dashboard")
if DASH not in sys.path:
    sys.path.insert(0, DASH)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import router_skills as R  # noqa: E402
import skills as S  # noqa: E402


SKILL_VIEJO = "dimensionar_cable_norma_antigua"
SKILL_VIGENTE = "dimensionar_cable_norma_vigente"
IDS = [SKILL_VIEJO, SKILL_VIGENTE]
CORRECTO = SKILL_VIGENTE  # post-drift


class SyntheticSimilitud:
    """Controllable similarity signal: with probability `ambiguedad` it suggests the OLD
    skill (wrong post-drift); with 1-ambiguedad it suggests the CURRENT one (correct).
    Exposes `.encode` (counts calls) and `.get` (compatibility with _similitud)."""
    def __init__(self, ambiguedad, seed):
        self.ambiguedad = ambiguedad
        self.seed = seed
        self.rng = random.Random(seed)
        self.n_encode = 0

    def sugerido(self, contexto):
        # the context hash fixes determinism per case, independent of the global rng
        h = abs(hash(contexto)) % 1000 / 1000.0
        if h < self.ambiguedad:
            return SKILL_VIEJO      # the signal 'remembers' the old skill (wrong)
        return SKILL_VIGENTE        # the signal hits

    def encode(self, contexto):
        self.n_encode += 1
        # one-hot vector: similarity 1 to the suggested, 0 to the other
        sug = self.sugerido(contexto)
        return {sid: (1.0 if sid == sug else 0.0) for sid in IDS}

    def get(self, skill_id):
        # not used directly; _similitud skips it if it finds .encode/.get
        return None


class _SimWrapper:
    """Adapts SyntheticSimilitud to the interface router_skills._similitud expects:
    _similitud calls indice.encode(contexto) and expects a vector, then compares with
    indice.get(skill_id). To keep control, we instead redefine the similarity
    here directly and use our own thin router."""
    pass


def _similitud_sintetica(contexto, skill_id, idx):
    """Synthetic similarity [0,1]: 1 if skill_id is the one suggested by the signal, 0 if not."""
    sug = idx.sugerido(contexto)
    return 1.0 if skill_id == sug else 0.0


def _elegir_cascada(m, contexto, idx):
    """Cascade reimplemented in a THIN way to use synthetic similarity,
    without depending on router_skills' real vectorizer. Keeps the SAME logic:
    use → (cost tie) → similarity → abstention."""
    usos = _usos_no_stale(m)
    # skills that match the context (by semantic id: both contain 'cable'/'norma')
    match = [sid for sid in IDS if _overlap(contexto, sid)]

    if not match:
        return ("crear", contexto, "no existe")

    con_uso = [sid for sid in match if usos.get(sid, 0) > 0]
    if len(con_uso) == 1:
        return ("usar", con_uso[0], "uso")
    if len(con_uso) > 1:
        # tie-break by cost (both cost 0 → the first by stable order); in practice
        # it would be resolved by the one with more uses instead of cost if cost ties:
        mejor = max(con_uso, key=lambda sid: usos.get(sid, 0))
        return ("usar", mejor, "costo")

    # cold start: use synthetic similarity
    idx.n_encode += 1
    sug = idx.sugerido(contexto)
    if _similitud_sintetica(contexto, sug, idx) >= R.UMBRAL_SIMILITUD:
        return ("usar", sug, "similitud")
    return ("abstener", "sin señal")


def _overlap(contexto, skill_id):
    t = set(contexto.lower().split())
    s = set(skill_id.replace("_", " ").split())
    return bool(t & s)


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


def _run(seed, ambiguedad, fase1_n=20, post_n=5, n_eval=200):
    # memory
    m = HCMAMemoriaSQLite(":memory:")
    vx = S.registro(m, SKILL_VIEJO, "norma antigua", fuente="ingeniero", verified=True,
                    meta={"costo": 0})
    vy = S.registro(m, SKILL_VIGENTE, "norma vigente", fuente="ingeniero", verified=True,
                    meta={"costo": 0})

    in_v1 = m.write("in_c", 10, unit="A", fuente="usuario", verified=True)
    res_old = m.write("res", 1, unit="u", fuente="calculadora", verified=True,
                      derived_from={"in_c": in_v1})
    for _ in range(fase1_n):
        S.registrar_uso(m, SKILL_VIEJO, vx, [("res", res_old)], exito=True)

    # drift
    in_v2 = m.write("in_c", 12, unit="A", fuente="usuario", verified=True)
    res_new = m.write("res", 1, unit="u", fuente="calculadora", verified=True,
                      derived_from={"in_c": in_v2})
    for _ in range(post_n):
        S.registrar_uso(m, SKILL_VIGENTE, vy, [("res", res_new)], exito=True)

    idx = SyntheticSimilitud(ambiguedad, seed)
    ctx = "dimensionar el cable del circuito"

    # cascade
    acc_c = 0
    enc_c = 0
    abst_c = 0
    n_resp_c = 0
    wall_c = 0.0
    for i in range(n_eval):
        prev = idx.n_encode
        t0 = time.perf_counter()
        out = _elegir_cascada(m, f"{ctx} {i}", idx)
        wall_c += (time.perf_counter() - t0) * 1e6  # µs per decision
        enc_c += idx.n_encode - prev
        if out[0] == "usar":
            n_resp_c += 1
            if out[1] == CORRECTO:
                acc_c += 1
        elif out[0] == "abstener":
            abst_c += 1

    # similarity only (with threshold)
    acc_s = 0
    enc_s = 0
    wall_s = 0.0
    for i in range(n_eval):
        idx.n_encode += 1
        t0 = time.perf_counter()
        sug = idx.sugerido(f"{ctx} {i}")
        if _similitud_sintetica(f"{ctx} {i}", sug, idx) >= R.UMBRAL_SIMILITUD and sug == CORRECTO:
            acc_s += 1
        wall_s += (time.perf_counter() - t0) * 1e6  # µs per decision
    enc_s = idx.n_encode  # accumulated

    # without retracting
    acc_r = 0
    ut = _usos_totales(m)
    mejor = max(ut, key=lambda k: ut.get(k, 0))
    acc_r = 1.0 if mejor == CORRECTO else 0.0

    m.close()
    return {
        "acc_cascada": acc_c / n_eval,
        "acc_solo_sim": acc_s / n_eval,
        "acc_sin_retractar": acc_r,
        "encode_cascada": enc_c / n_eval,
        "encode_solo_sim": enc_s / n_eval,
        "abst_cascada": abst_c / n_eval,
        "prec_cascada_no_abs": acc_c / n_resp_c if n_resp_c else None,
        "us_cascada_por_dec": round(wall_c / n_eval, 2),
        "us_solo_sim_por_dec": round(wall_s / n_eval, 2),
    }


def _mean_sem(v):
    n = len(v)
    mean = sum(v) / n
    var = sum((x - mean) ** 2 for x in v) / n
    return mean, (var ** 0.5) / (n ** 0.5)


def main():
    AMB = [0.0, 0.25, 0.5, 0.75, 1.0]
    SEEDS = [0, 1, 2, 3, 4]
    print(f"{'ambig':>5} | {'cascada':>8} {'similitud':>10} {'no-retract':>11} "
          f"{'enc_casc':>8} {'abst_casc':>9} {'prec_noAbs':>10} "
          f"{'µs_casc':>8} {'µs_sim':>8}")
    result = []
    for a in AMB:
        agg = {"acc_cascada": [], "acc_solo_sim": [], "acc_sin_retractar": [],
               "encode_cascada": [], "abst_cascada": [], "prec_cascada_no_abs": [],
               "us_cascada_por_dec": [], "us_solo_sim_por_dec": []}
        for s in SEEDS:
            r = _run(s, a)
            for k in agg:
                agg[k].append(r[k])
        row = {k: round(_mean_sem(agg[k])[0], 3) for k in agg}
        row_sem = {k: round(_mean_sem(agg[k])[1], 3) for k in agg}
        result.append({"ambiguity": a, **row, "sem": row_sem})
        pna = f"{row['prec_cascada_no_abs']:.3f}" if row['prec_cascada_no_abs'] is not None else "—"
        print(f"{a:>5.2f} | {row['acc_cascada']:>8.3f} {row['acc_solo_sim']:>10.3f} "
              f"{row['acc_sin_retractar']:>11.3f} {row['encode_cascada']:>8.3f} "
              f"{row['abst_cascada']:>9.3f} {pna:>10} "
              f"{row['us_cascada_por_dec']:>8.2f} {row['us_solo_sim_por_dec']:>8.2f}")

    out = os.path.join(HERE, "router_sintetico_resultados.json")
    json.dump(result, open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n[salvado {out}]")


if __name__ == "__main__":
    main()
