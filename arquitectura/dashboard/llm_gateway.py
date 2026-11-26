#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""llm_gateway.py — OpenAI-compatible gateway exposing the LLM configured in the panel.

Lets Zed, VSCode (OpenAI-compatible extension) or any client talk to the configured model
(OpenAI/DeepSeek/Kimi/OpenRouter/local/Anthropic) by pointing at:
    {base}/v1/chat/completions   and   {base}/v1/models

Internally:
  - Forwards to the OpenAI-compatible provider (`/chat/completions`) or, if Anthropic, translates
    the OpenAI <-> Anthropic format (`/v1/messages`).
  - Always returns the OpenAI-compatible shape (`chat.completion`) to the client.

Not a blind proxy: the gateway auth is its own key (gateway_key), distinct from the provider's API
key, so it can be revoked/regenerated without touching the provider's key.

Stdlib only + cryptography (via llm_config). Does not import prototipo (to avoid dragging in
pint/skills).
"""
import hashlib
import json
import os
import re
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import llm_config


class GatewayError(Exception):
    """Gateway error with HTTP status to return to the OpenAI-compatible client."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status
        self.message = message


def _conn():
    c = llm_config.load()
    base = (c.get("base_url") or "https://openrouter.ai/api/v1").rstrip("/")
    key = c.get("api_key") or ""
    provider = c.get("provider") or "openrouter"
    return base, key, provider


def _is_anthropic(base, provider):
    return provider == "anthropic" or "anthropic.com" in base


def _is_google(base, provider):
    return provider == "google" or "generativelanguage.googleapis.com" in base


def _provider_is_local(base):
    """Classifies a provider as LOCAL (loopback/socket) or EXTERNAL, by its base_url.

    P0-D (egress): local receives everything; external has an egress policy. The classification is
    derived from the host (not from an editable flag), so that 'localhost' marked as remote does not
    slip through."""
    b = (base or "").strip().lower()
    host = ""
    try:
        host = urllib.parse.urlparse(b).hostname or ""
    except Exception:
        host = ""
    if host in ("", "127.0.0.1", "localhost", "::1", "0.0.0.0"):
        return True
    return host.endswith(".local") or host.endswith(".internal")


def _fact_sens(fact):
    """Sensitivity of a fact: public | personal | secret. Chat/user is personal by default; meta.sens
    overrides it (e.g. 'secret')."""
    meta = (fact or {}).get("meta") or {}
    if meta.get("sens"):
        return str(meta["sens"]).lower()
    fuente = (fact or {}).get("fuente") or ""
    if fuente == "usuario" or fuente.startswith("chat:"):
        return "personal"
    return "public"


def _egress_allow_personal_external():
    """Inject PERSONAL facts into the EXTERNAL provider?

    The 'usuario'/'chat:' facts are the user's OWN data; the chat already warns that the prompt goes to
    a third party. By default they are injected (opt-out); an operator wanting strict privacy sets
    HCMA_EGRESS_ALLOW_PERSONAL_EXTERNAL=0. 'secret' (meta.sens) NEVER goes to external."""
    v = os.environ.get("HCMA_EGRESS_ALLOW_PERSONAL_EXTERNAL", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _egress_facts(facts, base, provider):
    """Filters facts by egress policy (P0-D). Returns those that CAN go out to the provider.

    local: everything passes. external: public always, personal by default (opt-out), secret never."""
    local = _provider_is_local(base)
    allow_personal_ext = _egress_allow_personal_external()
    out = []
    for f in facts or []:
        sens = _fact_sens(f)
        if sens == "secret":
            if local:
                out.append(f)
        elif sens == "personal":
            if local or allow_personal_ext:
                out.append(f)
        else:
            out.append(f)  # public
    return out


def _origin(url):
    """Normalized origin (scheme://host[:port]) of a URL, or '' if not parseable."""
    if not url:
        return ""
    try:
        p = urllib.parse.urlparse(str(url))
    except Exception:
        return ""
    if not p.hostname:
        return ""
    port = ":%s" % p.port if p.port else ""
    return "%s://%s%s" % (p.scheme.lower(), p.hostname.lower(), port)


def _scheme_allowed(url):
    """https only; http only for loopback/.local/.internal (local provider)."""
    try:
        p = urllib.parse.urlparse(str(url))
    except Exception:
        return False
    if p.scheme == "https":
        return True
    if p.scheme == "http":
        host = (p.hostname or "").lower()
        return host in ("", "127.0.0.1", "localhost", "::1", "0.0.0.0") \
            or host.endswith(".local") or host.endswith(".internal")
    return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Does not follow HTTP redirects: avoids forwarding Authorization to another host on a 30x."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener():
    return urllib.request.build_opener(_NoRedirect())


_PRESET_ORIGIN = {
    "openrouter": "https://openrouter.ai",
    "google": "https://generativelanguage.googleapis.com",
    "groq": "https://api.groq.com",
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "deepseek": "https://api.deepseek.com",
    "kimi": "https://api.moonshot.ai",
}


def conn_for(provider=None, base_url=None, api_key=None):
    """Resolves (base, key, provider) for a connection, with the ANTI-LEAK key rule.

    - If `api_key` is provided (new key from the form), it is used directly.
    - Otherwise, the provider's saved key is REUSED ONLY if the origin of `base_url` matches the saved
      origin FOR THAT provider (not the global base_url). Old keys without origin fall back to a known
      preset. If the user points at a different host, the saved key does NOT travel.
    """
    cfg = llm_config.load()
    prov = provider or cfg.get("provider") or "openrouter"
    base = (base_url or cfg.get("base_url") or "https://openrouter.ai/api/v1").rstrip("/")
    key = (api_key or "").strip()
    if not key:
        saved_origin = llm_config.api_key_origin_for(prov)
        if not saved_origin and prov in _PRESET_ORIGIN:
            saved_origin = _origin(_PRESET_ORIGIN[prov])
        if saved_origin and _origin(base) == saved_origin:
            key = llm_config.api_key_for(prov) or ""
    return base, key, prov


def _post(url, headers, body):
    if not _scheme_allowed(url):
        raise GatewayError("URL del proveedor no permitida (solo https, o http local)", status=400)
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with _opener().open(req, timeout=600) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:500]
        except Exception:
            detail = ""
        raise GatewayError("proveedor: HTTP %d %s%s" % (e.code, e.reason,
                                                        (" — " + detail) if detail else ""),
                           status=502)
    except urllib.error.URLError as e:
        raise GatewayError("proveedor: %s" % e.reason, status=502)


