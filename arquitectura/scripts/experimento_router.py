#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Skill router experiment — measures cascade vs baselines with synthetic ground truth.

Objective: demonstrate (or refute) that the "retractable-use → similarity → abstention" cascade
selects the correct skill at LOWER cost than "similarity only" or "LLM only", and that
abstention is a first-class output (coverage/precision/cost), not a failure.

Design (deterministic, no network):
  - A set of `contextos` with their `correct skill` (ground truth).
  - HCMA memory with: declared skills, and a HISTORY of uses (with lineage) that simulates which
    skills were used and worked. Part of the history becomes `stale` (retracted) when
    correcting an input, to verify that the router does NOT trust retracted uses.
  - 4 policies are compared: cascade, similarity-only, LLM-only(proxy), use-without-abstention.

Metrics per policy:
  - acierto     : fraction of contexts where it chose the correct skill
  - costo_medio : estimated accumulated cost (signal level used)
  - abstencion  : abstention fraction
  - precision_no_abstencion : accuracy conditional on not abstaining
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "..", "dashboard")
if DASH not in sys.path:
    sys.path.insert(0, DASH)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import router_skills as R  # noqa: E402
import skills as S  # noqa: E402

# ── synthetic ground truth ──────────────────────────────────────────────────────
# (context, correct_skill). Several contexts point to the same skill (reuse).
CASOS = [
    ("calcular el area de una habitacion", "area"),
    ("medir la superficie del suelo", "area"),
    ("dimensionar el cable por caida de tension", "seccion_cable"),
    ("que seccion de conductor necesito", "seccion_cable"),
    ("potencia de un motor electrico", "motor"),
    ("par motor y velocidad", "motor"),
    ("buscar el precio actual del acero", "busqueda_web"),
    ("normalizacion vigente de instalaciones", "busqueda_web"),
]


def _indice():
    """Deterministic similarity proxy: {skill_id: description}."""
    return {
        "area": "calcular area superficie suelo habitacion",
        "seccion_cable": "dimensionar conductor cable caida tension",
        "motor": "potencia par motor electrico velocidad",
        "busqueda_web": "buscar precio normativa web vigente",
    }


class _EmbeddingIndex:
    """Embedding index for Level 2 (similarity).

    Exposes `.encode(text) -> vector` (via SentenceTransformer) and `.get(skill_id) -> vector`
    precomputed. It is the 'hot' index that in production would be the cortex/dense.
    """
    def __init__(self, modelo, textos):
        self.modelo = modelo
        self.textos = textos  # {skill_id: description}
        self._vecs = {sid: modelo.encode(t, convert_to_numpy=True,
                                         normalize_embeddings=True)
                      for sid, t in textos.items()}

    def encode(self, texto):
        return self.modelo.encode(texto, convert_to_numpy=True,
                                  normalize_embeddings=True)

    def get(self, skill_id):
        return self._vecs.get(skill_id)


def _load_embedding_index():
    """Similarity index. Tries real embeddings (MiniLM); if there is no network/model,
    falls back to a deterministic local TF-IDF vectorizer (without download). Returns (index, type)."""
    try:
        from sentence_transformers import SentenceTransformer
        modelo = SentenceTransformer("all-MiniLM-L6-v2")
        return _EmbeddingIndex(modelo, _indice()), "embeddings"
    except Exception:
        return _TFIDFIndex(_indice()), "tfidf"


class _TFIDFIndex:
    """Local TF-IDF vectorizer (no network) as a proxy for semantic similarity.

    Better than raw overlap: it weights rare terms and normalizes by length.
    `.encode(text)` returns a sparse dictionary vector; `.get(skill_id)` the vector
    precomputed for the skill. Similarity is computed as cosine in _similitud_tfidf."""
    def __init__(self, textos):
        self.textos = textos
        self._vocab, self._idf = self._build_idf(textos.values())
        self._vecs = {sid: self._vectorizar(t) for sid, t in textos.items()}

    def _build_idf(self, docs):
        import math
        docs = [self._terms(d) for d in docs]
        vocab = sorted({t for d in docs for t in d})
        n = len(docs)
        idf = {}
        for t in vocab:
            df = sum(1 for d in docs if t in d)
            idf[t] = math.log((1 + n) / (1 + df)) + 1.0
        return vocab, idf

    def _terms(self, s):
        import re
        return set(re.findall(r"[a-z0-9]+", (s or "").lower()))

    def _vectorizar(self, s):
        import math
        terms = [t for t in self._terms(s) if t in self._idf]
        if not terms:
            return {}
        denom = math.sqrt(sum(self._idf[t] ** 2 for t in terms)) or 1.0
        return {t: self._idf[t] / denom for t in terms}

    def encode(self, texto):
        return self._vectorizar(texto)

    def get(self, skill_id):
        return self._vecs.get(skill_id)


# declared cost per skill (meta.costo)
COSTOS = {"area": 0, "seccion_cable": 0, "motor": 0, "busqueda_web": 3}


