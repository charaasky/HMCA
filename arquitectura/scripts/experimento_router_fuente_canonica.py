#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Exploration #5 — closing the 'golden tests' gap: the source must be CANONICAL.

Before, `verificar_skill` promoted to `verified` if the test passed + any `fuente_test`
string (not connected to an HCMA fact). That allowed 'a golden test validates something that is not'.
Now the source must be a CANONICAL `(key, version)` fact: trust is inherited by lineage.

Demonstrates the three cases:
  1) canonical source + test OK  → promotes (verified=True).
  2) canonical source + test BAD → does not promote (incorrect gold detected).
  3) NON-canonical source (loose string, e.g. unverified datum) + test OK → does NOT promote.
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


def calc_area(inputs):
    return [{"name": "area", "value": inputs["largo"] * inputs["ancho"], "unit": "m2"}]


def main():
    m = HCMAMemoriaSQLite(":memory:")

    # CANONICAL source: a verified fact that backs the formula
    fuente_ver = m.write("formula_area", "area = largo * ancho", unit="",
                         fuente="usuario", verified=True)

    # NON-canonical source: a loose string (nothing in HCMA backs it)
    # (simulates an LLM that 'says' the source without HCMA having verified it)

    print("=== Cierre del hueco: la fuente del test debe ser CANÓNICA ===\n")

    # Case 1: canonical source + test OK → promotes
    ok1, v1 = S.verificar_skill(m, "area_ok", calc_area,
                                ({"largo": 4, "ancho": 3}, {"area": 12}),
                                fuente_test=("formula_area", fuente_ver))
    f1 = m.current("skill:area_ok")
    print(f"1) fuente canónica + test OK    → verified={f1['verified'] if f1 else None}  (esperado True)")

    # Case 2: canonical source + test BAD (incorrect gold) → does not promote
    ok2, v2 = S.verificar_skill(m, "area_mal", calc_area,
                                ({"largo": 4, "ancho": 3}, {"area": 99}),
                                fuente_test=("formula_area", fuente_ver))
    f2 = m.current("skill:area_mal")
    print(f"2) fuente canónica + test MAL  → verified={f2['verified'] if f2 else None}  (esperado None/False)")

    # Case 3: NON-canonical source (loose string) + test OK → does NOT promote
    ok3, v3 = S.verificar_skill(m, "area_nofuente", calc_area,
                                ({"largo": 4, "ancho": 3}, {"area": 12}),
                                fuente_test="libro_geo")  # string: NOT (key, version)
    f3 = m.current("skill:area_nofuente")
    print(f"3) fuente no-canónica + test OK → verified={f3['verified'] if f3 else None}  (esperado None/False)")

    # Case 4: source points to a stale/non-current fact → does not promote
    # (we corrupt the source: we rewrite it, leaving the old version non-current)
    m.write("formula_area", "area = largo * ancho (revisada)", unit="",
            fuente="usuario", verified=True)
    ok4, v4 = S.verificar_skill(m, "area_stale", calc_area,
                                ({"largo": 4, "ancho": 3}, {"area": 12}),
                                fuente_test=("formula_area", fuente_ver))  # OLD version
    f4 = m.current("skill:area_stale")
    print(f"4) fuente version vieja (no current) + test OK → verified={f4['verified'] if f4 else None}  (esperado None/False)")

    out = os.path.join(HERE, "router_fuente_canonica_resultados.json")
    json.dump({"caso1": f1["verified"] if f1 else None,
               "caso2": f2["verified"] if f2 else None,
               "caso3": f3["verified"] if f3 else None,
               "caso4": f4["verified"] if f4 else None},
              open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n[salvado {out}]")


if __name__ == "__main__":
    main()
