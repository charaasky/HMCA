#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""D.1 — decay/window baseline against lineage-based retraction.

The counter-test that can REFUTE the contribution: can a blind temporal forgetting mechanism
(sliding window / exponential decay) recreate the SELECTIVE retraction that
lineage-by-version achieves? If it can, lineage adds nothing over a timestamp; if it
cannot, selectivity is the contribution.

Scenario (THE SAME as D.2, in `experimento_router_selectivo.py`):
  - Old skill with 6 uses of res_A (derived from input_A) and 6 uses of res_B (derived from B).
  - Selective DRIFT: ONLY input_A changes. → res_A@v1 becomes stale; res_B@v1 remains valid.
  - In lineage, each use carries `derived_from = {res_X: version}` + `t` (implicit timestamp
    in the write order). In decay, each use carries ONLY `t`.

Policies compared on the SAME history (none sees the future; all see timestamps):
  1. lineage (version collapse) : retracts the uses whose result became stale.
  2. sliding window N      : counts only the last N uses (forgetting by age).
  3. exponential decay γ : each use weighs γ^antiguedad; discarded if it weighs < threshold.

Metrics (the ones that separate "forgetting" from "selective forgetting"):
  - false current : INVALIDATED uses (of res_A) that the policy left counting.
  - false stale    : VALID uses (of res_B) that the policy discarded.
  - total error     : false_current + false_stale   (optimal lineage = 0).

Hypothesis:
  - Lineage gives error 0 (retracts exactly the 6 from A).
  - The window/decay, being blind to causality, CANNOT give 0:
      * if it forgets little → lets invalid uses of A through (false current > 0);
      * if it forgets a lot → discards valid uses of B (false stale > 0).
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


def _usos_no_stale(m, sid):
    out = 0
    for r in m.conn.execute("SELECT value FROM facts WHERE key LIKE ? AND stale=0",
                            (f"skill_uso:{sid}:%",)).fetchall():
        try:
            out += int(r["value"])
        except (TypeError, ValueError):
            pass
    return out


def _historia_tipada(m, sid):
    """Returns the usage history of the old skill in WRITE order, each with:
    (version, resultado_key, version_del_resultado, stale)."""
    rows = m.conn.execute(
        "SELECT version, meta, derived_from, stale FROM facts WHERE key LIKE ? "
        "ORDER BY version", (f"skill_uso:{sid}:%",)).fetchall()
    out = []
    for r in rows:
        df = json.loads(r["derived_from"])
        # the result that justifies this use is the only dep that is NOT 'skill:...'
        res_key = next((k for k in df if not k.startswith("skill:")), None)
        res_ver = df.get(res_key) if res_key else None
        out.append({"version": r["version"], "res_key": res_key,
                    "res_ver": res_ver, "stale": bool(r["stale"])})
    return out


def construir(n_a=6, n_b=6, intercalado=True):
    """Builds the memory with uses: n_a of res_A and n_b of res_B, in controlled order.

    `intercalado=True` alternates A and B so that the window/decay CANNOT separate them by
    simple temporal position (it is the hard case for blind forgetting)."""
    m = HCMAMemoriaSQLite(":memory:")
    v_viejo = S.registro(m, SKILL_VIEJO, "norma antigua", fuente="ingeniero",
                         verified=True, meta={"costo": 0})
    in_A = m.write("input_A", 1, unit="u", fuente="usuario", verified=True)
    in_B = m.write("input_B", 1, unit="u", fuente="usuario", verified=True)
    res_A = m.write("res_A", 1, unit="u", fuente="calculadora", verified=True,
                    derived_from={"input_A": in_A})
    res_B = m.write("res_B", 1, unit="u", fuente="calculadora", verified=True,
                    derived_from={"input_B": in_B})

    if intercalado:
        # A B A B ... so that blind forgetting cannot separate by age
        for i in range(max(n_a, n_b)):
            if i < n_a:
                S.registrar_uso(m, SKILL_VIEJO, v_viejo, [("res_A", res_A)], exito=True)
            if i < n_b:
                S.registrar_uso(m, SKILL_VIEJO, v_viejo, [("res_B", res_B)], exito=True)
    else:
        for _ in range(n_a):
            S.registrar_uso(m, SKILL_VIEJO, v_viejo, [("res_A", res_A)], exito=True)
        for _ in range(n_b):
            S.registrar_uso(m, SKILL_VIEJO, v_viejo, [("res_B", res_B)], exito=True)

    return m, in_A, in_B, res_A, res_B, v_viejo


