#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""memory_context.py — retrieves CURRENT facts from HCMA memory to inject them into the chat.

READ-ONLY retrieval (FTS5, only `authority=1` = current/canonical version) and formatting with
PROVENANCE (source) so the user sees where each datum came from, not an opaque answer.

Does NOT write the store (opens with read_only=True; the monitor is the single writer).
"""
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

MEMORY_DB = os.environ.get("HCMA_DASH_MEMORY_DB", "")


def retrieve(query, top_k=5):
    """Returns [{key, value, unit, fuente, score}] of current facts that match `query`.

    If no memory is configured or there is no match, returns []."""
    if not MEMORY_DB or not os.path.exists(MEMORY_DB) or not query:
        return []
    try:
        from memoria_sqlite import HCMAMemoriaSQLite
        m = HCMAMemoriaSQLite(MEMORY_DB, crear_esquema=False, read_only=True)
    except Exception:
        return []
    try:
        return m.buscar(query, top_k=top_k)
    except Exception:
        return []
    finally:
        m.close()


def _val(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else str(v)


def facts_payload(facts):
    """Shape for showing provenance to the user: {key, value, unit, fuente, score}."""
    return [{"key": f["key"], "value": _val(f.get("value")), "unit": f.get("unit") or "",
             "fuente": f.get("fuente") or "", "score": f.get("score")} for f in facts]


def context_block(facts):
    """Text block to inject into the LLM (with provenance).

    Goes as a SYSTEM message. The header is deliberately DIRECT: without it, aligned models respond
    "I don't have access to personal information" and ignore the facts, even though they are in the
    prompt. It clarifies these are the user's own data (not third parties') to avoid triggering
    refusals."""
    if not facts:
        return ""
    lines = [
        "Estos son hechos VERIFICADOS que el usuario ya te contó y están guardados en su memoria "
        "HCMA (son datos del propio usuario, no información privada de terceros). Usalos para "
        "responder de forma directa.",
        "Reglas anti-alucinación: (1) usá los términos EXACTOS de los hechos (nombres, apodos, "
        "valores) — NO los corrijas, traduzcas ni les cambies la ortografía; (2) NO agregues "
        "datos personales que no figuren en esta lista; (3) si la consulta pide algo que no está "
        "acá, decí que no lo sabés en vez de inventarlo.",
        "",
        "Hechos vigentes (con procedencia):",
    ]
    for f in facts:
        lines.append("- %s = %s [fuente: %s]" % (f["key"], _val(f.get("value")),
                                                 f.get("fuente") or "?"))
    return "\n".join(lines)


def claimed_block(facts):
    """Context block to inject CLAIMED (unverified) facts into the advisory LLM.

    Unlike `context_block`, it makes clear these are the user's unvalidated CLAIMS (tier=claimed), so
    the model does not treat them as canon nor propagate them as verified."""
    if not facts:
        return ""
    lines = [
        "El usuario te contó los siguientes hechos pero TODAVÍA NO están verificados en la "
        "memoria HCMA (son 'claims' pendientes de validación humana, no datos canónicos). "
        "Podés usarlos como contexto con la salvedad de que no están verificados.",
        "",
        "Claims del usuario (sin verificar):",
    ]
    for f in facts:
        lines.append("- %s = %s [fuente: %s]" % (f["key"], _val(f.get("value")),
                                                 f.get("fuente") or "?"))
    return "\n".join(lines)


def no_memory_block():
    """System message for when there are NO relevant facts in memory (general question).

    Only requires "I don't know" for PERSONAL data; for everything else (general knowledge, or about
    the assistant itself such as "what model are you") it lets the LLM answer normally, without
    guessing the user's personal data, and mark its response as unverified general knowledge."""
    return (
        "No hay hechos personales del usuario en la memoria para esta consulta. "
        "Si la consulta es sobre datos personales del usuario (su familia, mascotas, etc.), "
        "respondé que no lo sabés y NO adivines ni menciones otros datos personales de la "
        "conversación. Si es una pregunta general o sobre vos mismo (qué modelo sos, qué podés "
        "hacer), respondé normalmente y aclarale al final que es conocimiento general no "
        "verificado contra la memoria HCMA del usuario."
    )


