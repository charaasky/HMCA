#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Demo of selective CI (reverificar_descendientes) on real HCMA.

Shows the complete transition:
  1. Core + descendants (some functionally depend on the core, others do not).
  2. Correcting the core → version collapse marks stale ALL descendants (conservative lineage).
  3. Applying selective CI → re-tests and only re-certifies what is still correct; what
     truly broke remains 'unverified' (awaiting a fix).

Distinguishes two types of descendant:
  - 'afectado'   : its value depends on k_nucleo (changes if the core changes).
  - 'no_afectado': depends on the core in LINEAGE (hence the collapse marks it stale), but its
                   value does NOT change (e.g. a derived constant independent of the change).
                   → the CI must re-certify it without false-invalidating it.
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
import prototipo  # noqa: E402  (for FUENTE_CONFIABLE, if needed)


NUCLEO = "k_nucleo"
# descendants: {key: (factor if it changes, None if it does not)}
# 'afectado' = its value = k_nucleo * factor
# 'no_afectado' = fixed value (does not depend on k)
DESCENDIENTES = {
    "res_mecanica": 2.0,   # affected
    "res_electro": 3.0,    # affected
    "res_economia": 5.0,   # affected
    "res_geometria": None,  # not_affected (fixed value 7.0)
    "res_logica": None,     # not_affected (fixed value 11.0)
}
VALOR_FIJO = {"res_geometria": 7.0, "res_logica": 11.0}


def _calcular_descendiente(clave, k_nuevo):
    """Re-computes the value of a descendant given the new k_nucleo.
    Returns None if it cannot be re-computed (should not happen here)."""
    if DESCENDIENTES.get(clave) is not None:
        return k_nuevo * DESCENDIENTES[clave]
    # not_affected: fixed value, independent of k
    return VALOR_FIJO[clave]


def main():
    m = HCMAMemoriaSQLite(":memory:")
    vn = S.registro(m, "integral", "núcleo", fuente="ingeniero", verified=True,
                    meta={"costo": 0})
    # `m.write` returns the VERSION (int), not the value. We store the version separately.
    k_ver = m.write(NUCLEO, 1.0, unit="", fuente="usuario", verified=True)
    k_valor_inicial = 1.0

    # build descendants (all with lineage to k_nucleo, so that the collapse reaches them)
    for clave, factor in DESCENDIENTES.items():
        valor = k_valor_inicial * factor if factor is not None else VALOR_FIJO[clave]
        m.write(clave, valor, unit="", fuente="calculadora", verified=True,
                derived_from={NUCLEO: k_ver, "skill:integral": vn})

    print("=== ANTES de corregir ===")
    for clave in DESCENDIENTES:
        f = m.current(clave)
        print(f"  {clave:<16} value={f['value'] if f else None}")

    # ── correct the core (1.0 -> 2.0) ──
    m.write(NUCLEO, 2.0, unit="", fuente="usuario", verified=True)

    print("\n=== Después del collapse (linaje conservador) ===")
    for clave in DESCENDIENTES:
        f = m.current(clave)
        print(f"  {clave:<16} current={f['value'] if f else None}  "
              f"(None = quedó stale, sin re-test)")

    # ── apply selective CI ──
    res = S.reverificar_descendientes(m, NUCLEO, _calcular_descendiente)

    print(f"\n=== Después del CI selectivo ===")
    print(f"  re-certificados: {res['re_certificados']}  |  cambiaron: {res['cambiaron']}  "
          f"|  no-cambiaron: {res['no_cambiaron']}  |  quedan inválidos: {res['quedan_invalidos']}")
    for clave in DESCENDIENTES:
        f = m.current(clave)
        print(f"  {clave:<16} current={f['value'] if f else None}")

    # summary for JSON
    estados = {clave: (m.current(clave)['value'] if m.current(clave) else None)
               for clave in DESCENDIENTES}
    out = os.path.join(HERE, "router_ci_selectivo_resultados.json")
    json.dump({"re_certificados": res["re_certificados"],
               "cambiaron": res["cambiaron"],
               "no_cambiaron": res["no_cambiaron"],
               "quedan_invalidos": res["quedan_invalidos"],
               "estados_finales": estados}, open(out, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    print(f"\n[salvado {out}]")

    print("\nResultado esperado: los 5 descendientes quedan re-computados correctamente,")
    print("los 'afectados' cambian (valor*k=2), los 'no_afectados' conservan su valor fijo.")
    print("NADIE queda stale-use (todos tienen current), y no hay falsa-invalidación.")


if __name__ == "__main__":
    main()