def _eval_linaje(m):
    """Applies the drift and counts the lineage result (real selective retraction)."""
    # the drift is controlled outside; here we only count post-drift
    hist = _historia_tipada(m, SKILL_VIEJO)
    falsos_vig = sum(1 for h in hist if h["res_key"] == "res_A" and not h["stale"])
    falsos_stale = sum(1 for h in hist if h["res_key"] == "res_B" and h["stale"])
    return falsos_vig, falsos_stale


def _eval_ventana(hist, n_ventana):
    """Blind forgetting by window: only the last n_ventana uses count (the rest, 'stale').
    Applies the real LABELS (res_A invalid, res_B valid) to measure error."""
    n_tot = len(hist)
    # the window retires the first n_tot - n_ventana uses (by age)
    retirados = hist[: max(0, n_tot - n_ventana)]
    # error = how many res_A were left NOT retired (false current) + how many res_B were retired
    falsos_vig = sum(1 for h in hist[n_tot - n_ventana:] if h["res_key"] == "res_A")
    falsos_stale = sum(1 for h in retirados if h["res_key"] == "res_B")
    return falsos_vig, falsos_stale


def _eval_decay(hist, gamma, umbral=0.05):
    """Blind forgetting by exponential decay: weight = gamma^antiguedad (0 = oldest).
    Discarded if weight < threshold. Measures error the same as above."""
    n = len(hist)
    falsos_vig = 0
    falsos_stale = 0
    for i, h in enumerate(hist):
        antiguedad = n - 1 - i          # the last use has age 0
        peso = gamma ** antiguedad
        retirado = peso < umbral       # the decay forgets it
        if h["res_key"] == "res_A" and not retirado:
            falsos_vig += 1             # invalid that keeps counting
        if h["res_key"] == "res_B" and retirado:
            falsos_stale += 1           # valid that was discarded
    return falsos_vig, falsos_stale


def main():
    n_a, n_b = 6, 6
    # ── lineage ──
    m, in_A, in_B, res_A, res_B, v = construir(n_a, n_b)
    in_A2 = m.write("input_A", 2, unit="u", fuente="usuario", verified=True)  # selective DRIFT
    fv_l, fs_l = _eval_linaje(m)
    print("=== LINaje (version collapse) tras drift selectivo ===")
    print(f"  falsos vigentes = {fv_l}   falsos stale = {fs_l}   error total = {fv_l + fs_l}")
    hist = _historia_tipada(m, SKILL_VIEJO)
    m.close()

    # ── sliding window (on the SAME typed history, without lineage) ──
    print("\n=== Ventana deslizante (olvido ciego por antigüedad) ===")
    print(f"  (historia de {len(hist)} usos intercalados A/B)")
    for nw in [12, 10, 8, 6, 4, 2]:
        fv, fs = _eval_ventana(hist, nw)
        print(f"    ventana={nw:>3} → falsos_vig={fv}  falsos_stale={fs}  err={fv + fs}")

    # ── exponential decay ──
    print("\n=== Decaimiento exponencial (peso = γ^antigüedad) ===")
    for gamma in [1.0, 0.9, 0.8, 0.6, 0.4, 0.2]:
        fv, fs = _eval_decay(hist, gamma)
        print(f"    γ={gamma:.1f} → falsos_vig={fv}  falsos_stale={fs}  err={fv + fs}")

    # ── summary for JSON ──
    out = {
        "linaje": {"falsos_vigentes": fv_l, "falsos_stale": fs_l, "error": fv_l + fs_l},
        "ventana": [{"n": nw, "error": sum(_eval_ventana(hist, nw))}
                    for nw in [12, 10, 8, 6, 4, 2]],
        "decay": [{"gamma": g, "error": sum(_eval_decay(hist, g))} for g in [1.0, 0.9, 0.8, 0.6, 0.4, 0.2]],
    }
    p = os.path.join(HERE, "router_decay_vs_linaje_resultados.json")
    json.dump(out, open(p, "w", encoding="utf-8"), indent=2)
    print(f"\n[salvado {p}]")


if __name__ == "__main__":
    main()
