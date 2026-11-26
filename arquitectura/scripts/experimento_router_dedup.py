#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Exploration #4b — deduplication: reuse an existing skill instead of creating a copy.

Question: when a NEW context arrives whose superficial vocabulary does NOT match the id of
any skill, but whose need DOES equal an existing skill, does the router reuse it
(`usar`) instead of creating a duplicate (`crear`)?

It answers the honest critique of §12: cross-cutting reuse is not due to manual design, but to
the router detecting semantic equivalence.

Design:
  - Existing skill: 'area' with description 'calcular area superficie suelo habitacion'.
  - New context: 'dimensionar la planta del piso' (same need, different vocabulary):
    it does not share tokens with the id 'area', BUT its description is semantically close.
  - With an embedding index (or TF-IDF), _deduplicar must return 'area' → the router does
    'usar area' instead of 'crear'.
  - Negative control: genuinely new context ('calcular el torque de un tornillo') that
    does NOT equal 'area' → the router must 'crear'.
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "..", "dashboard")
if DASH not in sys.path:
    sys.path.insert(0, DASH)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import skills as S      # noqa: E402
import router_skills as R  # noqa: E402
from experimento_router import _TFIDFIndex  # noqa: E402


def main():
    m = HCMAMemoriaSQLite(":memory:")
    # skill 'area' (rich description, the id does not share tokens with the new context)
    S.registro(m, "area", "calcular area superficie suelo habitacion piso",
               fuente="ingeniero", verified=True, meta={"costo": 0})

    # similarity index over each skill's DESCRIPTION (not the id)
    indice = _TFIDFIndex({"area": "calcular area superficie suelo habitacion piso"})

    print("=== Deduplicación ===\n")

    # Case 1: new context with partial LEXICAL OVERLAP with the 'area' description
    # (does not share tokens with the ID 'area', but does with its DESCRIPTION) → should reuse.
    c1 = "calcular la superficie del suelo de la habitacion"
    out1 = R.elegir_skill(m, c1, indice=indice)
    print(f"contexto: '{c1}'")
    print(f"  → {out1}")
    print(f"  (esperado: ('usar', 'area', 'dedup') — reutiliza por léxico de la descripción, NO crea)\n")

    # Case 2: genuinely new context without an equivalent
    c2 = "calcular el torque de apriete de un tornillo"
    out2 = R.elegir_skill(m, c2, indice=indice)
    print(f"contexto: '{c2}'")
    print(f"  → {out2}")
    print(f"  (esperado: ('crear', ...) — no hay equivalente, debe crear)\n")

    # Case 3: same context as the id (direct token match)
    c3 = "calcular area de una habitacion"
    out3 = R.elegir_skill(m, c3, indice=indice)
    print(f"contexto: '{c3}'")
    print(f"  → {out3}")
    print(f"  (esperado: ('usar', 'area', ...) — match directo)\n")

    out = os.path.join(HERE, "router_dedup_resultados.json")
    json.dump({"caso1": out1, "caso2": out2, "caso3": out3},
              open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"[salvado {out}]")


if __name__ == "__main__":
    main()