def resolve(query, top_k=5):
    """Deterministic memory-first (recall gate): Decision {tipo, hechos, evidencia}.

    RECALL_PURO => the hippocampus responds ALONE, without calling the LLM. Opens memory read-only and
    delegates to hcma_recall.resolver. ALIAS resolution is via edges materialized at write time
    (`alias:<surface>`), not by parsing the value at read time. If no memory is configured, returns
    NO_HIT."""
    if not MEMORY_DB or not os.path.exists(MEMORY_DB) or not query:
        return {"tipo": "NO_HIT", "hechos": [], "evidencia": None}
    try:
        from hcma_recall import resolver as _resolver
        from memoria_sqlite import HCMAMemoriaSQLite
        m = HCMAMemoriaSQLite(MEMORY_DB, crear_esquema=False, read_only=True)
    except Exception:
        return {"tipo": "NO_HIT", "hechos": [], "evidencia": None}
    try:
        return _resolver(query, m, top_k=top_k)
    except Exception:
        return {"tipo": "NO_HIT", "hechos": [], "evidencia": None}
    finally:
        m.close()


def render_recall(decision):
    """Template response for RECALL_PURO (re-exports hcma_recall.render_recall)."""
    from hcma_recall import render_recall as _render
    return _render(decision)


def render_claimed(decision):
    """Template response for HIT_CLAIMED (re-exports hcma_recall.render_claimed)."""
    from hcma_recall import render_claimed as _render
    return _render(decision)


def render_held(facts):
    """Deterministic response for facts RETRIEVED but held by egress (not sent to the LLM). The user
    is the owner: local provenance is shown to them, without lying to the model.

    Fable opinion: 'there are no facts' and 'there are facts but I cannot send them' are distinct
    states; collapsing them into no_memory_block fabricates the denial."""
    partes = [f"{f['key']} = {_val(f.get('value'))}" for f in facts]
    fuentes = sorted({f.get("fuente") for f in facts if f.get("fuente")})
    base = "En tu memoria HCMA tengo (no lo envío al modelo por privacidad): " + "; ".join(partes)
    if fuentes:
        base += " [fuente: " + ", ".join(fuentes) + "]"
    return base


def _raw_words(text):
    import slug_canon
    return sorted(slug_canon.palabras(text))


def resolve_alias(query, decision=None, k=8):
    """ENTITY resolution via alias edges (hop 1) + closed-world block (hop 2).

    Fable 5.2 opinion (fable_propiedad_llm_fable.md): the ENTITY is PRIVATE knowledge and is resolved
    deterministically via `alias:<surface>` edges; the PROPERTY is PUBLIC knowledge (synonyms like
    'llamaba'≈'nombre' in all languages) and is selected by the consuming LLM.

    Hop 1 (deterministic): looks up `alias:<surface>` whose surfaces appear in the query. Verified
    beats claimed; two different targets at the same tier => None (unresolved entity).

    Hop 2 (deterministic evidence + SEMANTIC selection): if the entity resolves, returns a BLOCK with
    ALL current verified facts of the entity (ordered by key, literal hit first, K cap). It does NOT
    abstain for lack of a literal match and does NOT query `prop:*`.

    Returns None (unresolved entity / no memory) or:
      {"tipo": "entity_block", "entidad": target, "surface": surface,
       "facts": [{key, value, unit, fuente, prop, version}...], "complete": bool, "total": int}
    """
    if not MEMORY_DB or not os.path.exists(MEMORY_DB):
        return None
    if decision is not None and decision.get("tipo") not in (
            "HIT_CONTEXTUAL", "NO_HIT", "HIT_CLAIMED"):
        return None
    q_words = sorted(set(_raw_words(query)))
    if not q_words:
        return None
    try:
        from memoria_sqlite import HCMAMemoriaSQLite
        m = HCMAMemoriaSQLite(MEMORY_DB, crear_esquema=False, read_only=True)
    except Exception:
        return None
    try:
        found = []  # (surface, target, tier)
        for s in q_words:
            t = m.alias_target(s)
            if t is not None:
                found.append((s, t[0], t[1]))
        if not found:
            return None
        # verified beats claimed
        if any(f[2] == "verified" for f in found):
            found = [f for f in found if f[2] == "verified"]
        # tie rule: different targets at the same tier => unresolved entity
        targets = {f[1] for f in found}
        if len(targets) > 1:
            return None
        surface, target, _tier = found[0]
        facts = m.facts_entidad(target)
        # The literal ORDERS, it does not decide: literal hit first, then the rest by key.
        q_set = set(q_words)
        hits = [f for f in facts if f.get("prop") in q_set]
        ordered = hits + [f for f in facts if f not in hits]
        top = ordered[:k]
        return {
            "tipo": "entity_block",
            "entidad": target,
            "surface": surface,
            "facts": top,
            "complete": len(facts) <= k,
            "total": len(facts),
        }
    finally:
        m.close()


