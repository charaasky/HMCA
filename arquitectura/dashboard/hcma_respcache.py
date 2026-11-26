#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""hcma_respcache.py — advisory chat response cache (reuse, don't re-query the LLM).

Fable 5.2 opinion (fable_respuesta_cache_fable.md). An advisory LLM response
(`procedencia='llm_unverified'`) is stored as a CLAIMED fact `resp:<usuario>:<sha256(norm_q)>`
(source 'llm:<provider>/<model>', verified=0/authority=0, NEVER promotable) and is reused in
subsequent turns ONLY if:

  - the query is EXACT and normalized (P0; the semantic match is P1), and
  - the injected context did not change (`ctx_fp`), and
  - the provider/model did not change (`model_fp`).

It is NOT a verified fact nor an input to the deterministic routes (recall/alias/calculation/skill).
Stdlib only. Does not import memoria_sqlite nor skills/pint (memory_context does the DB read).
"""
import hashlib
import json
import re
import time
import unicodedata

# Size cap for a cacheable response (for storage, not correctness).
MAX_RESPUESTA_BYTES = 16384  # 16 KB

# Temporal deixis: turns that depend on "now" are not cached (they would age poorly).
_DEIXIS = {
    "hoy", "ahora", "actual", "actualmente", "ultimo", "ultimos", "ultima", "ultimas",
    "today", "now", "current", "currently", "latest", "last", "recent", "recently",
    "reciente", "recientemente", "esta", "este",
}

# Turns that are not a queryable question (greetings/confirmations/write orders).
_SIN_PREGUNTA = {
    "hola", "buenas", "gracias", "chau", "adios", "ok", "okey", "listo", "dale",
    "bien", "si", "no", "perfecto", "entendido", "sale", "va",
}


def normalizar(text):
    """Canonicalizes a query for an EXACT key: NFKC + casefold + collapse non-alphanumerics.

    The cache key is `sha256(normalizar(q))`: two strings equivalent in form collapse to the same
    key; a paraphrase does NOT (that is P1, semantic match with guards)."""
    s = unicodedata.normalize("NFKC", str(text or ""))
    s = s.casefold()
    s = "".join(c if c.isalnum() else " " for c in s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _sin_acentos(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c))


def _tokens(text):
    return set(re.findall(r"[a-z0-9]+", _sin_acentos(text).lower()))


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def key_for(usuario, norm_q):
    """Deterministic key of the cached response: `resp:<usuario>:<sha256(norm_q)>`.

    The scope is PER USER (the dashboard is multi-user/RBAC): A's response is never reused for B.
    Fable opinion: strict per-user scope in P0."""
    return "resp:%s:%s" % (str(usuario or ""), _sha(norm_q))


def ctx_fp(hechos):
    """Fingerprint of the INJECTED context: `sha256(sorted[(key, version)])` of verified and claimed.

    Covers the negative dependency: if a fact is DELETED or CHANGED (or a relevant new one appears),
    the current turn's fingerprint differs from the stored one and the cache is a miss. `""` = no context."""
    items = sorted({(str(f.get("key") or ""), str(f.get("version") or ""))
                    for f in (hechos or []) if f.get("key")})
    if not items:
        return ""
    return _sha(json.dumps(items, ensure_ascii=False))


def model_fp(provider, model):
    """Model fingerprint: `sha256(provider|model)[:16]`. If the provider/model changes => miss."""
    return _sha("%s|%s" % (str(provider or ""), str(model or "")))[:16]


def buscar(usuario, norm_q, memoria, ctx_fp_actual, model_fp_actual):
    """EXACT lookup by user. Returns {key, version, value, modelo, ts, meta} or None.

    `memoria` must expose `.current_raw(key)` (latest version WITHOUT filtering authority/verified).
    It is rejected if the fact became `stale` or if the context/model changed."""
    key = key_for(usuario, norm_q)
    f = memoria.current_raw(key)
    if f is None or f.get("stale"):
        return None
    meta = f.get("meta") or {}
    if meta.get("ctx_fp") != ctx_fp_actual:
        return None
    if meta.get("model_fp") != model_fp_actual:
        return None
    return {"key": key, "version": f.get("version"), "value": f.get("value"),
            "modelo": meta.get("modelo"), "ts": meta.get("ts"), "meta": meta}


def _es_negativa(content):
    """Do not cache an 'I don't know'/'I don't have' (it would freeze a false negative). ES/EN heuristic."""
    t = content.strip().lower()
    for m in ("no sé", "no se", "no lo sé", "no lo se", "no tengo", "no lo tengo",
              "no tengo información", "no tengo informacion", "i don't know", "i do not know",
              "i don't have", "i do not have", "i have no", "no lo se,"):
        if t.startswith(m):
            return True
    return False


def _es_deixis(q):
    return bool(_tokens(q) & _DEIXIS)


def _es_sin_pregunta(q):
    tok = _tokens(q)
    if len(tok) < 2:
        return True
    if len(tok) <= 2 and (tok & _SIN_PREGUNTA):
        return True
    return False


def elegible(body, resp, q):
    """Can this turn + response be cached? Returns (ok, razon).

    P0 rules (Fable opinion): self-contained turn (no history), no attachments, no explicit skill
    command, finished and non-empty response, not negative, no temporal deixis, not a
    greeting/order, and within the size cap."""
    msgs = body.get("messages") or []
    user_turns = [m for m in msgs if (m or {}).get("role") == "user"]
    if len(user_turns) != 1:
        return False, "historial multi-turno"
    if any((m or {}).get("role") == "assistant" for m in msgs):
        return False, "historial con turnos del asistente"
    if body.get("attachments"):
        return False, "con adjuntos"
    if body.get("skill") or body.get("crear_skill"):
        return False, "comando explícito de skill"

    choices = resp.get("choices") or []
    content = ""
    finish = ""
    if choices:
        msg = choices[0].get("message") or {}
        content = msg.get("content") or ""
        finish = choices[0].get("finish_reason") or ""
    if finish != "stop":
        return False, "respuesta no terminada (%s)" % finish
    if not content.strip():
        return False, "respuesta vacía"
    if len(content.encode("utf-8")) > MAX_RESPUESTA_BYTES:
        return False, "respuesta excede %d bytes" % MAX_RESPUESTA_BYTES
    if _es_negativa(content):
        return False, "respuesta negativa (no sé)"
    if _es_deixis(q):
        return False, "deixis temporal"
    if _es_sin_pregunta(q):
        return False, "no es una pregunta consultable"
    return True, ""


def candidato(usuario, q, hechos, provider, model, resp):
    """Builds the cache_candidate payload that `app._chat` enqueues into writeback_queue.

    `derived_from` ties to the injected facts (verified AND claimed) for inheritance-based deletion
    and stale invalidation when a cited fact changes."""
    norm_q = normalizar(q)
    content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    df = {}
    for f in (hechos or []):
        k = f.get("key")
        v = f.get("version")
        if not k or v is None:
            continue
        try:
            df[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return {
        "key": key_for(usuario, norm_q),
        "value": content,
        "usuario": str(usuario or ""),
        "modelo": str(model or ""),
        "ctx_fp": ctx_fp(hechos),
        "model_fp": model_fp(provider, model),
        "derived_from": df,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
