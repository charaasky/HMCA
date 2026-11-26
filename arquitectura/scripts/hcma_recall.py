#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""hcma_recall.py — deterministic memory-first (hippocampus recall gate).

Decides whether a query is answered ONLY from HCMA memory (verified/canonical facts) without
calling the LLM. It is the defense against the "inject memory into the prompt and delegate to the
LLM" regression.

The LLM NEVER decides whether memory is used: the gate is deterministic and does not depend on the
corpus size nor the model.

Decision:
  - RECALL_PURO      -> the key covers the query and nothing is left over: answer by template,
                        without LLM.
  - HIT_CONTEXTUAL   -> the key matches but the query asks something more: render the fact and the
                        LLM answers below (advisory).
  - NO_HIT           -> continue the pipeline (cortex / LLM).

Rule (sets, independent of corpus size):
  Q = norm(query)   K = norm(key tokens)   V = norm(value)
  m         = |Q ∩ K|
  cob_key   = m / |K|
  residuo   = Q − (K ∪ V)
  HIT           ⇔ Q ≠ ∅ ∧ (cob_key = 1 if |K| ≤ 2 ; cob_key ≥ 2/3 if |K| ≥ 3)
  RECALL_PURO   ⇔ HIT ∧ residuo = ∅
  HIT_CONTEXTUAL⇔ HIT ∧ residuo ≠ ∅

