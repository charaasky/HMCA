#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Experiment F (v3) — cost/benefit of lineage-based versioning, with dependency
diversity.

Corrects the previous version: includes domains that DEPEND on the core (they break when it is corrected)
and INDEPENDENT domains (they should not be invalidated). Only then is 'pure lineage'
(also discards what is valid) distinguished from 'lineage + CI' (only touches what broke).

Three possible states of each domain after correcting the core:
  - 'correcto'     : current value and consistent with the new core.
  - 'stale-use'    : current value but OLD (would use the obsolete version).
  - 'invalido'     : no current value (became stale, needs re-test) → lost coverage.

Clean metrics:
  - stale-use rate = fraction of domains in 'stale-use' (what HCMA eliminates by lineage).
  - false-invalidation = fraction of INDEPENDENT domains unfairly marked stale.
  - cost = number of re-verifications executed after the correction.

What is truly measured (real HCMA): lineage marks stale ALL descendants of k_nucleo.
The independent domains (without derived_from to k_nucleo) do NOT become stale.
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


NUCLEO = "integral"
# domains that DEPEND on the core (their result = k_nucleo * factor)
DEPENDIENTES = {"mecanica": 2.0, "electro": 3.0, "economia": 5.0}
# domains INDEPENDENT (their result does not depend on k_nucleo)
INDEPENDIENTES = {"geometria": 7.0, "logica": 11.0}


def _estado(m, dominio, k_nuevo, factores_indep):
    """Returns the state of a domain after the correction."""
    f = m.current("res_" + dominio)
    if f is None:
        return "invalido"
    if dominio in DEPENDIENTES:
        esperado = k_nuevo * DEPENDIENTES[dominio]
    else:
        esperado = factores_indep[dominio]
    return "correcto" if abs(f["value"] - esperado) < 1e-9 else "stale-use"


def construir(nucleo_ver=1.0):
    """Builds core + dependent and independent domains, with real lineage."""
    m = HCMAMemoriaSQLite(":memory:")
    vn = S.registro(m, NUCLEO, "integral base", fuente="ingeniero", verified=True,
                    meta={"costo": 0})
    k = m.write("k_nucleo", nucleo_ver, unit="", fuente="usuario", verified=True)
    for d, f in DEPENDIENTES.items():
        res = m.write("res_" + d, nucleo_ver * f, unit="", fuente="calculadora",
                      verified=True, derived_from={"k_nucleo": k, "skill:" + NUCLEO: vn})
        vd = S.registro(m, d, f"dominio {d}", fuente="ingeniero", verified=True,
                        meta={"costo": 0})
        S.registrar_uso(m, d, vd, [("res_" + d, res)], exito=True)
    for d, f in INDEPENDIENTES.items():
        # independent: does NOT derive from k_nucleo. It is a BASE DATUM (not a derived calculation),
        # hence fuente='usuario' (not 'calculadora' without lineage, which would be born unverified).
        res = m.write("res_" + d, f, unit="", fuente="usuario", verified=True,
                      derived_from={})
        vd = S.registro(m, d, f"dominio {d}", fuente="ingeniero", verified=True,
                        meta={"costo": 0})
        S.registrar_uso(m, d, vd, [("res_" + d, res)], exito=True)
    return m


def _clasificar_post(m, k_nuevo):
    estados = {}
    for d in list(DEPENDIENTES) + list(INDEPENDIENTES):
        estados[d] = _estado(m, d, k_nuevo, INDEPENDIENTES)
    return estados


def cond_linaje():
    """4) pure lineage: correcting k_nucleo → collapse marks stale ALL dependents.
    The independent ones remain intact. NO one is re-verified (no re-test)."""
    m = construir(1.0)
    m.write("k_nucleo", 2.0, unit="", fuente="usuario", verified=True)
    estados = _clasificar_post(m, 2.0)
    n = len(estados)
    stale_use = sum(1 for e in estados.values() if e == "stale-use") / n
    invalidos = sum(1 for e in estados.values() if e == "invalido") / n
    # false invalidation = independent ones that ended up 'invalido' (they should not)
    falsa_inv = sum(1 for d in INDEPENDIENTES if estados[d] == "invalido") / len(INDEPENDIENTES)
    coste = 0  # nothing re-verified yet
    m.close()
    return stale_use, invalidos, falsa_inv, coste


