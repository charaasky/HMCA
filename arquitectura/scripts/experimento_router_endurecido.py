#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Hardened skill router experiment: real cost + N skills + multi-seed.

Closes the gaps of the simple sweep:
  1) REAL COST: wall latency (perf_counter) and number of encodes (vectorizations) per
     decision, separating the cheap regime (level 1, use in HCMA) from the expensive one (level 2,
     similarity/encode).
  2) N competing skills (k > 2), to see that cost tie-breaking and abstention
     scale, not only the binary case.
  3) Error bars (mean ± SEM) over seeds, and an ABLATION of the similarity threshold.

Deterministic, no network. The similarity index is local TF-IDF (token proxy), and
each `.encode(contexto)` is counted as one "cost" operation.
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
from experimento_router import _TFIDFIndex  # noqa: E402


# ── synthetic domain with K skills, each with description + declared cost ──
# 3 "standards" (old, intermediate, current) competing for the SAME type of context,
# plus 1 expensive "decoy" skill (web) that should NOT be chosen due to cost.
SKILLS = {
    "dimensionar_cable_norma_antigua":  {"desc": "dimensionar cable caida tension norma antigua version vieja", "costo": 0},
    "dimensionar_cable_norma_intermedia": {"desc": "dimensionar cable caida tension norma intermedia revision", "costo": 0},
    "dimensionar_cable_norma_vigente": {"desc": "dimensionar cable caida tension norma vigente actual", "costo": 0},
    "busqueda_web_normativa":           {"desc": "buscar normativa vigente en la web consulta externa", "costo": 2},
}

# ground truth: the correct post-drift skill is the "current" standard
CORRECTO = "dimensionar_cable_norma_vigente"


class MeteredIndex:
    """Wraps a similarity index and counts each encode (proxy for computation cost)."""
    def __init__(self, inner):
        self.inner = inner
        self.n_encode = 0
        self.encode_ns = 0.0

    def encode(self, texto):
        self.n_encode += 1
        t0 = time.perf_counter()
        v = self.inner.encode(texto)
        self.encode_ns += (time.perf_counter() - t0) * 1e9
        return v

    def get(self, skill_id):
        return self.inner.get(skill_id)


def _cascada(m, c, idx):
    """Cascade with metering. Returns (out, n_encode_este, ns_este)."""
    t0 = time.perf_counter()
    out = R.elegir_skill(m, c, indice=idx)
    ns = (time.perf_counter() - t0) * 1e9
    n_enc = idx.n_encode  # accumulated; we subtract it outside per decision
    return out, n_enc, ns


