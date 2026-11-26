#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""linaje.py — classification of a fact's confidence GRADE, computed from lineage.

Does NOT touch the core (`memoria_sqlite.py`). It is Fable's "ontology of bases" (§7septies):
which root types exist and how strong they are. The core is domain-agnostic; this module
distinguishes, ON TOP of the existing `derived_from` DAG, which root is an independent oracle vs
a role axiom.

Central rule (Fable option D): the grade is NOT declared, it is COMPUTED from the roots.
Root classification:
  - `fuente:*` / `cota:*`   → ORACLE (canonical): table/norm/measurement/physical bound.
  - `skill:*:spec`          → FORM (the contract that declares inputs/outputs/tolerance),
                                NOT the authority of the expected value.
  - any other               → unknown (the weakest).

Where the minimum operates (per Fable, line "grado = min over roots of each test, and over tests
of the ok"):
  1. A `test:*` is CANONICAL if its lineage includes AT LEAST one oracle (`fuente:*`/`cota:*`).
     If it only derives from `spec`, it is CONFORME (consistency, not validity).
  2. The `ok` is CANONICAL iff ALL its `test:*` are canonical. It is strict on purpose.

This materializes I11 (independent oracle) and I15 (grade computed from the DAG) without touching
the core. If a `fuente:*` collapses (stale), its `test:*` stops having a current root and degrades
on its own.

Grades (highest to lowest): "canonico" > "conforme" > "desconocido".
"""


# ─────────────────────────────────────────────────────────────────────────────
# Root classification by namespace
# ─────────────────────────────────────────────────────────────────────────────
def _clasificar_raiz(key, fact=None):
    """Classifies a root. Requires `fact` (the fact, with its `authority` flag) for the
    `fuente:*`/`cota:*` roots: they are ORACLE only if `authority=True`. A `fuente:*` of type
    'evidencia' (authority=False) does NOT raise the grade to canonical — it serves to detect
    contradictions, not to certify validity. (P0 adjustment of the MCP revision.)"""
    if key.startswith("fuente:") or key.startswith("cota:"):
        if fact is not None and not fact.get("authority", False):
            return "evidencia"
        return "oraculo"
    if key.startswith("skill:") and key.endswith(":spec"):
        return "spec"
    return "desconocida"


def _raices(m, key, version):
    """Walks derived_from transitively returning [(key, fact|None)] of the roots.

    A root is: (a) a fact without `derived_from`, or (b) a NON-current dependency
    (`get_version` -> None, or `stale`). In (b) the root lost support. The `fact` is kept so
    `_clasificar_raiz` can read `authority` (oracle vs evidence)."""
    visitados = set()
    raices = []
    pila = [(key, version)]
    while pila:
        k, v = pila.pop()
        if (k, v) in visitados:
            continue
        visitados.add((k, v))
        f = m.get_version(k, v)
        if f is None or f.get("stale"):
            raices.append((k, None))
            continue
        df = f.get("derived_from") or {}
        if not df:
            raices.append((k, f))
            continue
        for dk, dv in df.items():
            pila.append((dk, dv))
    return raices


def grado_test(m, key, version):
    """Grade of a single `test:*`: 'canonico' if its lineage has >=1 current oracle with
    authority=True; 'conforme' if only spec or only evidence; 'desconocido' if nothing useful."""
    raices = _raices(m, key, version)
    tipos = {_clasificar_raiz(r[0], r[1]) for r in raices}
    if "oraculo" in tipos:
        return "canonico"
    if "spec" in tipos or "evidencia" in tipos:
        return "conforme"
    return "desconocido"


def grado(m, key, version):
    """Grade of an `ok:*` (or composite fact) = the MINIMUM over its direct `test:*` dependencies.
    If it does not derive from tests, it is classified by its own roots."""
    return grado_razon(m, key, version)[0]


def grado_razon(m, key, version):
    """Returns (grado, {dep_key: grado_dep}) for audit.

    For an `ok:<hash>`, its direct dependencies are the `test:*`; the grade is the minimum of the
    grades of those tests (strict: all canonical ⟹ canonical)."""
    f = m.get_version(key, version)
    if f is None:
        return "desconocido", {}
    df = f.get("derived_from") or {}
    deps = {}
    if not df:
        # no deps: it is a leaf; classify by its own root type (with its authority)
        t = _clasificar_raiz(key, f)
        g = "canonico" if t == "oraculo" else ("conforme" if t in ("spec", "evidencia") else "desconocido")
        return g, {key: g}
    for dk, dv in df.items():
        deps[dk] = grado_test(m, dk, dv)
    if not deps:
        return "desconocido", {}
    orden = {"canonico": 3, "conforme": 2, "desconocido": 1}
    g = min(deps.values(), key=lambda x: orden[x])
    return g, deps