def cond_linaje_ci():
    """5) lineage + CI: after the collapse, re-tests and re-certifies the dependents.
    The independent ones are not touched (they are preserved)."""
    m = construir(1.0)
    m.write("k_nucleo", 2.0, unit="", fuente="usuario", verified=True)
    coste = 0
    # re-verify only the dependents (which are the ones that ended up stale)
    for d, f in DEPENDIENTES.items():
        m.write("res_" + d, 2.0 * f, unit="", fuente="calculadora", verified=True,
                derived_from={"k_nucleo": m.current("k_nucleo")["version"],
                              "skill:" + NUCLEO: m.current("skill:" + NUCLEO)["version"]})
        coste += 1
    estados = _clasificar_post(m, 2.0)
    n = len(estados)
    stale_use = sum(1 for e in estados.values() if e == "stale-use") / n
    invalidos = sum(1 for e in estados.values() if e == "invalido") / n
    falsa_inv = sum(1 for d in INDEPENDIENTES if estados[d] == "invalido") / len(INDEPENDIENTES)
    m.close()
    return stale_use, invalidos, falsa_inv, coste


def main():
    print("=== Experimento F v3 (diversidad de dependencias, HCMA real) ===\n")
    print(f"Dominios dependientes del núcleo: {list(DEPENDIENTES)}")
    print(f"Dominios independientes:          {list(INDEPENDIENTES)}\n")

    # measurable conditions
    s4, inv4, f4, c4 = cond_linaje()
    s5, inv5, f5, c5 = cond_linaje_ci()

    # simulated conditions (without versioning)
    # flat (1): nothing is invalidated → the DEPENDENTS end up stale-use (old), cost 0
    s1 = len(DEPENDIENTES) / (len(DEPENDIENTES) + len(INDEPENDIENTES))
    inv1, f1, c1 = 0.0, 0.0, 0.0
    # re-verify everything (3): re-tests EVERYTHING (dependents + independents) → stale 0, high cost
    s3, inv3, f3 = 0.0, 0.0, 0.0
    c3 = len(DEPENDIENTES) + len(INDEPENDIENTES)

    print(f"{'condición':<30} {'stale-use':>10} {'invalidos':>10} {'falsa-inv':>10} {'coste':>7}")
    print(f"{'plano (no versiona)':<30} {s1:>10.2f} {inv1:>10.2f} {f1:>10.2f} {c1:>7.1f}")
    print(f"{'re-verificar todo':<30} {s3:>10.2f} {inv3:>10.2f} {f3:>10.2f} {c3:>7.1f}")
    print(f"{'linaje (invalida depend.)':<30} {s4:>10.2f} {inv4:>10.2f} {f4:>10.2f} {c4:>7.1f}")
    print(f"{'linaje + CI (re-testa dep.)':<30} {s5:>10.2f} {inv5:>10.2f} {f5:>10.2f} {c5:>7.1f}")

    out = os.path.join(HERE, "router_experimento_F_resultados.json")
    json.dump([
        {"cond": "plano", "stale_use": s1, "invalidos": inv1, "falsa_inv": f1, "coste": c1},
        {"cond": "reverificar_todo", "stale_use": s3, "invalidos": inv3, "falsa_inv": f3, "coste": c3},
        {"cond": "linaje", "stale_use": s4, "invalidos": inv4, "falsa_inv": f4, "coste": c4},
        {"cond": "linaje_ci", "stale_use": s5, "invalidos": inv5, "falsa_inv": f5, "coste": c5},
    ], open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"\n[salvado {out}]")
    print("\nLectura honesta:")
    print("  - 'linaje' elimina stale-use (0.00) pero deja DEPENDIENTES 'invalidos' hasta")
    print("    re-test (cobertura perdida). Los independientes quedan intactos (falsa_inv=0).")
    print("  - 'linaje + CI' recupera todo con stale-use=0 y coste solo de dependientes.")
    print("  - La diferencia real linaje vs CI es la COBERTURA (invalidos), no el stale-use.")


if __name__ == "__main__":
    main()
