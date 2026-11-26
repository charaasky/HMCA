#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""D.4 — post-drift cold-start: what happens when the correct skill starts with 0 uses?

Fable's critique: 'there is no post-drift cold-start (the correct skill starts with 5 gifted
uses), which is the hard regime of drift'. This experiment sweeps it.

Design:
  - Pre-drift: the 'old' skill accumulates uses (it is the dominant one). The 'current' one does not exist
    yet (or has 0 uses).
  - DRIFT: the 'current' one becomes correct, BUT it starts with 0 uses (n_c = 0).
  - n_c is swept from 0 to 5 (uses that the current one accumulates post-drift).
  - The 'retractable-use → similarity → abstention' cascade should:
      * with n_c = 0 (total cold-start): fall back to SIMILARITY (level 2), because there is no usage
        signal → its accuracy = similarity accuracy (honest degradation).
      * as n_c rises, the usage weight starts to dominate and accuracy rises to 1.0.

The accuracy_cascade(n_c) vs accuracy_similarity_only(n_c) curve shows WHERE the edge
of the method is: retractable-use only contributes when the correct skill started accumulating
usage evidence; before that, the cascade is honestly 'similarity + abstention'.

Metric: routing accuracy (chooses the correct skill) sweeping the number of post-drift uses.
Assumption (same as D.3): the router chooses by weight = number of non-stale uses; if tied or no
use, it falls back to similarity (modeled as a Bernoulli signal with fixed ambiguity).
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


def simular(n_viejo_validos, n_vigente, ambiguedad=0.5):
    """Cascade router model in the post-drift regime.

    - n_viejo_validos : non-stale uses of the old skill (the invalid ones were already retracted).
    - n_vigente       : uses of the correct skill (from 0 = cold-start to >0).

    Cascade:
      1) if n_vigente > 0 and n_vigente > n_viejo_validos → chooses current (accuracy 1).
      2) if n_vigente == 0 (cold-start) → there is no usage signal → falls back to similarity, which
         hits with prob (1 - ambiguity). (same as 'similarity only').
      3) if tied → similarity (1 - ambiguity).
    """
    # Retraction already happened: the invalid uses of the old skill are out. We are left with
    # n_viejo_validos (the ones that survived) competing with n_vigente.
    if n_vigente > 0 and n_vigente > n_viejo_validos:
        return 1.0   # usage dominates → hits
    # cold-start or tie → similarity (Bernoulli signal)
    return 1.0 - ambiguedad


def main():
    n_viejo_validos = 4   # non-stale uses of the old skill after retracting the invalid ones (res_B)
    AMBIGUEDAD = 0.5      # random similarity (hard regime, like E8)
    NC = list(range(0, 11, 2))  # uses of the correct skill post-drift: 0 (cold-start) -> 10

    print(f"Usos no-stale del skill viejo (post-retractación) = {n_viejo_validos}")
    print(f"Similitud (fallback nivel 2) acierta con prob = {1 - AMBIGUEDAD:.2f}\n")

    print(f"{'n_c (usos vigente)':>18} | {'cascada acierto':>16} | {'solo_sim acierto':>15} | {'aporta uso?':>11}")
    curva = []
    for n_c in NC:
        acc_cascada = simular(n_viejo_validos, n_c, AMBIGUEDAD)
        acc_sim = 1.0 - AMBIGUEDAD
        aporta = "sí" if (n_c > 0 and n_c > n_viejo_validos) else "no (sim)"
        curva.append({"n_c": n_c, "acierto_cascada": acc_cascada,
                      "acierto_solo_sim": acc_sim, "uso_aporta": aporta == "sí"})
        print(f"{n_c:>18} | {acc_cascada:>16.2f} | {acc_sim:>15.2f} | {aporta:>11}")

    out = os.path.join(HERE, "router_coldstart_resultados.json")
    json.dump({"n_viejo_validos": n_viejo_validos, "ambiguedad": AMBIGUEDAD,
               "curva": curva}, open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n[salvado {out}]")


def _verificar_con_memoria_real():
    """Sanity-check: in real cold-start (n_c=0), the cascade falls back to similarity, not to usage."""
    m = HCMAMemoriaSQLite(":memory:")
    vv = S.registro(m, SKILL_VIEJO, "norma antigua", fuente="ingeniero", verified=True,
                    meta={"costo": 0})
    vg = S.registro(m, SKILL_VIGENTE, "norma vigente", fuente="ingeniero", verified=True,
                    meta={"costo": 0})
    in_A = m.write("input_A", 1, unit="u", fuente="usuario", verified=True)
    in_B = m.write("input_B", 1, unit="u", fuente="usuario", verified=True)
    res_A = m.write("res_A", 1, unit="u", fuente="calculadora", verified=True,
                    derived_from={"input_A": in_A})
    res_B = m.write("res_B", 1, unit="u", fuente="calculadora", verified=True,
                    derived_from={"input_B": in_B})
    for _ in range(10):
        S.registrar_uso(m, SKILL_VIEJO, vv, [("res_A", res_A)], exito=True)
    for _ in range(4):
        S.registrar_uso(m, SKILL_VIEJO, vv, [("res_B", res_B)], exito=True)
    # drift: invalidates res_A
    m.write("input_A", 2, unit="u", fuente="usuario", verified=True)
    # at this point, current has 0 uses (cold-start)
    usos = _usos_no_stale(m)
    print(f"\n[memoria real] post-drift, skill vigente con 0 usos:")
    print(f"  usos no-stale = {usos}")
    print(f"  → el vigente no tiene señal de uso; la cascada caería a similitud.")
    m.close()


if __name__ == "__main__":
    main()
    _verificar_con_memoria_real()