def entity_block(res, facts=None):
    """CLOSED-WORLD block (alias hop 2, fable_propiedad_llm_fable.md opinion).

    Injects the current verified facts of the ENTITY (with K cap) and declares completeness: the
    property SELECTION is the LLM's (advisory), but the evidence is verified and closed. If `complete`
    is true, the LLM can read absence as 'not registered' instead of filling in.

    The answer is never labeled verified: the mapping (which fact answers the question) is advisory,
    the provenance of the evidence remains verified."""
    entidad = res.get("entidad")
    surface = res.get("surface")
    completo = bool(res.get("complete"))
    facts = facts if facts is not None else (res.get("facts") or [])
    lines = ["[MEMORIA verified · entidad=%s (alias: %s) · completo=%s]"
             % (entidad, surface, "true" if completo else "false")]
    for f in facts:
        lines.append("%s = %s" % (f["key"], _val(f.get("value"))))
    if completo:
        lines.append("[FIN · Son TODOS los hechos de esta entidad. Si lo pedido no está en esta "
                     "lista, decí que no está registrado y citá la clave que usaste.]")
    else:
        lines.append("[FIN · Hechos de esta entidad (TRUNCADO: hay más). Citá la clave que usaste; "
                     "si lo pedido no está acá, aclaralo en vez de inventarlo.]")
    return "\n".join(lines)


def validate_used_key(used_key, res):
    """P1 Fable (fable_propiedad_llm_fable.md): validates that the key cited by the LLM is in the
    entity block. None, empty, or outside the block => invalid (the answer cannot assert an absent
    property as verified)."""
    if not used_key or not isinstance(used_key, str):
        return False
    used_key = used_key.strip()
    if not used_key:
        return False
    keys = {f["key"] for f in (res.get("facts") or [])}
    return used_key in keys


def superficie_alias_legacy(facts):
    """P1.1 Fable: among the retrieved facts, returns the bare KEY (without ':') whose value looks like
    a legacy alias ('apodo/alias/sobrenombre of E') and that did NOT resolve via an edge. If none,
    None. Used to make the alias miss NOISY (not silent)."""
    import alias_backfill as AB
    for f in facts or []:
        key = f.get("key") or ""
        val = f.get("value")
        if ":" not in key and isinstance(val, str):
            if AB.clasificar_alias(key, val) is not None:
                return key
    return None