def _forward_openai(base, key, body):
    url = base + "/chat/completions"
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if "openrouter.ai" in base:
        headers["HTTP-Referer"] = "https://osf.io/vqx95"
        headers["X-Title"] = "HCMA gateway"
    return _post(url, headers, body)


def _forward_anthropic(base, key, body):
    """Translates an OpenAI-compatible request to Anthropic Messages and forwards it."""
    system = None
    messages = []
    for m in body.get("messages", []):
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            system = content
        elif role in ("user", "assistant"):
            messages.append({"role": role, "content": content})
        # "tool"/"function" roles are flattened to user (best effort)
        else:
            messages.append({"role": "user", "content": str(content)})
    abody = {
        "model": body.get("model"),
        "max_tokens": body.get("max_tokens") or 4096,
        "messages": messages,
    }
    if system:
        abody["system"] = system
    if body.get("temperature") is not None:
        abody["temperature"] = body["temperature"]
    url = base + "/messages"
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "Content-Type": "application/json"}
    return _post(url, headers, abody)


def _from_openai(raw, model):
    choice = (raw.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content")
    if content is None:
        content = msg.get("reasoning") or ""
    message = {"role": msg.get("role", "assistant"), "content": content}
    # reasoning (OpenRouter): preserve the model's reasoning so it can be continued in the next
    # turn (the client must forward reasoning_details verbatim).
    for k in ("reasoning", "reasoning_details"):
        if msg.get(k) is not None:
            message[k] = msg[k]
    return {
        "id": raw.get("id") or ("chatcmpl-" + uuid.uuid4().hex[:24]),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": raw.get("model") or model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": choice.get("finish_reason") or "stop",
        }],
        "usage": raw.get("usage") or {},
    }


def _from_anthropic(raw, model):
    text = "".join(b.get("text", "") for b in raw.get("content", [])
                   if isinstance(b, dict) and b.get("type") == "text")
    u = raw.get("usage") or {}
    it = u.get("input_tokens", 0)
    ot = u.get("output_tokens", 0)
    return {
        "id": raw.get("id") or ("chatcmpl-" + uuid.uuid4().hex[:24]),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": raw.get("model") or model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": it, "completion_tokens": ot, "total_tokens": it + ot},
    }


def _forward_google(base, key, body):
    """Translates OpenAI-compatible -> Google Gemini `generateContent` (v1beta)."""
    model = body.get("model") or ""
    url = (base or "").rstrip("/") + "/models/" + urllib.parse.quote(model, safe="") + ":generateContent"
    system = None
    contents = []
    for m in body.get("messages", []):
        role = m.get("role")
        content = m.get("content", "")
        if isinstance(content, list):
            # multipart (images): for text chat we only extract the texts
            content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
        if role == "system":
            system = content
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": content}]})
        else:
            contents.append({"role": "user", "parts": [{"text": content}]})
    gen = {}
    if body.get("temperature") is not None:
        gen["temperature"] = body["temperature"]
    if body.get("max_tokens"):
        gen["maxOutputTokens"] = body["max_tokens"]
    gbody: dict = {"contents": contents}
    if system:
        gbody["systemInstruction"] = {"parts": [{"text": system}]}
    if gen:
        gbody["generationConfig"] = gen
    url += "?key=" + urllib.parse.quote(key, safe="")
    headers = {"Content-Type": "application/json"}
    return _post(url, headers, gbody)


def _from_google(raw, model):
    candidates = raw.get("candidates") or []
    text = ""
    if candidates:
        c = candidates[0]
        content = c.get("content") or {}
        parts = content.get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    u = raw.get("usageMetadata") or {}
    it = u.get("promptTokenCount", 0)
    ot = u.get("candidatesTokenCount", 0)
    return {
        "id": "chatcmpl-hcma-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": raw.get("model") or model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": it, "completion_tokens": ot, "total_tokens": it + ot},
    }


def _last_user_text(body, window=1):
    """Builds the retrieval query with the user's LAST message.

    window=1 on purpose: joining previous turns contaminates recall (e.g. 'y quien era mi guiya?'
    preceded by 'quien es mi vecino?' -> 'vecino' residue -> HIT_CONTEXTUAL instead of RECALL_PURO ->
    the LLM hallucinates). Memory already resolves context via FTS + overlap by VALUE."""
    msgs = body.get("messages") or []
    textos = []
    for m in reversed(msgs):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                textos.append(c)
            elif isinstance(c, list):
                textos.append(" ".join(str(x.get("text", "")) for x in c if isinstance(x, dict)))
            if len(textos) >= window:
                break
    return " ".join(reversed(textos))


def _inject_memory(body, ctx):
    """Injects the memory block as a system message (or prepends it to the existing system)."""
    b = dict(body)
    msgs = [dict(m) for m in (b.get("messages") or [])]
    if msgs and msgs[0].get("role") == "system":
        prev = str(msgs[0].get("content", ""))
        msgs[0] = {"role": "system", "content": ctx + ("\n\n" + prev if prev else "")}
    else:
        msgs.insert(0, {"role": "system", "content": ctx})
    b["messages"] = msgs
    return b


def _attachment_block(attachments):
    """Attached document (PDF) context to inject into the LLM. NOT HCMA memory.

    The text already comes extracted by the dashboard (pdftotext); here it is only packaged as a system
    message with the document name so the model cites the source."""
    if not attachments:
        return ""
    lines = [
        "El usuario adjuntó los siguientes documentos (texto extraído del PDF). Usá su contenido "
        "para responder, citando el nombre del documento cuando corresponda. Este texto NO es "
        "memoria verificada de HCMA: es el contenido del archivo adjunto.",
        "",
    ]
    for a in attachments:
        if not isinstance(a, dict):
            continue
        name = a.get("filename") or "documento"
        text = a.get("text") or ""
        if not text:
            continue
        lines.append("--- %s ---" % name)
        lines.append(str(text))
        lines.append("")
    return "\n".join(lines)


_TIMESTAMP_RE = re.compile(r'(?m)^\s*\[\s*\d+\.\d+\s*:\s*\d*(?:\.\d+)?\s*\]')