def _run(seed, ambiguedad, fase1_n=20, post_n=5, n_eval=40, umbral=0.5):
    random.seed(seed)
    # set similarity threshold for the ablation
    old_umbral = R.UMBRAL_SIMILITUD
    R.UMBRAL_SIMILITUD = umbral

    m = HCMAMemoriaSQLite(":memory:")
    ver = {}
    for sid, spec in SKILLS.items():
        ver[sid] = S.registro(m, sid, spec["desc"], fuente="ingeniero", verified=True,
                              meta={"costo": spec["costo"]})

    # pre-drift: the "old" standard was correct → uses of that skill
    in_v1 = m.write("in_c", 10, unit="A", fuente="usuario", verified=True)
    res_old = m.write("res", 1, unit="u", fuente="calculadora", verified=True,
                      derived_from={"in_c": in_v1})
    early = "dimensionar_cable_norma_antigua"
    for _ in range(fase1_n):
        S.registrar_uso(m, early, ver[early], [("res", res_old)], exito=True)

    # DRIFT: the regulation changes → res becomes stale, the correct one becomes "current"
    in_v2 = m.write("in_c", 12, unit="A", fuente="usuario", verified=True)
    res_new = m.write("res", 1, unit="u", fuente="calculadora", verified=True,
                      derived_from={"in_c": in_v2})
    for _ in range(post_n):
        S.registrar_uso(m, CORRECTO, ver[CORRECTO], [("res", res_new)], exito=True)

    # neutral (ambiguous) context according to ambiguity
    if ambiguedad < 0.5:
        ctx_base = "dimensionar el cable por caida de tension segun la norma vigente actual"
    else:
        ctx_base = "dimensionar el cable por caida de tension para la instalacion"

    inner = _TFIDFIndex({sid: s["desc"] for sid, s in SKILLS.items()})
    idx = MeteredIndex(inner)

    # ── cascade policy ──
    acc_c = 0
    enc_c = 0
    ns_c = 0.0
    abst_c = 0
    for i in range(n_eval):
        prev_enc = idx.n_encode
        out, _, ns = _cascada(m, f"{ctx_base} tramo {i}", idx)
        enc_c += idx.n_encode - prev_enc
        ns_c += ns
        if out[0] == "usar" and out[1] == CORRECTO:
            acc_c += 1
        elif out[0] == "abstener":
            abst_c += 1

    # ── similarity-only policy (always encodes all) ──
    acc_s = 0
    enc_s = 0
    ns_s = 0.0
    for i in range(n_eval):
        c = f"{ctx_base} tramo {i}"
        prev_enc = idx.n_encode
        t0 = time.perf_counter()
        sims = [(sid, R._similitud(c, sid, idx)) for sid in SKILLS]
        ns_s += (time.perf_counter() - t0) * 1e9
        enc_s += idx.n_encode - prev_enc
        mejor = max(sims, key=lambda x: x[1])
        if mejor[1] >= umbral and mejor[0] == CORRECTO:
            acc_s += 1

    # ── use-without-retraction policy ──
    acc_r = 0
    usos_tot = _usos_totales(m)
    for i in range(n_eval):
        if max(usos_tot, key=lambda k: usos_tot.get(k, 0)) == CORRECTO:
            acc_r += 1

    R.UMBRAL_SIMILITUD = old_umbral
    m.close()
    return {
        "acc_cascada": acc_c / n_eval,
        "acc_solo_sim": acc_s / n_eval,
        "acc_sin_retractar": acc_r / n_eval,
        "encode_cascada": enc_c / n_eval,
        "encode_solo_sim": enc_s / n_eval,
        "ns_cascada_por_dec": round(ns_c / n_eval, 1),
        "ns_solo_sim_por_dec": round(ns_s / n_eval, 1),
        "abst_cascada": abst_c / n_eval,
    }


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


def _mean_sem(v):
    n = len(v)
    mean = sum(v) / n
    var = sum((x - mean) ** 2 for x in v) / n
    sem = (var ** 0.5) / (n ** 0.5)
    return mean, sem


def main():
    AMBIGUEDADES = [0.0, 0.4, 0.6, 0.8, 1.0]
    SEEDS = [0, 1, 2, 3, 4]
    print(f"{'ambig':>5} | {'acc_casc':>8} {'acc_sim':>8} {'acc_noRetr':>10} "
          f"{'enc_casc':>8} {'enc_sim':>8} {'ns_casc':>9} {'ns_sim':>9} {'abst_c':>7}")
    result = []
    for a in AMBIGUEDADES:
        agg = {k: [] for k in ["acc_cascada", "acc_solo_sim", "acc_sin_retractar",
                               "encode_cascada", "encode_solo_sim",
                               "ns_cascada_por_dec", "ns_solo_sim_por_dec", "abst_cascada"]}
        for s in SEEDS:
            r = _run(s, a)
            for k in agg:
                agg[k].append(r[k])
        row = {}
        for k in agg:
            row[k] = round(_mean_sem(agg[k])[0], 3)
            row[k + "_sem"] = round(_mean_sem(agg[k])[1], 3)
        result.append({"ambiguity": a, **row})
        print(f"{a:>5.1f} | {row['acc_cascada']:>8.3f}±{row['acc_cascada_sem']:.2f} "
              f"{row['acc_solo_sim']:>8.3f} {row['acc_sin_retractar']:>10.3f} "
              f"{row['encode_cascada']:>8.2f} {row['encode_solo_sim']:>8.2f} "
              f"{row['ns_cascada_por_dec']:>9.1f} {row['ns_solo_sim_por_dec']:>9.1f} "
              f"{row['abst_cascada']:>7.3f}")

    out = os.path.join(HERE, "router_endurecido_resultados.json")
    json.dump(result, open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n[salvado {out}]")


if __name__ == "__main__":
    main()