def resolve_skill(query):
    """Queries the skill router (cascade, metacognition) to decide WHICH tool to use.

    Returns the router output: ("usar", skill_id, nivel) | ("sugerir", skill_id, nivel) |
    ("crear", contexto, razon) | ("abstener", razon[, top]), or None if there is no memory/router.
    Injects the semantic index (indice_skills) for top-k retrieval (layer 3)."""
    if not MEMORY_DB or not os.path.exists(MEMORY_DB) or not query:
        return None
    try:
        from memoria_sqlite import HCMAMemoriaSQLite
        import router_skills as RS
        import indice_skills as IDX
        m = HCMAMemoriaSQLite(MEMORY_DB, crear_esquema=False, read_only=True)
    except Exception:
        return None
    try:
        indice = IDX.construir(m)
        return RS.elegir_skill(m, query, indice=indice)
    except Exception:
        return None
    finally:
        m.close()


def skill_block(skill_id, nivel):
    """Context block to inject the selected skill's spec (with lineage)."""
    if not MEMORY_DB or not os.path.exists(MEMORY_DB):
        return None
    try:
        from memoria_sqlite import HCMAMemoriaSQLite
        m = HCMAMemoriaSQLite(MEMORY_DB, crear_esquema=False, read_only=True)
    except Exception:
        return None
    try:
        f = m.current("skill:%s:spec" % skill_id) or m.current("skill:" + skill_id)
        if f is None:
            return None
        desc = _val(f.get("value"))
        return ("El agente va a usar el skill '%s' (seleccionado por %s). Su contrato:\n%s\n"
                "Resolvé usando esta spec; no inventes una fórmula distinta."
                % (skill_id, nivel, desc))
    finally:
        m.close()


def ejecutar_skill_route(skill_id):
    """P0-C: if the skill has a DECLARATIVE spec {inputs, formulas}, EXECUTES it deterministically
    (llm_gateway.ejecutar_skill) instead of delegating to the LLM. Returns the result of
    ejecutar_skill or None if the skill is not declarative (then the chat falls back to advisory with
    the spec injected)."""
    if not MEMORY_DB or not os.path.exists(MEMORY_DB):
        return None
    try:
        from memoria_sqlite import HCMAMemoriaSQLite
        import llm_gateway as LG
        m = HCMAMemoriaSQLite(MEMORY_DB, crear_esquema=False, read_only=True)
    except Exception:
        return None
    try:
        f = m.current("skill:%s:spec" % skill_id) or m.current("skill:" + skill_id)
        if f is None:
            return None
        v = f.get("value")
        if not isinstance(v, dict) or "inputs" not in v or "formulas" not in v:
            return None
        return LG.ejecutar_skill(skill_id, v, memoria=m)
    finally:
        m.close()


def normalizar(query):
    """Re-exports hcma_respcache.normalizar (normalized EXACT key of the turn)."""
    import hcma_respcache as RC
    return RC.normalizar(query)


def ctx_fp(hechos):
    """Re-exports hcma_respcache.ctx_fp (fingerprint of the injected context)."""
    import hcma_respcache as RC
    return RC.ctx_fp(hechos)


def model_fp(provider, model):
    """Re-exports hcma_respcache.model_fp (provider|model fingerprint)."""
    import hcma_respcache as RC
    return RC.model_fp(provider, model)


def resolve_respuesta(usuario, query, ctx_fp_actual, model_fp_actual):
    """Lookup of cached response (previous advisory from the SAME user, context and model).

    READ-ONLY (like retrieve/resolve): opens memory read-only and delegates to hcma_respcache.buscar.
    Returns {key, version, value, modelo, ts, meta} or None."""
    if not MEMORY_DB or not os.path.exists(MEMORY_DB) or not usuario or not query:
        return None
    try:
        import hcma_respcache as RC
        from memoria_sqlite import HCMAMemoriaSQLite
        m = HCMAMemoriaSQLite(MEMORY_DB, crear_esquema=False, read_only=True)
    except Exception:
        return None
    try:
        return RC.buscar(usuario, RC.normalizar(query), m, ctx_fp_actual, model_fp_actual)
    except Exception:
        return None
    finally:
        m.close()