def _respuesta_degenerada(query, content):
    '''Guard: (bool, motivo) if the LLM output is degenerate (echo/timestamps/poem).'''
    if not isinstance(content, str):
        return False, ''
    text = content.strip()
    if not text:
        return False, ''
    ts = _TIMESTAMP_RE.findall(text)
    if len(ts) >= 2:
        return True, 'timestamp_segments'
    q = (query or '').strip().lower()
    if q and q.endswith('?') and len(q) >= 6:
        t = _TIMESTAMP_RE.sub(' ', text.lower())
        if t.count(q) >= 2 and len(t.strip()) <= len(q) * 6:
            return True, 'echo'
    return False, ''


def chat_completions(body):
    """OpenAI-compatible body {model, messages:[{role,content}], ...} -> OpenAI response.

    MEMORY-FIRST (deterministic): if `hcma_memory` is truthy, first queries the hippocampus with the
    recall gate (`hcma_recall`). If there is RECALL_PURO, responds WITHOUT calling the LLM (the model
    does not decide whether memory is used). If HIT_CONTEXTUAL or NO_HIT, injects the current facts as
    context and calls the LLM (advisory)."""
    base, key, provider = _conn()
    model = body.get("model") or llm_config.load().get("model") or ""
    if not model:
        raise GatewayError("falta 'model' en el request", status=400)
    usuario = body.get("usuario") or ""

    # Attached documents (PDF): text already extracted by the dashboard. Injected as document
    # context (NOT memory) BEFORE recall, so the LLM answers about the attachment.
    if body.get("attachments"):
        actx = _attachment_block(body["attachments"])
        if actx:
            body = _inject_memory(body, actx)

    use_memory = bool(body.get("hcma_memory"))
    facts = []
    tier = None
    egress_info = None
    alias_legacy_unresolved = None
    alias_entidad = None
    q = _last_user_text(body)
    if use_memory:
        import memory_context as MC
        dec = MC.resolve(q, top_k=5)
        tier = dec.get("tier")
        # Anaphora: if the last message is a follow-up WITHOUT a hit (e.g. "mi guiya es mi perra"
        # followed by "¿y de qué color es?"), retry with the previous turn (focus entity), CAPPED to
        # HIT_CONTEXTUAL (never RECALL_PURO: a reference to a previous turn does not answer
        # deterministically).
        if dec.get("tipo") == "NO_HIT" and not dec.get("hechos"):
            q2 = _last_user_text(body, window=2)
            if q2 and q2 != q:
                dec2 = MC.resolve(q2, top_k=5)
                if dec2.get("hechos") or dec2.get("tipo") in ("RECALL_PURO", "HIT_CONTEXTUAL"):
                    dec = dec2
                    dec["tipo"] = "HIT_CONTEXTUAL"
                    tier = dec.get("tier")
        if dec.get("tipo") == "RECALL_PURO":
            resp = _synthetic_response(model, MC.render_recall(dec))
            resp["hcma_facts"] = MC.facts_payload(dec.get("hechos", []))
            resp["origen"] = "hipocampo"
            resp["llm_llamado"] = False
            resp["hcma_decision"] = dec["tipo"]
            resp["hcma_tier"] = "verified"
            resp["procedencia"] = "recall_puro"
            return resp
        # Alias hops 1+2 (fable_propiedad_llm_fable.md opinion): the ENTITY is private knowledge and
        # is resolved deterministically via `alias:<surface>` edges; the PROPERTY is semantic and is
        # selected by the consuming LLM. If an alias edge resolves, a closed-world BLOCK (verified
        # facts of the entity) is injected and the answer is ADVISORY (not deterministic). A verified
        # edge beats the claim and the weak context.
        alias_res = MC.resolve_alias(q, dec)
        if alias_res is not None:
            inject = _egress_facts(alias_res["facts"], base, provider)
            egress_info = {"injected": len(inject), "held": len(alias_res["facts"]) - len(inject)}
            if not inject:
                resp = _synthetic_response(model, MC.render_held(alias_res["facts"]))
                resp["origen"] = "hipocampo"
                resp["hcma_facts"] = MC.facts_payload(alias_res["facts"])
                resp["llm_llamado"] = False
                resp["hcma_decision"] = "HELD"
                resp["hcma_tier"] = "verified"
                resp["procedencia"] = "recall_held"
                resp["hcma_egress"] = egress_info
                return resp
            body = _inject_memory(body, MC.entity_block(alias_res, inject))
            facts = alias_res["facts"]
            tier = "verified"
            alias_entidad = {"surface": alias_res["surface"], "entidad": alias_res["entidad"],
                             "complete": alias_res["complete"]}
        elif dec.get("tipo") == "HIT_CLAIMED":
            # HIT_CLAIMED without a resolved entity: UNVERIFIED chat fact -> deterministic ATTRIBUTED
            # answer (not canon), without LLM.
            resp = _synthetic_response(model, MC.render_claimed(dec))
            resp["hcma_facts"] = MC.facts_payload(dec.get("hechos", []))
            resp["origen"] = "hipocampo"
            resp["llm_llamado"] = False
            resp["hcma_decision"] = dec["tipo"]
            resp["hcma_tier"] = "claimed"
            resp["procedencia"] = "recall_claimed"
            return resp
        else:
            # HIT_CONTEXTUAL / NO_HIT: inject ONLY the relevant facts (sharing content with the
            # query). If there are none relevant, the LLM is given the privacy guard (do not guess
            # personal data; for general knowledge answer normally).
            facts = dec.get("hechos", [])
            if facts:
                if tier == "verified":
                    # P1.1 Fable: legacy surface WITHOUT an edge -> make the miss NOISY (not silent).
                    alias_legacy_unresolved = MC.superficie_alias_legacy(facts)
                # P0-D: filter by egress policy ONLY what is injected into the LLM (the external
                # provider does not see 'secret' facts; 'personal' from the user is injected by
                # default). `facts` (unfiltered) remains for the `hcma_facts` shown to the USER.
                inject = _egress_facts(facts, base, provider)
                egress_info = {"injected": len(inject), "held": len(facts) - len(inject)}
                if not inject:
                    # P0 (Fable): facts retrieved but ALL held by egress -> do NOT lie to the LLM with
                    # no_memory_block. Answer deterministically with local provenance.
                    resp = _synthetic_response(model, MC.render_held(facts))
                    resp["origen"] = "hipocampo"
                    resp["hcma_facts"] = MC.facts_payload(facts)
                    resp["llm_llamado"] = False
                    resp["hcma_decision"] = "HELD"
                    resp["hcma_tier"] = tier
                    resp["procedencia"] = "recall_held"
                    resp["hcma_egress"] = egress_info
                    return resp
                if tier == "claimed":
                    body = _inject_memory(body, MC.claimed_block(inject))
                else:
                    body = _inject_memory(body, MC.context_block(inject))
            else:
                # CORTEX (P0): if the hippocampus has no facts, look up CHUNKS of ingested
                # documents/books (external knowledge). First a DETERMINISTIC lookup by reference
                # (Cap:Vers, e.g. 'traeme Juan 3:16'); otherwise it falls back to content search (FTS)
                # and injects the relevant PORTION, not the whole book.
                cap_vers = MC.cap_vers_en_query(q)
                if cap_vers:
                    ch = MC.buscar_chunk_por_cap_vers(cap_vers)
                    if ch is not None:
                        resp = _synthetic_response(model, MC.render_chunk(ch))
                        resp["origen"] = "cortex"
                        resp["procedencia"] = "cortex_ref"
                        resp["llm_llamado"] = False
                        resp["hcma_chunks"] = [{"key": ch["key"], "meta": ch.get("meta")}]
                        return resp
                chunks = MC.buscar_chunks(q, top_k=5)
                if chunks:
                    body = _inject_memory(body, MC.chunks_block(chunks))
                    egress_info = {"injected": 0, "held": 0, "chunks": len(chunks)}
                else:
                    body = _inject_memory(body, MC.no_memory_block())
                    egress_info = {"injected": 0, "held": 0}

    # Calculation router: if the query looks like a calculation and memory did not answer it, try the
    # DETERMINISTIC calculator (the LLM decomposes, execution is safe AST + verified persistence with
    # lineage). If it is not a calculation or fails, continue with the generic LLM.
    if _parece_calculo(q):
        calc = resolver_calculo(q, model)
        if calc.get("ok"):
            resp = _synthetic_response(model, calc["texto"])
            resp["origen"] = "calculadora"
            resp["computo"] = "determinista"
            resp["hcma_facts"] = calc["hechos"]
            resp["formulas"] = calc["formulas"]
            resp["procedencia"] = "calculadora"
            return resp

    # Skill router (metacognition): decide WHICH tool/skill the agent needs.
    # If there is a skill for the context, its spec (lineage) is injected so the LLM uses it, and the
    # decision is exposed in `hcma_skill` for transparency/audit.
    hcma_skill = None
    # EXPLICIT SIGNAL (out-of-band, deterministic): the user asks to 'use this skill' or 'create a
    # skill'. NOT guessed by keywords (Fable P0 opinion).
    if use_memory and body.get("skill"):
        import memory_context as MC
        sk_id = body["skill"]
        ex = MC.ejecutar_skill_route(sk_id)
        if ex is not None and ex.get("ok"):
            resp = _synthetic_response(model, ex["texto"])
            resp["origen"] = "skill"
            resp["hcma_skill"] = {"id": sk_id, "nivel": "explicito", "ejecutado": True}
            resp["hcma_tier"] = ex.get("tier")
            resp["llm_llamado"] = False
            resp["procedencia"] = "skill"
            return resp
        if ex is not None and not ex.get("ok"):
            faltan = [f.get("name") for f in (ex.get("falta") or [])]
            msg = "Falta(n) input(s) para ejecutar el skill '%s': %s" % (
                sk_id, ", ".join(faltan) if faltan else (ex.get("razon") or "error"))
            resp = _synthetic_response(model, msg)
            resp["origen"] = "skill"
            resp["hcma_skill"] = {"id": sk_id, "nivel": "explicito", "ejecutado": False, "falta": faltan}
            resp["llm_llamado"] = False
            resp["procedencia"] = "skill"
            return resp
        sb = MC.skill_block(sk_id, "explicito")
        if sb:
            body = _inject_memory(body, sb)
            hcma_skill = {"id": sk_id, "nivel": "explicito"}
    elif use_memory and body.get("crear_skill"):
        r = _write_monitor({"tipo": "crear_skill", "contexto": q})
        if r.get("ok"):
            resp = _synthetic_response(
                model, "Candidata de skill creada (autor=modelo, unverified): %s (v%s). "
                       "Un humano debe ratificarla para que sea canónica; no es ejecutable aún."
                       % (r.get("skill_id"), r.get("version")))
            resp["origen"] = "skill"
            resp["hcma_skill"] = {"decision": "crear", "skill_id": r.get("skill_id"),
                                  "version": r.get("version"), "autor": "modelo", "es_canonico": False}
            resp["llm_llamado"] = False
            resp["procedencia"] = "skill_creada"
            return resp
        resp = _synthetic_response(
            model, "No se pudo crear la candidata: %s" % (r.get("detalle") or r.get("error")))
        resp["llm_llamado"] = False
        resp["procedencia"] = "skill_creada_error"
        return resp

    if use_memory:
        import memory_context as MC
        sk = MC.resolve_skill(q)
        if sk and sk[0] == "usar":
            # P0-C: if the skill is DECLARATIVE {inputs, formulas}, EXECUTE it deterministically
            # (do not delegate to the LLM). If it is not declarative, falls back to advisory with the
            # spec injected.
            ex = MC.ejecutar_skill_route(sk[1])
            if ex is not None and ex.get("ok"):
                resp = _synthetic_response(model, ex["texto"])
                resp["origen"] = "skill"
                resp["hcma_skill"] = {"id": sk[1], "nivel": sk[2], "ejecutado": True}
                resp["hcma_tier"] = ex.get("tier")
                resp["llm_llamado"] = False
                resp["procedencia"] = "skill"
                return resp
            if ex is not None and not ex.get("ok"):
                # declarative but without inputs/error: ask for the missing input, do not invent.
                faltan = [f.get("name") for f in (ex.get("falta") or [])]
                msg = "Falta(n) input(s) para ejecutar el skill '%s': %s" % (
                    sk[1], ", ".join(faltan) if faltan else (ex.get("razon") or "error"))
                resp = _synthetic_response(model, msg)
                resp["origen"] = "skill"
                resp["hcma_skill"] = {"id": sk[1], "nivel": sk[2], "ejecutado": False, "falta": faltan}
                resp["llm_llamado"] = False
                resp["procedencia"] = "skill"
                return resp
            sb = MC.skill_block(sk[1], sk[2])
            if sb:
                body = _inject_memory(body, sb)
                hcma_skill = {"id": sk[1], "nivel": sk[2]}
            else:
                # The router pointed at a skill but it is NOT registered in the store (no spec).
                # It is exposed so the UI offers to create it; the LLM answers advisory meanwhile.
                hcma_skill = {"id": sk[1], "nivel": sk[2], "sin_spec": True}
        elif sk and sk[0] == "crear":
            hcma_skill = {"decision": "crear", "contexto": sk[1], "razon": sk[2]}
        elif sk and sk[0] == "sugerir":
            # Gray band: skill_sugerido. Does NOT execute (the UI shows a chip and the LLM answers).
            hcma_skill = {"decision": "sugerir", "skill_sugerido": sk[1], "nivel": sk[2]}
        elif sk and sk[0] == "abstener":
            hcma_skill = {"decision": "abstener", "razon": sk[1]}
            candidatos = sk[2] if len(sk) > 2 else []
            # P0-3: without a multilingual model, a NON-latin query cannot be deduplicated via
            # embeddings (the default is English only). Do NOT auto-create so as not to fragment the
            # skill.
            import slug_canon as _sc
            import indice_skills as _IS
            if _sc.contiene_no_latino(q) and not _IS.es_modelo_multilingue():
                hcma_skill["sin_multilingue"] = True
            else:
                # AUTO-CREATION (P0): on abstaining, an LLM JUDGE decides REUSE vs NEW vs NONE.
                # Only 'new' mints a candidate (author=model, unverified, not executable). Human
                # ratification is the boundary (there is no auto-approval).
                try:
                    juez = juez_auto_skill(q, model, base, key, provider, candidatos=candidatos)
                except Exception:
                    juez = {}
                dec = juez.get("decision") if isinstance(juez, dict) else None
                if dec == "reuse":
                    sid = str(juez.get("id_reuse") or "").strip()
                    if sid:
                        resp = _synthetic_response(
                            model, "Ya existe un skill para esto ('%s'); lo reutilizo." % sid)
                        resp["origen"] = "skill"
                        resp["hcma_skill"] = {"decision": "reuse", "skill_id": sid}
                        resp["llm_llamado"] = False
                        resp["procedencia"] = "skill_reuse"
                        return resp
                elif dec == "new":
                    nombre = str(juez.get("nombre") or "").strip()
                    desc = str(juez.get("descripcion") or "").strip()
                    spec = {"descripcion": desc or q,
                            "inputs": juez.get("inputs") or [],
                            "formulas": juez.get("formulas") or []}
                    w = _write_monitor({"tipo": "crear_skill", "contexto": q,
                                        "valor": spec, "nombre": nombre})
                    if w.get("ok"):
                        resp = _synthetic_response(
                            model, "Creé una candidata de skill '%s' (autor=modelo, unverified). "
                                   "Ratificala en la pestaña Skills para que sea ejecutable y reutilizable."
                                   % w.get("skill_id"))
                        resp["origen"] = "skill"
                        resp["hcma_skill"] = {"decision": "crear_auto", "skill_id": w.get("skill_id"),
                                              "version": w.get("version"), "autor": "modelo",
                                              "es_canonico": False}
                        resp["llm_llamado"] = False
                        resp["procedencia"] = "skill_creada_auto"
                        return resp
                # dec == 'ninguna' or empty judge -> falls through to normal advisory.

    # Cached response (P0, Fable opinion): before re-querying the LLM, if there is a previous
    # advisory response from the SAME user for an exact equivalent query, with the SAME context
    # (ctx_fp) and model (model_fp), it is reused. It is a replay of the UNVERIFIED advisory, never a
    # verified fact nor an input to deterministic routes. Does not apply if a skill spec was injected.
    skill_inyectado = bool(hcma_skill and hcma_skill.get("id"))
    if use_memory and usuario and not skill_inyectado:
        import memory_context as MC
        cached = MC.resolve_respuesta(usuario, q, MC.ctx_fp(facts), MC.model_fp(provider, model))
        if cached is not None:
            resp = _synthetic_response(model, cached["value"])
            resp["origen"] = "respuesta_cacheada"
            resp["procedencia"] = "llm_unverified_cached"
            resp["llm_llamado"] = False
            resp["hcma_tier"] = tier
            resp["hcma_cache"] = {"key": cached["key"], "version": cached["version"],
                                  "modelo": cached.get("modelo"), "ts": cached.get("ts"),
                                  "reutilizada": True}
            resp["hcma_facts"] = MC.facts_payload(facts)
            if hcma_skill is not None:
                resp["hcma_skill"] = hcma_skill
            if egress_info is not None:
                resp["hcma_egress"] = egress_info
            return resp

    if not key:
        raise GatewayError("LLM no configurado (falta API key en el panel)", status=400)

    if _is_google(base, provider):
        raw = _forward_google(base, key, body)
        resp = _from_google(raw, model)
    elif _is_anthropic(base, provider):
        raw = _forward_anthropic(base, key, body)
        resp = _from_anthropic(raw, model)
    else:
        raw = _forward_openai(base, key, body)
        resp = _from_openai(raw, model)

    # Anti-degenerate-output guard: if the model does not answer directly (echo with timestamps,
    # spurious poem), the content is discarded and marked for audit (the original remains in
    # `degenerada_original`; it is not propagated to the chat nor to the cache).
    _msg = (resp.get('choices') or [{}])[0].get('message') or {}
    _content = _msg.get('content') or ''
    _degen, _motivo = _respuesta_degenerada(q, _content)
    if _degen:
        resp['degenerada'] = _motivo
        resp['degenerada_original'] = _content
        _msg['content'] = (
            '[HCMA] Respuesta descartada: el modelo devolvió una salida degenerada (%s). '
            'Probá cambiar de modelo.' % _motivo)

    if hcma_skill is not None:
        resp["hcma_skill"] = hcma_skill
    # Provenance set by CODE (not by the LLM): the model's advisory is general knowledge NOT
    # verified against HCMA. It remains structured for audit (hash chain / dashboard).
    resp["procedencia"] = "llm_unverified"
    if use_memory:
        import memory_context as MC
        resp["hcma_facts"] = MC.facts_payload(facts)
        if tier is not None:
            resp["hcma_tier"] = tier
        if egress_info is not None:
            resp["hcma_egress"] = egress_info
        # P1.1: make the alias miss noisy (legacy surface without edge), for health/telemetry.
        if alias_legacy_unresolved:
            resp["hcma_alias_unresolved"] = alias_legacy_unresolved
        # Entity block resolved via alias (hop 2, fable_propiedad_llm_fable.md opinion): exposes the
        # injected evidence (entidad/complete) for transparency/audit.
        if alias_entidad is not None:
            resp["hcma_alias"] = alias_entidad
        # Advisory response cache (P0, Fable opinion): return the candidate so app._chat ENQUEUES it
        # (write outside the gateway, via monitor op `escribir_respuesta`).
        if usuario and not skill_inyectado:
            import hcma_respcache as RC
            ok_cache, _razon = RC.elegible(body, resp, q)
            if ok_cache:
                resp["cache_candidate"] = RC.candidato(usuario, q, facts, provider, model, resp)
    return resp


