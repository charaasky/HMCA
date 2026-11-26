#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Skill router experiment with DRIFT — does the retractable-use signal beat
similarity when the context distribution changes?

Motivation (from the project): semantic similarity is random under drift (E8: 48.7%). The
USAGE signal with retractable lineage should keep pointing to the correct skill even when
similarity is confused, because it retains "what worked before" in an auditable way and
retracts when a result becomes stale.

Scenario (deterministic, no network):
  - Two skills that share superficial vocabulary (ambiguous similarity): SKILL_X (old
    standard) and SKILL_Y (current standard).
  - Pre-drift phase: the task type is solved with SKILL_X; uses accumulate.
  - DRIFT: the regulation changes → the uses of X become stale, and the correct skill becomes Y.
    Similarity WILL keep favoring X (same vocabulary), but the retractable history
    no longer trusts X.

Hypothesis to falsify:
  H1: post-drift, the cascade (retractable-use) hits MORE than 'similarity only'.
  H2: retraction matters: without it (stale uses keep counting), the router would keep
      choosing X (wrong skill).
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "..", "dashboard")
if DASH not in sys.path:
    sys.path.insert(0, DASH)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import router_skills as R  # noqa: E402
import skills as S  # noqa: E402
from experimento_router import _TFIDFIndex  # noqa: E402 (reuses the local vectorizer)

SKILL_X = "dimensionar_cable"        # old standard (pre-drift correct)
SKILL_Y = "dimensionar_cable_norma"  # current standard (post-drift correct)

DESC = {
    SKILL_X: "dimensionar conductor cable por caida de tension segun norma antigua",
    SKILL_Y: "dimensionar conductor cable por caida de tension segun norma vigente",
}


def _registrar_usos(m, skill_id, ver, resultado_key, resultado_version, n):
    for _ in range(n):
        S.registrar_uso(m, skill_id, ver, [(resultado_key, resultado_version)], exito=True)


def main():
    FASE1 = 20   # pre-drift uses of skill X
    POST = 5     # post-drift uses of skill Y
    N = 40       # contexts to evaluate post-drift

    m = HCMAMemoriaSQLite(":memory:")
    vx = S.registro(m, SKILL_X, DESC[SKILL_X], fuente="ingeniero", verified=True,
                    meta={"costo": 0})
    vy = S.registro(m, SKILL_Y, DESC[SKILL_Y], fuente="ingeniero", verified=True,
                    meta={"costo": 0})

    # input data + pre-drift result (validate the uses of X)
    in_v1 = m.write("in_corriente", 10, unit="A", fuente="usuario", verified=True)
    res_x = m.write("res_x", 1, unit="u", fuente="calculadora", verified=True,
                    derived_from={"in_corriente": in_v1})

    # pre-drift uses of X (this is what similarity "remembers")
    _registrar_usos(m, SKILL_X, vx, "res_x", res_x, FASE1)

    # ── DRIFT: the regulation changes ──
    # New input (the current changed) → when writing in_corriente again, it cascades:
    # res_x (derived from in_corriente@v1) becomes stale, and with it THE USES of X that depended
    # on res_x@v1. It is real transitive retraction.
    in_v2 = m.write("in_corriente", 12, unit="A", fuente="usuario", verified=True)
    res_x2 = m.write("res_x", 1, unit="u", fuente="calculadora", verified=True,
                     derived_from={"in_corriente": in_v2})

    # post-drift uses of skill Y (the correct one now)
    _registrar_usos(m, SKILL_Y, vy, "res_x", res_x2, POST)

    # evaluation context: identical on the surface to both skills (ambiguous by similarity)
    ctx_eval = "dimensionar el cable del circuito para la instalacion"

    INDICE = _TFIDFIndex(DESC)

    # ── diagnosis ──
    sx = R._similitud(ctx_eval, SKILL_X, INDICE)
    sy = R._similitud(ctx_eval, SKILL_Y, INDICE)
    usos = R._usos_no_stale(m)
    print(f"Pre-drift:  skill X '{SKILL_X}' con {FASE1} usos.")
    print(f"Post-drift: skill Y '{SKILL_Y}' correcto, {POST} usos.")
    print(f"similitud(ctx, X) = {sx:.4f}   similitud(ctx, Y) = {sy:.4f}")
    print(f"usos no-stale: X={usos.get(SKILL_X, 0)} (eran {FASE1}, retractados)  "
          f"Y={usos.get(SKILL_Y, 0)}")
    print()

    # ── point decisions ──
    out_cascada = R.elegir_skill(m, ctx_eval, indice=INDICE)
    sims = [(sid, R._similitud(ctx_eval, sid, INDICE)) for sid in [SKILL_X, SKILL_Y]]
    mejor_sim = max(sims, key=lambda x: x[1])
    print(f"cascada        -> {out_cascada}")
    print(f"solo_similitud -> usar '{mejor_sim[0]}' (sim={mejor_sim[1]:.4f})")

    # ── aggregate post-drift metric (N contexts) ──
    def _acierto_cascada():
        c = 0
        for i in range(N):
            out = R.elegir_skill(m, f"{ctx_eval} tramo {i}", indice=INDICE)
            if out[0] == "usar" and out[1] == SKILL_Y:
                c += 1
        return c / N

    def _acierto_similitud():
        c = 0
        for i in range(N):
            cc = f"{ctx_eval} tramo {i}"
            s = [(sid, R._similitud(cc, sid, INDICE)) for sid in [SKILL_X, SKILL_Y]]
            if max(s, key=lambda x: x[1])[0] == SKILL_Y:
                c += 1
        return c / N

    acc_cascada = _acierto_cascada()
    acc_sim = _acierto_similitud()

    print(f"\n=== post-drift (N={N}, skill correcto = Y) ===")
    print(f"acierto cascada (uso-retractable → similitud): {acc_cascada:.3f}")
    print(f"acierto solo-similitud:                        {acc_sim:.3f}")

    # save result
    out = os.path.join(HERE, "router_drift_resultados.json")
    json.dump({
        "sim_x": sx, "sim_y": sy,
        "usos_x": usos.get(SKILL_X, 0), "usos_y": usos.get(SKILL_Y, 0),
        "acierto_cascada": acc_cascada, "acierto_similitud": acc_sim},
        open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n[salvado {out}]")
    m.close()


if __name__ == "__main__":
    main()
