#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de la capa 3 del router: índice semántico (TF-IDF) + top-k + umbrales + skill_sugerido.

Valida el diseño (fable_router_diseno_fable.md):
  - recuperación semántica top-k (no keywords de id).
  - 'diseñame una lampara led' sugiere luminaria_led (banda gris), NO ejecuta.
  - 'actualizá el ledger' NO matchea luminaria_led (sin falso positivo por 'led' en 'ledger').
  - 'dimensionar una luminaria led' SÍ usa (score >= τ_alto).
  - sin índice -> abstener.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import router_skills as RS  # noqa: E402
import indice_skills as IDX  # noqa: E402
import memory_context as MC  # noqa: E402


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    MC.MEMORY_DB = db

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    # skills canónicos para el índice
    m.write("skill:luminaria_led",
            "dimensionar luminaria led: potencia flujo eficacia corriente tension seccion cable",
            fuente="ingeniero", verified=True, meta={"tipo": "skill"})
    m.write("skill:corriente",
            "corriente electrica: potencia tension ley de ohm amperios",
            fuente="ingeniero", verified=True, meta={"tipo": "skill"})

    idx = IDX.construir(m)
    check("índice con 2 skills y hash estable", len(idx.tarjetas) == 2 and len(idx.hash) == 16,
          f"n={len(idx.tarjetas)}")

    # top-k: match fuerte
    top = idx.top_k("dimensionar una luminaria led", k=2)
    check("top-1 luminaria_led para 'dimensionar una luminaria led'",
          top and top[0][0] == "luminaria_led" and top[0][1] >= RS.UMBRAL_ALTO,
          f"top={top}")

    # elegir_skill con índice (embeddings): sinónimos rutean; ruido abstiene
    r_uso = RS.elegir_skill(m, "dimensionar una luminaria led", indice=idx)
    check("fuerte -> usar", r_uso[0] == "usar" and r_uso[1] == "luminaria_led", f"r={r_uso}")

    # con embeddings 'lampara' y 'luminaria' son semánticamente cercanas -> usar (no sugerir)
    r_lampara = RS.elegir_skill(m, "diseñame una lampara led", indice=idx)
    check("sinónimo lampara -> luminaria_led",
          r_lampara[0] in ("usar", "sugerir") and r_lampara[1] == "luminaria_led",
          f"r={r_lampara}")

    # el caso del bug reportado: 'lapicera con led' debe rutear al dominio LED, no caer al LLM
    r_lapicera = RS.elegir_skill(m, "lapicera con led", indice=idx)
    check("lapicera con led -> luminaria_led",
          r_lapicera[0] in ("usar", "sugerir") and r_lapicera[1] == "luminaria_led",
          f"r={r_lapicera}")

    r_abs = RS.elegir_skill(m, "actualizá el ledger", indice=idx)
    check("sin match -> abstener (ledger no es led)", r_abs[0] == "abstener", f"r={r_abs}")

    r_noi = RS.elegir_skill(m, "dimensionar una luminaria led", indice=None)
    check("sin índice -> abstener", r_noi[0] == "abstener", f"r={r_noi}")

    # determinismo: mismo índice -> misma decisión
    idx2 = IDX.construir(m)
    r2 = RS.elegir_skill(m, "diseñame una lampara led", indice=idx2)
    check("mismo índice -> misma decisión", idx2.hash == idx.hash and r2 == r_lampara, f"r2={r2}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ índice semántico + umbrales OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
