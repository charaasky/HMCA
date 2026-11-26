#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""M2 — REAL temporal drift: does the similarity retriever return the obsolete version?

Over REAL facts that changed in the world (country names, capitals, laws,
monarchs, records), with their old and current version, measures "stale-retrieval": the fraction
of queries where a similarity retriever (without governance) returns the OLD version
when the current one is available, and whether version governance (filtering to current)
corrects it.
"""
import os, sys, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prototipo import _tokens

HERE = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(HERE, "..", "resultados", "drift_real.json")


def _overlap(qt, t):
    qt, t = set(qt), set(t)
    if not qt:
        return 0.0
    return len(qt & t) / len(qt)


def _doc_score(query, doc_text):
    return _overlap(_tokens(query), _tokens(doc_text))


def evaluar(hechos, gobernado):
    n = stale = 0
    fallos = []
    for f in hechos:
        q = f"what is the {f['propiedad']} of {f['entidad']}"
        docs = [
            (False, f"{f['propiedad']} of {f['entidad']}: {f['viejo']}"),
            (True, f"{f['propiedad']} of {f['entidad']}: {f['nuevo']}"),
        ]
        if gobernado:
            docs = [d for d in docs if d[0]]  # governance: only current versions
        top = sorted(docs, key=lambda cd: -_doc_score(q, cd[1]))[0]
        n += 1
        if not top[0]:
            stale += 1
            fallos.append((f["entidad"], f["propiedad"], f["viejo"], f["nuevo"]))
    return stale / n, fallos


def main():
    hechos = json.load(open(DATOS, encoding="utf-8"))
    s_ung, fallos = evaluar(hechos, gobernado=False)
    s_gob, _ = evaluar(hechos, gobernado=True)

    print("=" * 70)
    print(f"M2 — drift temporal REAL ({len(hechos)} hechos que cambiaron en el mundo)")
    print("=" * 70)
    print(f"  Retriever por similitud (sin gobernanza): stale-retrieval = {s_ung:.2f}")
    print(f"  Gobernanza de versiones (solo current):  stale-retrieval = {s_gob:.2f}")
    print(f"\n  Ejemplos donde la similitud devolvió la versión VIEJA:")
    for ent, prop, viejo, nuevo in fallos[:8]:
        print(f"    · {prop} de {ent}: devolvió «{viejo}» en vez de «{nuevo}»")
    print("\n  Conclusión: la similitud semántica no distingue 'vigente' de 'obsoleto';")
    print("  solo la gobernanza temporal (versión vigente) lo hace.")


if __name__ == "__main__":
    main()
