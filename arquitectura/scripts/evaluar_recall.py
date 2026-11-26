#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Evaluates the local retriever's recall against the gold set.

Measures recall@k, MRR, and precision@k of the retriever (BM25 or dense) over queries with
known answers inside the store. A 'hit' = some needle (substring,
case-insensitive) appears in the text of a retrieved item.
"""
import json, os, sys, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prototipo import _recuperar_local

HERE = os.path.dirname(os.path.abspath(__file__))
GOLD = os.path.join(HERE, "..", "resultados", "gold_set.json")


def _texto_item(tipo, item):
    if tipo == "hecho":
        return f"{item.get('clave', '')} {item.get('valor', '')}"
    v = item.get("variables") or []
    if isinstance(v, dict):
        v = " ".join(f"{k} {x}" for k, x in v.items())
    elif isinstance(v, (list, tuple)):
        v = " ".join(map(str, v))
    return f"{item.get('expresion', '')} {item.get('contexto', '')} {v}"


def evaluar(retriever, top_k=40):
    gold = json.load(open(GOLD, encoding="utf-8"))
    ks = [1, 5, 10, 20, 40]
    recall = {k: 0 for k in ks}
    precision_sum = 0.0
    mrr_sum = 0.0
    detalles = []
    for g in gold:
        query = g["query"]
        needles = [n.lower() for n in g["needles"]]
        hits = retriever(query, top_k=top_k)
        items = [_texto_item(tipo, item).lower() for (s, tipo, item, proc) in hits]
        primer_rank = None
        for rank, txt in enumerate(items, 1):
            if any(n in txt for n in needles):
                primer_rank = rank
                break
        for k in ks:
            kk = min(k, len(items))
            if any(any(n in items[i] for n in needles) for i in range(kk)):
                recall[k] += 1
        n_hits = sum(1 for txt in items if any(n in txt for n in needles))
        precision_sum += n_hits / max(len(items), 1)
        if primer_rank:
            mrr_sum += 1.0 / primer_rank
        detalles.append({"query": query, "hit": primer_rank is not None,
                         "rank": primer_rank, "n_retrieved": len(items), "n_hits": n_hits})
    n = len(gold)
    recall = {k: v / n for k, v in recall.items()}
    return {"n": n, "recall": recall, "mrr": mrr_sum / n,
            "precision@40": precision_sum / n, "detalles": detalles}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--retriever", choices=["bm25", "dense", "hybrid"], default="bm25")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.retriever == "dense":
        from prototipo import _recuperar_dense
        retriever = _recuperar_dense
    elif args.retriever == "hybrid":
        from prototipo import _recuperar_hibrido
        retriever = _recuperar_hibrido
    else:
        retriever = _recuperar_local

    r = evaluar(retriever)
    print(f"Evaluación ({args.retriever}, N={r['n']}):")
    for k in (1, 5, 10, 20, 40):
        print(f"  recall@{k:<3} = {r['recall'][k]:.3f}")
    print(f"  MRR        = {r['mrr']:.3f}")
    print(f"  precision@40 = {r['precision@40']:.3f}")
    fails = [d for d in r["detalles"] if not d["hit"]]
    print(f"\n  fallos ({len(fails)}):")
    for d in fails:
        print(f"    - {d['query']} (retrieved={d['n_retrieved']})")
    if args.out:
        json.dump(r, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n  resultado guardado en {args.out}")


if __name__ == "__main__":
    main()