def buscar_chunks(query, top_k=5):
    """Retrieves cortex CHUNKS (long mapped books/documents) by CONTENT. Read-only."""
    if not MEMORY_DB or not os.path.exists(MEMORY_DB) or not query:
        return []
    try:
        from memoria_sqlite import HCMAMemoriaSQLite
        m = HCMAMemoriaSQLite(MEMORY_DB, crear_esquema=False, read_only=True)
    except Exception:
        return []
    try:
        return m.buscar_chunks(query, top_k=top_k)
    except Exception:
        return []
    finally:
        m.close()


def buscar_chunk_posicion(page=None, para=None, doc_sha=None):
    """POSITIONAL lookup of a chunk (page/paragraph). Read-only. Returns the chunk or None."""
    if not MEMORY_DB or not os.path.exists(MEMORY_DB):
        return None
    try:
        from memoria_sqlite import HCMAMemoriaSQLite
        m = HCMAMemoriaSQLite(MEMORY_DB, crear_esquema=False, read_only=True)
    except Exception:
        return None
    try:
        return m.buscar_chunk_posicion(page=page, para=para, doc_sha=doc_sha)
    except Exception:
        return None
    finally:
        m.close()


def buscar_chunk_por_ref(ref, doc_sha=None):
    """Lookup of a chunk by its REFERENCE ('Juan 3:16'). Read-only. Returns the chunk or None."""
    if not MEMORY_DB or not os.path.exists(MEMORY_DB) or not ref:
        return None
    try:
        from memoria_sqlite import HCMAMemoriaSQLite
        m = HCMAMemoriaSQLite(MEMORY_DB, crear_esquema=False, read_only=True)
    except Exception:
        return None
    try:
        return m.buscar_chunk_por_ref(ref, doc_sha=doc_sha)
    except Exception:
        return None
    finally:
        m.close()


_CAP_VER_RE = re.compile(r"\b(\d+\s*:\s*\d+)\b")


def cap_vers_en_query(query):
    """Extracts the 'Cap:Vers' core from a query ('traeme Juan 3:16' -> '3:16'), or None.

    NUMERIC pattern (language-independent), not a lexicon: recognizes the standard reference form
    'book chapter:verse' in any language."""
    m = _CAP_VER_RE.search(str(query or ""))
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1))


def buscar_chunk_por_cap_vers(cap_vers, doc_sha=None):
    """Lookup of a chunk by 'Cap:Vers' (ignoring the book). Read-only."""
    if not MEMORY_DB or not os.path.exists(MEMORY_DB) or not cap_vers:
        return None
    try:
        from memoria_sqlite import HCMAMemoriaSQLite
        m = HCMAMemoriaSQLite(MEMORY_DB, crear_esquema=False, read_only=True)
    except Exception:
        return None
    try:
        return m.buscar_chunk_por_cap_vers(cap_vers, doc_sha=doc_sha)
    except Exception:
        return None
    finally:
        m.close()


def render_chunk(chunk):
    """Deterministic render of a cortex chunk (verse/section), with its reference."""
    meta = (chunk or {}).get("meta") or {}
    ref = meta.get("ref") or ""
    val = str((chunk or {}).get("value") or "")
    return (f"[{ref}] {val}") if ref else val


def chunks_block(chunks):
    """Context block to inject cortex CHUNKS into the LLM (advisory, external knowledge)."""
    if not chunks:
        return ""
    lines = [
        "Fragmentos del documento/libro (córtex: conocimiento externo, NO es un hecho verificado "
        "del usuario). Usalos para responder citando el fragmento.",
        "",
    ]
    for c in chunks:
        meta = c.get("meta") or {}
        loc = []
        if meta.get("page"):
            loc.append(f"página {meta['page']}")
        if meta.get("para"):
            loc.append(f"párrafo {meta['para']}")
        lines.append(f"--- [{', '.join(loc) or 'córtex'}] ---")
        lines.append(str(c.get("value") or ""))
        lines.append("")
    return "\n".join(lines)