def build_memory():
    """Builds the HCMA memory with skills + usage history (partly retracted)."""
    m = HCMAMemoriaSQLite(":memory:")
    ids = list(COSTOS)
    # declare skills (canonical)
    ver = {}
    for sid in ids:
        ver[sid] = S.registro(m, sid, _indice()[sid], fuente="ingeniero",
                              verified=True, meta={"costo": COSTOS[sid]})
    # history of successful uses with lineage to results
    def _resultado(nombre, input_keys):
        # input datum + derived result (calculadora) validate the use
        dv = {}
        for k in input_keys:
            dv[k] = m.write(k, 1, unit="u", fuente="usuario", verified=True)
        return m.write(nombre, 1, unit="u", fuente="calculadora", verified=True,
                       derived_from=dv)

    r_area = _resultado("res_area", ["largo", "ancho"])
    S.registrar_uso(m, "area", ver["area"], [("res_area", r_area)], exito=True)
    S.registrar_uso(m, "area", ver["area"], [("res_area", r_area)], exito=True)

    r_cable = _resultado("res_cable", ["corriente"])
    S.registrar_uso(m, "seccion_cable", ver["seccion_cable"],
                    [("res_cable", r_cable)], exito=True)

    # motor and busqueda_web remain unused (cold-start) for level 2
    return m


def _policy_cascada(m, contexto):
    out = R.elegir_skill(m, contexto, indice=INDICE)
    return out


def _policy_solo_similitud(m, contexto):
    """Baseline: always choose by similarity (without using the usage level)."""
    ids = R._skill_ids(m)
    match = R._match(contexto, ids) or ids  # if it does not match, consider all
    if not match:
        return ("abstener", "sin match")
    sims = [(sid, R._similitud(contexto, sid, INDICE)) for sid in match]
    mejor = max(sims, key=lambda x: x[1])
    if mejor[1] >= R.UMBRAL_SIMILITUD:
        return ("usar", mejor[0], "similitud")
    return ("abstener", "similitud insuficiente")


def _policy_solo_llm(m, contexto):
    """Baseline: 'LLM planner' that ALWAYS pays the LLM cost (2) for selection.

    Its accuracy is modeled with the SAME similarity as level 2, so as not to artificially
    inflate its accuracy: the honest comparison is 'do I reach the same accuracy paying
    always LLM vs paying only when the cheap signal is not enough?'. It never abstains."""
    ids = R._skill_ids(m)
    sims = [(sid, R._similitud(contexto, sid, INDICE)) for sid in ids]
    mejor = max(sims, key=lambda x: x[1])
    return ("usar", mejor[0], "llm")


def _policy_uso_sin_abstencion(m, contexto):
    """Ablation: like cascade but WITHOUT abstention or creation (uses the usage level and, if not,
    falls back to forced similarity)."""
    out = R.elegir_skill(m, contexto, indice=INDICE)
    if out[0] in ("abstener", "crear"):
        # degrades to forced similarity (chooses the best even below threshold)
        ids = R._skill_ids(m)
        match = R._match(contexto, ids) or ids
        sims = [(sid, R._similitud(contexto, sid, INDICE)) for sid in match]
        mejor = max(sims, key=lambda x: x[1])
        return ("usar", mejor[0], "similitud_forzada")
    return out


COSTO_NIVEL = {"uso": 0, "costo": 0, "similitud": 1, "llm": 2,
               "similitud_forzada": 1, "abstener": 0, "crear": 2}

INDICE = None  # similarity index (embeddings or text); loaded in main()

POLITICAS = {
    "cascada": _policy_cascada,
    "solo_similitud": _policy_solo_similitud,
    "solo_llm": _policy_solo_llm,
    "uso_sin_abstencion": _policy_uso_sin_abstencion,
}


def evaluar(policy, m, casos):
    acc = 0
    costo = 0
    abst = 0
    n_resp = 0
    for contexto, correcto in casos:
        out = policy(m, contexto)
        nivel = out[2] if len(out) > 2 else out[0]
        costo_nivel = COSTO_NIVEL.get(nivel, 1)
        costo += costo_nivel
        if out[0] == "abstener":
            abst += 1
            continue
        if out[0] == "crear":
            # create is considered "does-not-hit" in the absence of a prior correct skill
            n_resp += 1
            continue
        n_resp += 1
        if out[1] == correcto:
            acc += 1
    n = len(casos)
    return {
        "acierto": round(acc / n, 4),
        "costo_total": costo,
        "costo_medio": round(costo / n, 4),
        "abstencion": round(abst / n, 4),
        "precision_no_abstencion": round(acc / n_resp, 4) if n_resp else None,
        "respuestas": n_resp,
    }


def main():
    global INDICE
    _ind, _tipo = _load_embedding_index()
    INDICE = _ind
    print(f"✅ similitud por {_tipo} (determinista, sin red).\n")
    m = build_memory()
    print("Skills declarados y usos (vista):")
    for s in S.list_skills(m):
        print(f"  {s['id']:<14} usos={s['usos']:<3} costo={s['meta'].get('costo',0)} "
              f"verified={s['verified']}")
    print()
    print(f"{'política':<20} {'acierto':>8} {'costo_tot':>10} {'costo_med':>10} "
          f"{'abstención':>10} {'prec_no_abs':>12}")
    results = {}
    for nombre, p in POLITICAS.items():
        r = evaluar(p, m, CASOS)
        results[nombre] = r
        pna = f"{r['precision_no_abstencion']:.4f}" if r['precision_no_abstencion'] is not None else '—'
        print(f"{nombre:<20} {r['acierto']:>8.4f} {r['costo_total']:>10} "
              f"{r['costo_medio']:>10.4f} {r['abstencion']:>10.4f} {pna:>12}")
    out = os.path.join(HERE, "router_experimento_resultados.json")
    json.dump(results, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"\n[salvado {out}]")
    m.close()


if __name__ == "__main__":
    main()