The KEY qualifies; the VALUE does not (in recall the user asks about the key and does not know the
value). V only reduces the residue. The residue is the defense against weak overlap ("opinás", "el
perro de Juan" stay in the residue and do not cut).

Stdlib only. Does not import prototipo.py (so as not to drag in skills/pint).
"""
import re
import unicodedata

# Stopwords ES+EN: articles/prepositions, interrogatives, possessives and ser/estar/tener/haber.
# Without accents (compared after _quitar_acentos).
_STOP = set("""
a an and at by for from in into is it of on or that the this to with two
de del la el los las un una unos unas y o en por para con al se es son como entre sobre su sus lo
que cual cuales quien quienes cuando donde cuanto cuanta cuantos cuantas
mi tu mis tus nuestro nuestra nuestros nuestras me te le les nos os
ser estar tener haber era fue fueron son esta estaba estan estas estaban tiene tienen hay habia
""".split())


def _quitar_acentos(s):
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c))


def _stem(tok):
    """Light stem (ES/EN gender/plural): 'perras'/'perros'/'perra'/'perro' -> 'perr'.

    It only prunes if a stem of at least 3 characters remains, so as not to destroy short tokens."""
    if len(tok) > 3 and tok.endswith("s"):
        tok = tok[:-1]
    if len(tok) > 3 and tok.endswith(("a", "o")):
        tok = tok[:-1]
    return tok


def _norm_tokens(text):
    """Normalized content tokens (lowercase, no accents, stemmed, no stopwords)."""
    t = _quitar_acentos(text).lower()
    return [_stem(x) for x in re.findall(r"[a-z0-9]+", t) if x not in _STOP]


def _plain(v):
    if isinstance(v, (dict, list)):
        return " ".join(_flatten(v))
    return "" if v is None else str(v)


def _flatten(v):
    if isinstance(v, dict):
        for k, val in v.items():
            yield str(k)
            yield from _flatten(val)
    elif isinstance(v, list):
        for x in v:
            yield from _flatten(x)
    else:
        yield "" if v is None else str(v)


def _evaluar(cands, Q):
    """Applies the recall rule to a list of candidates. Returns (hits, relevant).

    The KEY qualifies; the VALUE does not (it only reduces residue and adds context in short
    queries). `hits` are solid key coverage (deterministic); `relevant` are facts that share
    content (context, not deterministic)."""
    hits = []
    relevant = []
    for c in cands:
        key = c.get("key") or ""
        K = set(_norm_tokens(key))
        V = set(_norm_tokens(_plain(c.get("value"))))
        if not K:
            continue
        m_key = len(Q & K)
        m_val = len(Q & V)
        cob_key = m_key / len(K) if K else 0.0
        # Relevance for context INJECTION (not for deterministic response):
        #   - KEY coverage >= 0.5 (half the concept), OR
        #   - VALUE match only in SHORT queries (|Q|<=2, specific lookup like a name).
        #     In long queries a loose token from the value is noise and is NOT injected.
        valor_relevante = (len(Q) <= 2) and (m_val >= 1)
        if not (cob_key >= 0.5 or valor_relevante):
            continue
        # DETERMINISTIC response: ONLY by KEY coverage. A VALUE match never answers
        # deterministically (it only adds context).
        es_hit = (cob_key >= 1.0) if len(K) <= 2 else (cob_key >= (2.0 / 3.0))
        residuo = Q - (K | V)
        entry = {"fact": c, "m": m_key, "m_val": m_val, "cob_key": cob_key,
                 "residuo": sorted(residuo), "K": sorted(K), "version": c.get("version", 0)}
        relevant.append(entry)
        if es_hit:
            hits.append(entry)
    return hits, relevant


def _desempate(items):
    items.sort(key=lambda h: (-h["m"], -h["cob_key"], -h["version"], h["fact"]["key"]))
    return items[0]


def resolver(query, memoria, top_k=5):
    """Returns a Decision {tipo, hechos, evidencia, tier} for `query` against memory.

    `tier` ∈ {"verified", "claimed", None}: verified answers deterministically/canonical context;
    claimed answers ATTRIBUTED (not deterministic, without feeding calculator/skills).
    `memoria` must expose `.buscar(query, top_k=...)` and, optionally, `.buscar_claimed(...)`."""
    q = (query or "").strip()
    Q = set(_norm_tokens(q))
    if not q or not Q:
        return {"tipo": "NO_HIT", "hechos": [], "tier": None,
                "evidencia": {"Q": sorted(Q), "nota": "query vacía o solo stopwords"}}
    try:
        cands = memoria.buscar(q, top_k=top_k) or []
    except Exception:
        cands = []

    hits, relevant = _evaluar(cands, Q)

    # 1) verified HIT wins over everything (verified > claimed).
    if hits:
        best = _desempate(hits)
        tipo = "RECALL_PURO" if not best["residuo"] else "HIT_CONTEXTUAL"
        return {"tipo": tipo, "hechos": [best["fact"]], "tier": "verified",
                "evidencia": {"Q": sorted(Q), "K": best["K"], "m": best["m"],
                              "cob_key": round(best["cob_key"], 4), "residuo": best["residuo"]}}

    # 2) claimed HIT (attributed, not deterministic): a claim with solid key coverage beats a
    #    verified that is only weak noise (e.g. 'color de mi auto' must not fall into
    #    'perra:color').
    try:
        cands_c = memoria.buscar_claimed(q, top_k=top_k) or []
    except Exception:
        cands_c = []
    hits_c, relevant_c = _evaluar(cands_c, Q)

    if hits_c:
        best = _desempate(hits_c)
        tipo = "HIT_CLAIMED" if not best["residuo"] else "HIT_CONTEXTUAL"
        return {"tipo": tipo, "hechos": [best["fact"]], "tier": "claimed",
                "evidencia": {"Q": sorted(Q), "K": best["K"], "m": best["m"],
                              "cob_key": round(best["cob_key"], 4), "residuo": best["residuo"]}}

    # 3) context: verified first, then claimed.
    if relevant:
        relevant.sort(key=lambda h: (-(h["m"] + h["m_val"]), -h["cob_key"], -h["version"], h["fact"]["key"]))
        return {"tipo": "NO_HIT", "hechos": [h["fact"] for h in relevant], "tier": "verified",
                "evidencia": {"Q": sorted(Q), "relevantes": len(relevant)}}

    if relevant_c:
        relevant_c.sort(key=lambda h: (-(h["m"] + h["m_val"]), -h["cob_key"], -h["version"], h["fact"]["key"]))
        return {"tipo": "NO_HIT", "hechos": [h["fact"] for h in relevant_c], "tier": "claimed",
                "evidencia": {"Q": sorted(Q), "relevantes": len(relevant_c)}}

    return {"tipo": "NO_HIT", "hechos": [], "tier": None,
            "evidencia": {"Q": sorted(Q), "candidatos": [c["key"] for c in cands]}}


def render_recall(decision):
    """Template response for RECALL_PURO (deterministic, with provenance)."""
    partes = []
    for f in decision.get("hechos", []):
        val = _plain(f.get("value"))
        unit = f.get("unit") or ""
        partes.append(f"{f['key']} = {val}" + (f" {unit}" if unit else ""))
    base = "Según tu memoria (hipocampo, verificado): " + "; ".join(partes)
    fuentes = sorted({f.get("fuente") for f in decision.get("hechos", []) if f.get("fuente")})
    if fuentes:
        base += " [fuente: " + ", ".join(fuentes) + "]"
    return base


def render_claimed(decision):
    """Template response for HIT_CLAIMED (ATTRIBUTED, not deterministic): the fact comes from the
    chat unverified and remains pending human validation (promotion)."""
    partes = []
    for f in decision.get("hechos", []):
        val = _plain(f.get("value"))
        unit = f.get("unit") or ""
        partes.append(f"{f['key']} = {val}" + (f" {unit}" if unit else ""))
    base = "Me dijiste (aún sin verificar): " + "; ".join(partes)
    fuentes = sorted({f.get("fuente") for f in decision.get("hechos", []) if f.get("fuente")})
    if fuentes:
        base += " [fuente: " + ", ".join(fuentes) + "]"
    base += " (claim: queda pendiente de validación humana)"
    return base


if __name__ == "__main__":
    # offline smoke test (no DB)
    print(_norm_tokens("de qué color era mi perra"))
    print(_norm_tokens("color_perro"))
    print(_norm_tokens("gris"))
    print("module OK")