def _parece_calculo(query):
    """Cheap heuristic: does the query have numbers + a calculation hint? Avoids calling the
    decomposition LLM on every message (only when it looks like a numeric problem)."""
    q = (query or "").lower()
    if not re.search(r"\d", q):
        return False
    pistas = ("calcula", "calcular", "cuanto", "cuánto", "distancia", "energia", "energía",
              "velocidad", "choque", "encuentro", "pesa", "peso", "masa", "tiempo", "fuerza",
              "km/h", "m/s", "kg", "joule", "watt", "volt", "amper", "potencia", "impacto",
              "aceleracion", "aceleración", "volumen", "area", "área", "perimetro", "perímetro")
    return any(p in q for p in pistas)


def _synthetic_response(model, content):
    """Locally built OpenAI-compatible response (without calling the provider)."""
    return {
        "id": "chatcmpl-hcma-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _write_monitor(op):
    """Sends ONE op to the monitor (governance socket, single writer). Returns {ok, ...}."""
    import socket
    import monitor_ipc as IPC
    sock = os.environ.get("HCMA_MONITOR_SOCK", "/run/hcma/gobernanza.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10.0)
    try:
        s.connect(sock)
    except OSError as e:
        return {"ok": False, "error": f"no conecta a {sock}: {e}"}
    try:
        cli = IPC.ClienteMonitor(sock=s)
        return cli.pedir(op)
    finally:
        s.close()


_CALC_SYS = (
    "Sos un descomponedor de problemas de cálculo a JSON. Devolvé EXCLUSIVAMENTE un JSON "
    "(sin markdown) con esta forma:\n"
    '{"es_calculo": true|false, "inputs": {"nombre": valor_numérico, ...}, '
    '"formulas": [{"name": "...", "expr": "expresión", "unit": "..."}]}\n\n'
    "Reglas:\n"
    "1. Convertí TODO a unidades SI (km/h→m/s, km→m, g→kg, cm→m, minutos→s) y poné el valor "
    "ya convertido en `inputs`.\n"
    "2. `expr` usa SOLO: números, + - * / **, paréntesis, y nombres de `inputs` o de fórmulas "
    "anteriores. NADA de funciones (sin sqrt/pow/sin), sin comas, sin unidades dentro de la expr.\n"
    "3. Ordená las fórmulas para que cada una use solo lo ya definido.\n"
    "4. Si la consulta NO es un problema de cálculo, devolvé exactamente {\"es_calculo\": false}.\n"
    "5. Redondeá los inputs a 4 decimales."
)


def _decompose_calculo(query, model):
    """LLM decomposes the query into a spec {es_calculo, inputs, formulas}. Returns dict or None.

    Provider-agnostic: Google (generateContent), Anthropic (messages) and OpenAI-compatible. The
    EXECUTION (AST calculator) is deterministic and does not depend on the provider."""
    base, key, provider = _conn()
    body = {"model": model, "temperature": 0.0,
            "messages": [{"role": "system", "content": _CALC_SYS},
                         {"role": "user", "content": query}]}
    try:
        if _is_google(base, provider):
            resp = _from_google(_forward_google(base, key, body), model)
        elif _is_anthropic(base, provider):
            resp = _from_anthropic(_forward_anthropic(base, key, body), model)
        else:
            resp = _from_openai(_forward_openai(base, key, body), model)
        text = (resp.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except GatewayError:
        return None
    return _parse_json_loose(text)


def _parse_json_loose(text):
    if not isinstance(text, str):
        return None
    t = re.sub(r"```(?:json)?", "", text)
    s, e = t.find("{"), t.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        return json.loads(t[s:e + 1])
    except Exception:
        return None


def juez_auto_skill(query, model, base, key, provider, candidatos=None):
    """LLM judge: decides whether a request REUSES an existing skill or creates a NEW candidate.

    `candidatos` = ids of existing skills (top-k of the index). Returns
    {decision: 'reuse'|'new'|'ninguna', id_reuse, nombre, descripcion, inputs, formulas} or {} if the
    LLM does not answer valid JSON. 'reuse' does NOT mint a new id (avoids fragmentation, P0-2 Fable);
    'new' produces an unverified candidate (author=model, not executable); 'ninguna' is general
    knowledge. DIRECT call to the provider (does not recurse through chat_completions)."""
    cand = ", ".join(str(c) for c in (candidatos or [])) or "(ninguno)"
    sys = (
        "Sos el orquestador de HCMA. Decidí si el pedido del usuario REUTILIZA una skill "
        "existente o amerita una NUEVA. Skills existentes más parecidas (top-k del índice): "
        "[" + cand + "]. Respondé EXCLUSIVAMENTE un JSON: {\"decision\": \"reuse\"|\"new\"|"
        "\"ninguna\", \"id_reuse\": \"<id>\", \"nombre\": \"slug\", \"descripcion\": "
        "\"1-3 frases\", \"inputs\": [...], \"formulas\": [...]}. Si reutilizás (decision=reuse), "
        "poné id_reuse y dejá nombre/descripcion/inputs/formulas vacíos. Si creás (decision=new), "
        "el slug `nombre` DEBE ser ASCII snake_case canónico (a-z0-9_, NO el idioma del usuario, "
        "ej. 'control_tv'). Si es conocimiento general sin skill reutilizable (decision=ninguna), "
        "dejá todo vacío."
    )
    body = {"model": model,
            "messages": [{"role": "system", "content": sys},
                         {"role": "user", "content": str(query)}],
            "temperature": 0.0}
    try:
        if _is_google(base, provider):
            resp = _from_google(_forward_google(base, key, body), model)
        elif _is_anthropic(base, provider):
            resp = _from_anthropic(_forward_anthropic(base, key, body), model)
        else:
            resp = _from_openai(_forward_openai(base, key, body), model)
        content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    except Exception:
        return {}
    d = _parse_json_loose(content)
    return d if isinstance(d, dict) else {}


def resolver_calculo(query, model):
    """DETERMINISTIC calculation router. Returns {ok, texto, hechos} or {ok:False, razon}.

    The LLM ONLY decomposes (inputs + formulas); EXECUTION is deterministic (calculadora.computar, safe
    AST evaluator) and the inputs/results are persisted as verified facts with lineage (source
    calculadora, derived_from → inputs). It remains auditable."""
    import calculadora as CALC
    spec = _decompose_calculo(query, model)
    if not isinstance(spec, dict) or not spec.get("es_calculo"):
        return {"ok": False, "razon": "no_es_calculo"}
    formulas = spec.get("formulas") or []
    if not formulas:
        return {"ok": False, "razon": "sin_formulas"}
    try:
        resultados = CALC.computar({"inputs": spec.get("inputs") or {}, "formulas": formulas})
    except CALC.CalcError as e:
        return {"ok": False, "razon": f"error de cálculo: {e}"}

    pref = "calc:" + hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
    versions = {}
    for k, v in (spec.get("inputs") or {}).items():
        r = _write_monitor({"tipo": "escribir_hecho", "key": f"{pref}:in:{k}",
                            "value": v, "unit": ""})
        if r.get("ok"):
            versions[f"{pref}:in:{k}"] = r.get("version")
    hechos = []
    for res in resultados:
        r = _write_monitor({"tipo": "escribir_calculo", "key": f"{pref}:{res['name']}",
                            "value": res["value"], "unit": res["unit"],
                            "derived_from": dict(versions)})
        hechos.append({"key": f"{pref}:{res['name']}", "value": res["value"],
                       "unit": res["unit"], "fuente": "calculadora",
                       "version": r.get("version")})
    lineas = ["Cálculo determinista (fuente calculadora):"]
    for res in resultados:
        linea = f"- {res['name']} = {res['value']:.4g}"
        if res["unit"]:
            linea += f" {res['unit']}"
        lineas.append(linea)
    return {"ok": True, "texto": "\n".join(lineas), "hechos": hechos,
            "formulas": resultados}


def ejecutar_skill(skill_id, spec, memoria=None):
    """Executes a DECLARATIVE skill deterministically (P0-C). PURE function: does not write the monitor.

    spec = {inputs: {name: {key: <hecho>} | {valor: <literal>} | <literal>},
            formulas: [{name, expr, unit}]}

    Input binding: first VERIFIED facts by key (memory), then literals. The LLM NEVER calculates or
    invents inputs. If an input is missing, returns {ok:False, falta:[...]} to ask the user for it.
    Result verified only if ALL inputs are verified (tier is inherited).

    Returns {ok, texto, resultados, tier, faltan} or {ok:False, falta|razon}."""
    import calculadora as CALC
    inputs = {}
    faltan = []
    todo_verified = True
    for name, src in (spec.get("inputs") or {}).items():
        if isinstance(src, dict) and "key" in src:
            f = memoria.current(src["key"]) if memoria is not None else None
            if f is None:
                faltan.append({"name": name, "key": src["key"]})
                continue
            inputs[name] = f["value"]
            if not f.get("verified"):
                todo_verified = False
        elif isinstance(src, dict) and "valor" in src:
            inputs[name] = src["valor"]
            todo_verified = False
        else:
            inputs[name] = src
            todo_verified = False
    if faltan:
        return {"ok": False, "falta": faltan, "razon": "faltan inputs verificados"}
    formulas = spec.get("formulas") or []
    if not formulas:
        return {"ok": False, "razon": "spec sin formulas"}
    try:
        resultados = CALC.computar({"inputs": inputs, "formulas": formulas})
    except CALC.CalcError as e:
        return {"ok": False, "razon": f"error de cálculo: {e}"}
    lineas = [f"Skill {skill_id} (determinista):"]
    for res in resultados:
        linea = f"- {res['name']} = {res['value']:.4g}"
        if res["unit"]:
            linea += f" {res['unit']}"
        lineas.append(linea)
    return {"ok": True, "texto": "\n".join(lineas), "resultados": resultados,
            "tier": "verified" if todo_verified else "claimed"}


def list_models():
    c = llm_config.load()
    models = []
    if c.get("model"):
        models.append(c["model"])
    data = [{"id": m, "object": "model", "created": int(time.time()),
             "owned_by": c.get("provider") or "openrouter"} for m in models]
    return {"object": "list", "data": data}


def modality_type(modality):
    """Classifies the modality (OpenRouter's architecture.modality) into a stable type.

    Typical format: 'text->text', 'text+image->text' (vision), 'text->image' (generation),
    'audio->text' (STT), 'text->audio' (TTS), 'text+image+video->text' (multimodal), etc.
    Returns one of: text, image_in, image_out, audio_in, audio_out, video, multimodal."""
    m = (modality or "").strip().lower()
    if not m:
        return "text"
    inp, _, out = m.partition("->")
    ins = {p.strip() for p in inp.split("+") if p.strip()}
    outs = {p.strip() for p in out.split("+") if p.strip()} if out else set()
    nin = ins - {"text"}
    nout = outs - {"text"}
    # generation: non-textual output wins (text -> image / audio / video)
    if "image" in nout:
        return "image_out"
    if "audio" in nout:
        return "audio_out"
    if "video" in nout:
        return "video"
    if nout:
        return "multimodal"
    # perception: non-textual input -> text
    if not nin:
        return "text"
    if len(nin) > 1:
        return "multimodal"
    (solo,) = nin
    return {"image": "image_in", "audio": "audio_in", "video": "video"}.get(solo, "multimodal")


def _is_free(model):
    p = model.get("pricing") or {}
    try:
        return float(p.get("prompt", 0)) == 0.0 and float(p.get("completion", 0)) == 0.0
    except (TypeError, ValueError):
        return False


def _list_models(base, key, provider, free_only=False, type=None):
    """Lists the provider's catalog. Google uses `?key=` and its own format; the rest are
    OpenAI-compatible (Bearer). `free_only` only filters on OpenRouter (the only one with pricing=0)."""
    base = (base or "").rstrip("/")
    if not _scheme_allowed(base):
        raise GatewayError("base_url no permitida (solo https, o http local)", status=400)
    if _is_google(base, provider):
        url = base + "/models?key=" + urllib.parse.quote(key, safe="")
        try:
            with _opener().open(urllib.request.Request(url), timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise GatewayError("HTTP %d %s en %s/models (¿base_url correcto?)" % (e.code, e.reason, base),
                               status=502)
        except urllib.error.URLError as e:
            raise GatewayError("%s en %s/models (¿base_url correcto?)" % (e.reason, base), status=502)
        gmodels = data.get("models", []) if isinstance(data, dict) else []
        out = []
        for m in gmodels:
            name = m.get("name") or ""
            mid = name.split("/", 1)[1] if name.startswith("models/") else name
            out.append({"id": mid, "name": m.get("displayName") or mid,
                        "modality": "", "type": "text", "free": False})
        if type:
            out = [m for m in out if m["type"] == type]
        out.sort(key=lambda x: x["id"])
        return out

    # OpenAI-compatible (OpenRouter, Groq, OpenAI, DeepSeek, Kimi, Ollama, llama.cpp…)
    headers = {"Authorization": "Bearer " + key}
    if "openrouter.ai" in base:
        headers["HTTP-Referer"] = "https://osf.io/vqx95"
        headers["X-Title"] = "HCMA gateway"
    url = base + "/models"
    try:
        with _opener().open(urllib.request.Request(url, headers=headers), timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise GatewayError("HTTP %d %s en %s/models (¿base_url correcto?)" % (e.code, e.reason, base),
                           status=502)
    except urllib.error.URLError as e:
        raise GatewayError("%s en %s/models (¿base_url correcto?)" % (e.reason, base), status=502)
    models = data.get("data", []) if isinstance(data, dict) else []
    if free_only and "openrouter.ai" in base:
        models = [m for m in models if _is_free(m)]
    out = []
    for m in models:
        arch = m.get("architecture") or {}
        mod = arch.get("modality") or ""
        out.append({"id": m.get("id"), "name": m.get("name") or m.get("id"),
                    "modality": mod, "type": modality_type(mod), "free": _is_free(m)})
    if type:
        out = [m for m in out if m["type"] == type]
    out.sort(key=lambda x: x["id"])
    return out


def fetch_models(free_only=True, type=None, provider=None, base_url=None, api_key=None):
    """Lists the provider's catalog. If free_only, filters those with pricing=0 (OpenRouter only).

    Accepts FORM overrides (provider/base_url/api_key). The key is resolved with `conn_for` (anti-leak:
    only reuses the saved one if the origin matches)."""
    base, key, provider = conn_for(provider, base_url, api_key)
    if not key:
        raise GatewayError("LLM no configurado (falta API key en el panel)", status=400)
    return _list_models(base, key, provider, free_only=free_only, type=type)


def test_models(base, key, provider):
    """For 'test connection': returns (n_modelos, error)."""
    try:
        return len(_list_models(base, key, provider, free_only=False)), None
    except GatewayError as e:
        return 0, e.message


def _plain_value(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else str(v)


def _parse_facts_json(text):
    """Parses the fact extractor output (a JSON list). Robust to markdown/noise."""
    if not isinstance(text, str):
        return []
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except (ValueError, RecursionError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        k = item.get("key")
        v = item.get("value")
        if not isinstance(k, str) or not k.strip() or v is None:
            continue
        unit = item.get("unit")
        out.append({"key": k.strip(), "value": _plain_value(v),
                    "unit": "" if unit is None else str(unit).strip()})
    return out


def extract_facts(messages):
    """Extracts facts declared by the user (best effort, without memory) as [{key,value,unit}].

    Used to PERSIST into HCMA memory what a human tells in the chat, so another model/session can
    retrieve it. If the model does not return valid JSON, returns [] without failing."""
    if not messages:
        return []
    # Only the USER's turns: never extract from what the assistant, the system nor injected context
    # said (prevents the model's self-description from being saved as a user fact).
    user_msgs = [dict(m) for m in messages if (m or {}).get("role") == "user"]
    if not user_msgs:
        return []
    sys_msg = {"role": "system", "content": (
        "Extraé los hechos que el USUARIO afirma (sobre sí mismo, su entorno o el mundo) "
        "como una lista JSON de objetos con claves: \"key\" (nombre corto en minúsculas, "
        "usá ':' para categorías, ej. \"perro:color\"), \"value\" (texto simple) y "
        "\"unit\" (opcional, vacío si no aplica). Reglas estrictas:\n"
        "1) Solo hechos declarados explícitamente por el usuario.\n"
        "2) Ignorá preguntas puras, saludos, pedidos e instrucciones; PERO si dentro de una "
        "pregunta el usuario AFIRMA un hecho (ej. \"¿por qué no sabés que mi guiya es mi perra?\" "
        "→ hecho: guiya = apodo de mi perra), extraé ese hecho igual.\n"
        "3) Si el usuario NIEGA, duda o presupone algo (ej. \"¿por qué dijiste que mi guiya es mi "
        "gata?\"), NO lo extraigas: negación/presuposición no es afirmación.\n"
        "4) Respondé EXCLUSIVAMENTE con el JSON, sin markdown ni texto extra.\n"
        "5) Si no hay hechos, respondé exactamente: []")}
    try:
        cfg = llm_config.load()
        resp = chat_completions({"model": cfg.get("model") or "",
                                  "messages": [sys_msg] + user_msgs,
                                  "hcma_memory": False})
        content = (resp.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except Exception:
        return []
    return _parse_facts_json(content)
