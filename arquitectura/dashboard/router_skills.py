#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Skill router — optimal and cheap cascade selection (with metacognition).

The router decides, given a context, which skill to use. It prioritizes the CHEAPEST and most
distribution-shift-robust signal (usage with retractable lineage), and only escalates to more
expensive signals (similarity, LLM) when the cheap one is insufficient. Abstention and skill
creation are FIRST-CLASS outputs (not failed fallbacks).

Cascade (each level with its cost and its output):

  Level 0 (does it exist?) : if there is no skill for the context → CREATE (unverified candidate)
  Level 1 (usage)          : skills with non-stale usage → 1 → use it; n>1 → tie-break by cost
  Level 2 (similarity)     : exists but without usage (cold-start) → high similarity → use it
  Level 3 (abstention)     : no reliable signal → ABSTAIN (metacognition)

The point of the paper: the usage signal (O(1), already in HCMA) knows WHEN there is no signal and
abstains or creates, instead of spending on similarity/LLM that fail under drift (see E8:
similarity = chance).

Confidence is inherited via lineage: a skill is only canonical if its `derived_from` points to
`verified` facts (mixed validation option). Creation produces an `unverified` candidate visible in
the dashboard, which self-promotes only when the lineage is solid.
"""
try:
    import skills as _sk
    from memoria_sqlite import HCMAMemoriaSQLite  # noqa: F401 (referencia de tipo)
except ImportError:
    _sk = None

# Router outputs (readable tuples, not loose strings)
#   ("usar", skill_id, nivel)      -> level in {"uso","costo","similitud","explicito"}
#   ("sugerir", skill_id, nivel)   -> skill_sugerido: the UI shows a chip; does NOT execute (gray band)
#   ("crear", contexto, razon)     -> ONLY via explicit signal (not auto-reached)
#   ("abstener", razon[, top])     -> no reliable signal

# Semantic similarity thresholds (τ_alto / τ_bajo). Initial; calibrated with the golden set.
# With EMBEDDINGS (sentence-transformers) the cosine separates better: a real semantic match
# (synonyms like 'lampara'->'luminaria') gives ~0.5-0.7; noise/irrelevant gives <0.2. τ_alto marks
# use; τ_bajo filters the noise (e.g. 'ledger' with an embedding close to 'led').
UMBRAL_SIMILITUD = 0.5   # legacy (cold-start by similarity without index)
UMBRAL_ALTO = 0.40       # τ_alto: use on cold-start
UMBRAL_BAJO = 0.25       # τ_bajo: suggest (gray band; in P1 the judge decides)


def _usos_no_stale(m):
    """View {skill_id: nº of non-stale uses}, reusing skills.py if available."""
    if _sk is not None:
        return _sk._usos_por_id(m)
    # direct fallback in case skills.py is not importable
    out = {}
    for r in m.conn.execute("SELECT key, value FROM facts WHERE key LIKE ? AND stale=0",
                            ("skill_uso:%",)).fetchall():
        sid = r["key"][len("skill_uso:"):].rpartition(":")[0]
        try:
            out[sid] = out.get(sid, 0) + int(r["value"])
        except (TypeError, ValueError):
            pass
    return out


def _skill_ids(m):
    """List of skill ids (canonical: verified ∧ authority ∧ ¬stale)."""
    c = m.conn
    out = []
    for r in c.execute(
            "SELECT key FROM facts WHERE key LIKE ? AND key NOT LIKE ? "
            "AND authority=1 AND verified=1 AND stale=0",
            ("skill:%", "skill_uso:%")).fetchall():
        sid = r["key"][len("skill:"):]
        # P1 Fable: quarantine of the degenerate 'skill' id (the pre-multi-language collapse mixed
        # different skills under the SAME id). We do not serve it as a canonical skill.
        if sid == "skill":
            continue
        out.append(sid)
    return out


def _match(contexto, skill_ids):
    """Matches a context to skills by simple textual token overlap.

    Deterministic and cheap (no embeddings). Used for Level 1: filter skills whose id shares tokens
    with the context. Intentionally crude: fine similarity is Level 2.
    """
    ctx = _tokens(contexto)
    if not ctx:
        return []
    out = []
    for sid in skill_ids:
        st = _tokens(sid)
        if ctx & st:
            out.append(sid)
    return out


def _tokens(s):
    import re
    s = (s or "").lower()
    return set(re.findall(r"[a-z0-9]+", s))


def _similitud(contexto, skill_id, indice):
    """Similarity [0,1] between context and skill.

    `indice` can be:
      - an object with .encode(texto) and .get(skill_id) -> vector (embeddings or sparse TF-IDF), or
      - a dict {skill_id: description} (crude token proxy).

    Automatically detects whether the vectors are numpy (dense cosine) or dict (sparse cosine).
    """
    if indice is None:
        return 0.0
    if hasattr(indice, "encode") and hasattr(indice, "get"):
        ctx = indice.encode(contexto)
        sk = indice.get(skill_id)
        if ctx is None or sk is None:
            return 0.0
        return _cosine(ctx, sk)
    # dict {skill_id: description} → token proxy (Jaccard)
    v = indice.get(skill_id)
    if v is None:
        return 0.0
    a, b = _tokens(contexto), _tokens(str(v))
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _cosine(a, b):
    """Cosine similarity between two vectors: numpy array (dense) or dict (sparse)."""
    import math
    if isinstance(a, dict) or isinstance(b, dict):
        # sparse cosine: sum(a[t]*b[t]) / (||a|| * ||b||)
        if not isinstance(a, dict):
            a = {str(i): float(v) for i, v in enumerate(a)}
        if not isinstance(b, dict):
            b = {str(i): float(v) for i, v in enumerate(b)}
        dot = sum(float(a.get(t, 0.0)) * float(b.get(t, 0.0)) for t in set(a) | set(b))
        na = math.sqrt(sum(float(x) ** 2 for x in a.values())) or 1.0
        nb = math.sqrt(sum(float(x) ** 2 for x in b.values())) or 1.0
        return dot / (na * nb)
    import numpy as _np
    a = _np.asarray(a, dtype=float).reshape(1, -1)
    b = _np.asarray(b, dtype=float).reshape(1, -1)
    na = a / (_np.linalg.norm(a) + 1e-9)
    nb = b / (_np.linalg.norm(b) + 1e-9)
    return float((na @ nb.T)[0, 0])


def elegir_skill(m, contexto, indice=None, tau_alto=None, tau_bajo=None):
    """Cascade router. Returns an output tuple (see above).

    - `m`        : HCMAMemoriaSQLite connection (or HCMAMemoria in RAM with .conn; see below).
    - `contexto` : textual description of the action the human is asking for.
    - `indice`   : IndiceSkills (top_k + encode + get) or mapping {skill_id: description}.
    - `tau_alto`/`tau_bajo`: optional thresholds (for CALIBRATION); if None uses the defaults.

    Order (Fable 5.2 opinion):
      * with index: top-k cosine; non-stale usage wins (real evidence) -> use;
        cold-start: score >= τ_alto -> use; τ_bajo..τ_alto -> suggest; < τ_bajo -> abstain.
      * without index: usage by token-match only; no signal -> ABSTAIN (never CREATE).
    """
    ta = UMBRAL_ALTO if tau_alto is None else tau_alto
    tb = UMBRAL_BAJO if tau_bajo is None else tau_bajo
    skill_ids = set(_skill_ids(m))
    usos = _usos_no_stale(m)

    # ── with semantic index: top-k retrieval (layer 3, deterministic ML) ──
    if indice is not None and hasattr(indice, "top_k"):
        top = [(sid, s) for sid, s in indice.top_k(contexto, k=5) if sid in skill_ids]
        con_uso = [(sid, s) for sid, s in top if usos.get(sid, 0) > 0]
        if con_uso:
            mejor = max(con_uso, key=lambda x: x[1])
            return ("usar", mejor[0], "uso")
        if top:
            sid, s = top[0]
            if s >= ta:
                return ("usar", sid, "similitud")
            if s >= tb:
                return ("sugerir", sid, "similitud")
        return ("abstener", "sin cobertura semántica confiable", [t[0] for t in top[:3]])

    # ── without index: cheap signals only (usage by token-match) ──
    match = [sid for sid in _match(contexto, skill_ids)]
    if not match:
        return ("abstener", "sin índice semántico ni skill que matchee por tokens")
    con_uso = [sid for sid in match if usos.get(sid, 0) > 0]
    if len(con_uso) == 1:
        return ("usar", con_uso[0], "uso")
    if len(con_uso) > 1:
        return ("usar", min(con_uso, key=lambda sid: _costo(m, sid)), "costo")
    return ("abstener", "sin uso ni índice semántico")


def _costo(m, skill_id):
    if _sk is not None:
        return _sk.costo_skill(m, skill_id)
    return 0


def _deduplicar(m, contexto, skill_ids, indice):
    """Deduplication: given a context that did NOT match by id, looks for an existing skill whose
    DESCRIPTION (not the id) has high semantic similarity to the context. If found, returns its id
    (to reuse it); otherwise returns None (a new one is created).

    `indice` must provide the description/embedding of each skill (same object as level 2).
    If there is no index, semantic deduplication is impossible → None.
    """
    if indice is None or not skill_ids:
        return None
    mejor = None
    mejor_sim = 0.0
    for sid in skill_ids:
        s = _similitud(contexto, sid, indice)
        if s > mejor_sim:
            mejor_sim = s
            mejor = sid
    if mejor is not None and mejor_sim >= UMBRAL_SIMILITUD:
        return mejor
    return None


def crear_skill(m, contexto, valor=None, fuente="ingeniero", derived_from=None,
                costo=0):
    """Creates a candidate (unverified) skill for a context without a skill.

    Drafts the candidate and writes it as `skill:<id>` with `verified=False` (unless `derived_from`
    points to `verified` facts and `fuente` is trusted → m.write promotes it, mixed option). The id
    is derived from the context (deterministic slug). Returns the version.

    In production, `valor` (the description/formula) is produced by the LLM or extracted from the
    cortex; here it is parameterized so the experiment is deterministic."""
    import slug_canon
    sid = _slug(contexto)
    if not sid:
        raise ValueError("contexto sin contenido identificable para acuñar un id de skill")
    meta = {"tipo": "skill", "costo": costo,
            "slug_algo": slug_canon.SLUG_ALGO,
            "slug_origen": "hash" if sid.startswith("sk_") else "legacy"}
    if _sk is not None:
        return _sk.registro(m, sid, valor or contexto, fuente=fuente,
                            verified=False, meta=meta,
                            derived_from=derived_from or {})
    # direct fallback
    return m.write("skill:" + sid, valor or contexto, fuente=fuente,
                   verified=False, meta=meta, derived_from=derived_from or {})


def _slug(s):
    import slug_canon
    return slug_canon.slug(s)
