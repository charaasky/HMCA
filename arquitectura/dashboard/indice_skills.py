#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""indice_skills.py — semantic skill index (layer 3 of the router, Fable 5.2 opinion).

Uses REAL EMBEDDINGS (sentence-transformers) when the model is available; otherwise degrades to
sparse TF-IDF (stdlib). The interface is unified:

  - .encode(texto) -> vector (normalized dense numpy, or normalized sparse dict)
  - .get(skill_id) -> vector or None
  - .top_k(texto, k) -> [(skill_id, score)] by descending cosine
  - .hash -> index fingerprint (changes if a card or the model changes)

Pinned model, configurable via HCMA_EMBED_MODEL. Default: all-MiniLM-L6-v2 (already in local cache).
For full multi-language: paraphrase-multilingual-MiniLM-L12-v2 (~470MB one-time download).

IMPORTANT (multi-language): the skill ID (slug) is INDEPENDENT of language (see
router_skills._slug), but MATCHING across languages depends on the embeddings model. The default
all-MiniLM-L6-v2 is English ONLY: it does NOT match '帮我设计电视遥控器' against 'control_tv'. For
cross-lingual matching, set HCMA_EMBED_MODEL=paraphrase-multilingual-MiniLM-L12-v2 in the dashboard
environment.
"""
import hashlib
import json
import math
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
# sentence-transformers / torch live in .deps (as a prototype).
DEP = os.path.join(HERE, "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

VERSION = "emb-v1"

# Pinned model. Configurable for multi-language.
MODELO_DEFAULT = os.environ.get("HCMA_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# Models with real CROSS-LINGUAL capability (P0-3 Fable). The default all-MiniLM-L6-v2 is English
# only: it does not match '帮我设计电视遥控器' against 'control_tv'.
MODELOS_MULTILINGUE = {
    "paraphrase-multilingual-minilm-l12-v2",
    "multilingual-e5-small",
    "multilingual-e5-base",
    "multilingual-e5-large",
}

# In-memory model cache (avoids reloading it on every dashboard request).
_EMB_CACHE = {}


def es_modelo_multilingue(modelo=None):
    """Is the embeddings model multilingual (cross-lingual matching)?

    The default all-MiniLM-L6-v2 is English ONLY. P0-3 Fable: without a multilingual model, a
    non-latin query cannot be deduplicated via embeddings -> the router must abstain from auto-creating."""
    if modelo is None:
        modelo = os.environ.get("HCMA_EMBED_MODEL", MODELO_DEFAULT)
    base = (modelo or "").lower().split("/")[-1]
    return base in MODELOS_MULTILINGUE


def _cargar_modelo(modelo):
    if modelo in _EMB_CACHE:
        return _EMB_CACHE[modelo]
    try:
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer(modelo)
    except Exception:
        m = None
    _EMB_CACHE[modelo] = m
    return m


# ── TF-IDF fallback (stdlib) ──────────────────────────────────────────────────
_STOP = set("""
a an and at by for from in into is it of on or that the this to with two
de del la el los las un una unos unas y o en por para con al se es son como entre sobre su sus lo
que cual cuales quien quienes cuando donde cuanto cuanta cuantos cuantas
mi tu mis tus nuestro nuestra nuestros nuestras me te le les nos os
ser estar tener haber era fue fueron son esta estaba estan estas estaban tiene tienen hay habia
""".split())


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def _tokens(s):
    return [t for t in re.findall(r"[a-z0-9]+", _norm(s)) if t not in _STOP]


def _aplanar(v):
    if isinstance(v, dict):
        for k, val in v.items():
            yield str(k)
            yield from _aplanar(val)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _aplanar(x)
    else:
        yield "" if v is None else str(v)


def card_texto(declaracion):
    if isinstance(declaracion, str):
        return declaracion
    return " ".join(_aplanar(declaracion))


class IndiceSkills:
    def __init__(self, tarjetas, modelo=MODELO_DEFAULT, version=VERSION):
        # tarjetas: {skill_id: text description}
        self.tarjetas = dict(tarjetas or {})
        self.modelo = modelo
        self._emb = _cargar_modelo(modelo) if modelo else None
        self._idf = {}
        self._vecs = {}
        self._construir()
        self.version = (self.modelo or "tfidf") + ":" + version
        self.hash = self._hash()

    def _construir(self):
        if self._emb is not None:
            ids = list(self.tarjetas.keys())
            if ids:
                embs = self._emb.encode([self.tarjetas[s] for s in ids],
                                        normalize_embeddings=True, convert_to_numpy=True)
                for sid, v in zip(ids, embs):
                    self._vecs[sid] = v
        else:
            # sparse TF-IDF
            docs = {sid: _tokens(txt) for sid, txt in self.tarjetas.items()}
            df = {}
            for toks in docs.values():
                for t in set(toks):
                    df[t] = df.get(t, 0) + 1
            n = max(1, len(docs))
            self._idf = {t: math.log((n + 1.0) / (df[t] + 1.0)) + 1.0 for t in df}
            for sid, toks in docs.items():
                tf = {}
                for t in toks:
                    tf[t] = tf.get(t, 0) + 1
                norm = math.sqrt(sum((v * self._idf[t]) ** 2 for t, v in tf.items())) or 1.0
                self._vecs[sid] = {t: (v * self._idf[t]) / norm for t, v in tf.items()}

    def encode(self, texto):
        if self._emb is not None:
            return self._emb.encode(texto, normalize_embeddings=True, convert_to_numpy=True)
        vec = {}
        for t in _tokens(texto):
            if t in self._idf:
                vec[t] = vec.get(t, 0) + self._idf[t]
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def get(self, skill_id):
        return self._vecs.get(skill_id)

    def top_k(self, texto, k=5):
        q = self.encode(texto)
        if self._emb is not None:
            out = [(sid, round(float(q @ v), 4)) for sid, v in self._vecs.items()]
        else:
            out = [(sid, round(sum(q.get(t, 0.0) * w for t, w in v.items()), 4))
                   for sid, v in self._vecs.items()]
        out.sort(key=lambda x: -x[1])
        return out[:k]

    @property
    def es_embeddings(self):
        return self._emb is not None

    def _hash(self):
        canon = json.dumps(self.tarjetas, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256((self.version + "|" + canon).encode("utf-8")).hexdigest()[:16]


def construir(m, modelo=MODELO_DEFAULT):
    """Builds the index from memory (canonical skills)."""
    import skills as SK
    tarjetas = {}
    for s in SK.list_skills(m):
        txt = card_texto(s.get("declaracion") or "")
        if txt.strip():
            tarjetas[s["id"]] = txt
    return IndiceSkills(tarjetas, modelo=modelo)
