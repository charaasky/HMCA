#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Prototype v3: generic core + declarative domains.

The key design idea: the CORE (orchestrator, status router, HCMA, verification,
preflight, escalation, cost) does NOT change per domain. To add a new domain
(e.g. electric motor) you only add a declarative SPEC in the DOMINIOS section:
deterministic formulas + inputs + ranges + examples + golden tests.

The LLM never does arithmetic: it picks the formula and extracts inputs; the code computes.
"""
import hashlib, json, math, os, re, sys, time, unicodedata
from collections import Counter
from urllib.request import Request, urlopen

import pint

ureg = pint.UnitRegistry()
# Dimensionless units for counting (bricks, bags, etc.). Pint accepts them as
# `dimensionless`; the semantic name is kept for the report.
ureg.define("unidad = [] = und")
ureg.define("bolsa = [] = bol")

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "llm_cache")
# Dashboard (skills) lives one level up; optional import (does not break if absent).
_DASH = os.path.join(os.path.dirname(HERE), "dashboard")
if _DASH not in sys.path:
    sys.path.insert(0, _DASH)
try:
    import skills as _skills_mod
except Exception:
    _skills_mod = None
MODEL = os.environ.get("ARQ_MODEL", "openai/gpt-4o-mini")
MODEL_FRONTERA = os.environ.get("ARQ_FRONTERA", "openai/gpt-6-luna-pro")
DEMO = os.environ.get("ARQ_DEMO", "1") == "1"
G = 9.80665

# ═══════════════════════════════════════════════════════════════════════════════
# NÚCLEO GENÉRICO (no cambia por dominio)
# ═══════════════════════════════════════════════════════════════════════════════

def load_key():
    env = {}
    # busca .env en: HMC/.env (2 niveles arriba), arquitectura/.env (1), scripts/.env (acá)
    for p in (os.path.join(HERE, "..", "..", ".env"),
              os.path.join(HERE, "..", ".env"),
              os.path.join(HERE, ".env")):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env.get("OPENROUTER_API_KEY", "")


KEY = load_key()
USAGE = {}
WEB_COST = {"usd": 0.0, "llamadas": 0, "cache_hits": 0}

# ── Config del panel LLM del dashboard (un humano configura la conexión sin editar .env) ──
# Prioridad: variable de entorno explícita > panel (llm_config.json) > default. La API key del
# panel solo se usa si no hay OPENROUTER_API_KEY en .env/entorno (el panel es el single source
# para el humano, pero un override explícito gana).
def _llm_config_panel():
    try:
        import llm_config as _lc
        return _lc.load()
    except Exception:
        return {}


_cfg_panel = _llm_config_panel()
if not KEY and _cfg_panel.get("api_key"):
    KEY = _cfg_panel["api_key"]
if not os.environ.get("ARQ_MODEL") and _cfg_panel.get("model"):
    MODEL = _cfg_panel["model"]
if not os.environ.get("ARQ_FRONTERA") and _cfg_panel.get("model_frontera"):
    MODEL_FRONTERA = _cfg_panel["model_frontera"]


def _add_usage(model, pt, ct):
    u = USAGE.setdefault(model, [0, 0])
    u[0] += pt
    u[1] += ct


_TEL = None
def _telemetry():
    global _TEL
    if _TEL is None:
        try:
            _dash = os.path.join(HERE, "..", "dashboard")
            if _dash not in sys.path:
                sys.path.insert(0, _dash)
            from telemetry import Telemetry
            _TEL = Telemetry(os.environ.get("ARQ_TELEMETRY_DB") or
                             os.path.join(_dash, "telemetry.db"))
        except Exception:
            _TEL = False
    return _TEL


def _record_llm(model, pt, ct, latency_ms=0.0, cached=False, call_type="llm", cost=None):
    t = _telemetry()
    if t:
        try:
            t.record(model, pt, ct, latency_ms=latency_ms, cached=cached,
                     call_type=call_type, cost=cost)
        except Exception:
            pass


def _catalog():
    cp = "/tmp/openrouter_models_cache.json"
    try:
        return json.load(open(cp, encoding="utf-8"))
    except Exception:
        req = Request("https://openrouter.ai/api/v1/models",
                      headers={"Authorization": f"Bearer {KEY}"})
        with urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode()).get("data", [])
        json.dump(data, open(cp, "w", encoding="utf-8"))
        return data


def get_model_max_tokens(model, default=8000):
    """Devuelve el max_tokens del modelo, capado a 16384 (E1: pedir context_length entero
    da 400 en muchos providers para JSON corto)."""
    for m in _catalog():
        if m.get("id") == model or m.get("canonical_slug") == model:
            tp = m.get("top_provider") or {}
            mc = tp.get("max_completion_tokens") or 0
            if mc > 0:
                return min(mc, 16384)
            return min(m.get("context_length") or default, 16384)
    return min(default, 16384)


def get_pricing():
    out = {}
    for m in _catalog():
        p = m.get("pricing") or {}
        try:
            out[m.get("id")] = {"prompt": float(p.get("prompt", 0)),
                                "completion": float(p.get("completion", 0))}
        except (TypeError, ValueError):
            pass
    return out


def report_cost():
    try:
        pricing = get_pricing()
    except Exception:
        pricing = {}
    print("\n" + "=" * 60)
    print("💰 Costo de cómputo (tokens / USD):")
    tp = tc = total_usd = 0
    for model, (pt, ct) in sorted(USAGE.items()):
        p = pricing.get(model, {"prompt": 0, "completion": 0})
        usd = pt * p["prompt"] + ct * p["completion"]
        tp += pt; tc += ct; total_usd += usd
        print(f"   {model}: {pt} prompt + {ct} completion = ${usd:.4f}")
    if WEB_COST["llamadas"]:
        print(f"   🌐 búsqueda web: {WEB_COST['llamadas']} llamada(s) "
              f"({WEB_COST['cache_hits']} en caché) = ${WEB_COST['usd']:.4f}")
        total_usd += WEB_COST["usd"]
    print(f"   TOTAL: {tp + tc} tokens = ${total_usd:.4f} USD")


def _llm_conn():
    """Devuelve (base_url, api_key, provider) del LLM: panel (llm_config) > KEY/env > OpenRouter.

    Leer acá (no cachear) hace que un cambio en el panel tome efecto sin reiniciar prototipo."""
    base = "https://openrouter.ai/api/v1"
    key = KEY
    provider = "openrouter"
    try:
        import llm_config as _lc
        c = _lc.load()
        if c.get("base_url"):
            base = str(c["base_url"]).rstrip("/")
        if c.get("api_key"):
            key = c["api_key"]
        if c.get("provider"):
            provider = str(c["provider"])
    except Exception:
        pass
    return base, key, provider


def _openai_request(base_url, api_key, model, prompt, system, max_tokens, json_mode):
    """Request OpenAI-compatible (/chat/completions)."""
    msgs = ([{"role": "system", "content": system}] if system else [])
    msgs.append({"role": "user", "content": prompt})
    body = {"model": model, "messages": msgs, "temperature": 0.0, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    if "openrouter.ai" in base_url:
        headers["HTTP-Referer"] = "https://osf.io/vqx95"
        headers["X-Title"] = "HCMA arquitectura"
    return Request(base_url + "/chat/completions", data=json.dumps(body).encode(),
                   headers=headers, method="POST")


def _anthropic_request(base_url, api_key, model, prompt, system, max_tokens):
    """Request del API de Mensajes de Anthropic (/messages; NO es OpenAI-compatible)."""
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
               "Content-Type": "application/json"}
    return Request(base_url + "/messages", data=json.dumps(body).encode(),
                   headers=headers, method="POST")


def call_llm(prompt, model, system=None, max_tokens=None, json_mode=True):
    key = hashlib.sha256(json.dumps({"p": prompt, "s": system, "m": model}).encode()).hexdigest()
    cp = os.path.join(CACHE_DIR, key + ".json")
    if os.path.exists(cp):
        try:
            d = json.load(open(cp, encoding="utf-8"))
            _add_usage(model, d.get("prompt_tokens", 0), d.get("completion_tokens", 0))
            _record_llm(model, d.get("prompt_tokens", 0), d.get("completion_tokens", 0), cached=True)
            return d["content"]
        except Exception:
            pass
    # max_tokens se resuelve SOLO si hay caché-miss, para que un fallo del catálogo no
    # tumbe un cache-hit (y no hacer una llamada de red antes de mirar la caché).
    if max_tokens is None:
        try:
            max_tokens = get_model_max_tokens(model)
        except Exception:
            max_tokens = 8000
    base_url, api_key, provider = _llm_conn()
    anthropic = (provider == "anthropic") or ("anthropic.com" in base_url)
    if anthropic:
        req = _anthropic_request(base_url, api_key, model, prompt, system, max_tokens)
    else:
        req = _openai_request(base_url, api_key, model, prompt, system, max_tokens, json_mode)
    content = None
    pt = ct = 0
    t0 = time.perf_counter()
    for attempt in range(4):
        try:
            with urlopen(req, timeout=600) as r:
                d = json.loads(r.read().decode())
            if anthropic:
                content = "".join(b.get("text", "") for b in d.get("content", [])
                                  if isinstance(b, dict) and b.get("type") == "text")
                u = d.get("usage", {})
                pt = u.get("input_tokens", 0)
                ct = u.get("output_tokens", 0)
            else:
                content = d["choices"][0]["message"].get("content")
                if content is None:
                    content = d["choices"][0]["message"].get("reasoning") or ""
                u = d.get("usage", {})
                pt = u.get("prompt_tokens", 0)
                ct = u.get("completion_tokens", 0)
            break
        except Exception:
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
            else:
                raise
    os.makedirs(CACHE_DIR, exist_ok=True)
    # no cachear respuestas vacías/inválidas (envenenarían la caché y re-dispararían el frontier)
    cacheable = bool((content or "").strip())
    if json_mode:
        cacheable = cacheable and bool(parse_json(content))
    if cacheable:
        json.dump({"content": content, "prompt_tokens": pt, "completion_tokens": ct},
                  open(cp, "w", encoding="utf-8"))
    _add_usage(model, pt, ct)
    _record_llm(model, pt, ct, latency_ms=(time.perf_counter() - t0) * 1000.0, cached=False)
    return content


def parse_json(text):
    if not text:
        return {}
    text = re.sub(r"```(?:json)?", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e <= s:
        return {}
    try:
        return json.loads(text[s:e+1])
    except Exception:
        return {}


def call_vision(prompt, images_b64, model=None, max_tokens=None):
    """LLM multimodal (visión) vía OpenRouter: envía imágenes y devuelve texto.

    Tiene caché por hash(prompt+imágenes+modelo) y reintentos, igual que call_llm.
    """
    model = model or os.environ.get("ARQ_VISION_MODEL", "openai/gpt-4o-mini")
    key = hashlib.sha256(json.dumps({"p": prompt, "imgs": images_b64, "m": model}).encode()).hexdigest()
    cp = os.path.join(CACHE_DIR, "vis_" + key + ".json")
    if os.path.exists(cp):
        try:
            d = json.load(open(cp, encoding="utf-8"))
            _add_usage(model, d.get("prompt_tokens", 0), d.get("completion_tokens", 0))
            _record_llm(model, d.get("prompt_tokens", 0), d.get("completion_tokens", 0),
                        cached=True, call_type="vision")
            return d["content"]
        except Exception:
            pass
    if max_tokens is None:
        try:
            max_tokens = get_model_max_tokens(model)
        except Exception:
            max_tokens = 8000
    content = [{"type": "text", "text": prompt}]
    for b64 in images_b64:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}})
    payload = json.dumps({"model": model, "messages": [{"role": "user", "content": content}],
                          "temperature": 0.0, "max_tokens": max_tokens}).encode()
    req = Request("https://openrouter.ai/api/v1/chat/completions", data=payload,
                  headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                           "HTTP-Referer": "https://osf.io/vqx95", "X-Title": "HCMA arquitectura"},
                  method="POST")
    content_out = None
    pt = ct = 0
    t0 = time.perf_counter()
    for attempt in range(4):
        try:
            with urlopen(req, timeout=600) as r:
                d = json.loads(r.read().decode())
            content_out = d.get("choices", [{}])[0].get("message", {}).get("content") or ""
            u = d.get("usage", {})
            pt = u.get("prompt_tokens", 0)
            ct = u.get("completion_tokens", 0)
            break
        except Exception:
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
            else:
                raise
    os.makedirs(CACHE_DIR, exist_ok=True)
    if (content_out or "").strip():
        json.dump({"content": content_out, "prompt_tokens": pt, "completion_tokens": ct},
                  open(cp, "w", encoding="utf-8"))
    _add_usage(model, pt, ct)
    _record_llm(model, pt, ct, latency_ms=(time.perf_counter() - t0) * 1000.0,
                cached=False, call_type="vision")
    return content_out


def busqueda_modelo(query):
    """Búsqueda sobre el CONOCIMIENTO INTERNO del modelo (sin web).

    Devuelve hechos + marca de vigencia: 'estable' (constante física / tabla estándar) vs
    'drift_temporal' (precios/fechas/normas que cambian). Los hechos drift_temporal NO se
    confirman solos: quedan unverified y el ingeniero los valida.
    """
    return parse_json(call_llm(query, MODEL, system=SYS_BUSQUEDA_MODELO))


def _safe_max_tokens(model, default=8000):
    try:
        return get_model_max_tokens(model, default)
    except Exception:
        return default


def busqueda_web(query, max_resultados=3):
    """Búsqueda WEB REAL (plugin de OpenRouter) → hechos estructurados + fuentes (URLs).

    Se usa para datos drift_temporal (precios, normativa vigente, disponibilidad). Devuelve
    hechos + citas; sigue entrando como `unverified`. Tiene caché por query (para no repetir
    lookups caros) y registra el costo real (usage.cost, que incluye la recuperación web).
    """
    key = hashlib.sha256(query.encode()).hexdigest()
    cp = os.path.join(CACHE_DIR, "web_" + key + ".json")
    if os.path.exists(cp):
        try:
            d = json.load(open(cp, encoding="utf-8"))
            WEB_COST["cache_hits"] += 1
            _record_llm(MODEL, 0, 0, cached=True, call_type="web")
            return d
        except Exception:
            pass
    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYS_BUSQUEDA_WEB},
            {"role": "user", "content": query},
        ],
        "temperature": 0.0,
        "max_tokens": _safe_max_tokens(MODEL),
        "response_format": {"type": "json_object"},
        "plugins": [{"id": "web", "max_results": max_resultados}],
    }).encode()
    req = Request("https://openrouter.ai/api/v1/chat/completions", data=payload,
                  headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                           "HTTP-Referer": "https://osf.io/vqx95", "X-Title": "HCMA arquitectura"},
                  method="POST")
    msg = None
    t0 = time.perf_counter()
    for attempt in range(4):
        try:
            with urlopen(req, timeout=180) as r:
                d = json.loads(r.read().decode())
            msg = d.get("choices", [{}])[0].get("message", {})
            u = d.get("usage", {})
            _add_usage(MODEL, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
            WEB_COST["usd"] += float(u.get("cost", 0) or 0)
            WEB_COST["llamadas"] += 1
            _record_llm(MODEL, u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                        latency_ms=(time.perf_counter() - t0) * 1000.0,
                        cached=False, call_type="web", cost=float(u.get("cost", 0) or 0))
            break
        except Exception:
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
            else:
                # degradación: un fallo de web no debe tumbar la corrida (E2)
                return {"hechos": {}, "fuentes": [], "via": "web", "nota": "búsqueda web falló"}
    out = parse_json(msg.get("content") or "") if msg is not None else {}
    if not isinstance(out, dict):
        out = {}
    fuentes = []
    for a in (msg.get("annotations", []) if msg is not None else []):
        c = a.get("url_citation", {})
        if c.get("url"):
            fuentes.append({"url": c["url"], "title": c.get("title", "")})
    out["fuentes"] = fuentes
    out["via"] = "web"
    # no cachear resultados vacíos (evita persistir un lookup fallido para siempre)
    if out.get("hechos"):
        os.makedirs(CACHE_DIR, exist_ok=True)
        json.dump(out, open(cp, "w", encoding="utf-8"))
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# Retrieval LOCAL determinista sobre el conocimiento ingerido (ingesta_libros.py).
# Se consulta PRIMERO (gratis, determinista, con procedencia); si no hay match
# suficiente se degrada a modelo → web. Los hechos locales son OCR + UNVERIFIED.
# ═══════════════════════════════════════════════════════════════════════════════
CONOCIMIENTO_DIR = os.path.join(HERE, "..", "resultados", "conocimiento")
_stores_cache = {"mtime": None, "data": []}

# Claves bibliográficas/de portada: NO son conocimiento de ingeniería. Se separan
# en "metadata" (no buscables) para no ensuciar el retrieval local.
METADATA_EXACTAS = {
    "autor", "autores", "author", "authors", "editor", "editores", "editorial",
    "publisher", "publicacion", "publication", "ano", "anio", "año", "year",
    "fecha", "date", "titulo", "title", "volumen", "volume", "vol", "tomo",
    "edicion", "edition", "copyright", "institucion", "institution",
    "universidad", "university", "college", "escuela", "facultad",
    "departamento", "department", "cargo", "position", "profesion", "profession",
    "puesto", "experiencia", "nivel", "isbn", "issn", "doi",
    "tipo", "pagina", "page", "lugar", "ciudad", "pais",
}
METADATA_SUBCADENAS = (
    "publicacion", "publication", "editorial", "institucion", "institution",
    "universidad", "university", "edicion", "edition", "copyright", "autor",
    "author", "año", "anio", "titulo", "title", "nivel",
)


def _quitar_acentos(s):
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c))


def _norm_clave(k):
    return re.sub(r"[^a-z0-9]+", "", _quitar_acentos(k).lower())


def _es_metadata(clave):
    # matching por tokens EXACTOS (no subcadenas): evita falsos positivos tipo
    # "medicion"⊂"edicion", "nivelacion"⊂"nivel", "series winding"⊂"series".
    partes = set(re.split(r"[^a-z0-9]+", _quitar_acentos(clave).lower()))
    return bool(partes & METADATA_EXACTAS)


# Stopwords ES/EN (bajan el ruido de palabras vacías; sin ellas el score se satura).
STOP = set("""a an and at by for from in into is it of on or that the this to with two
de del la el los las un una unos unas y o en por para con al se que es son como entre
sobre su sus lo cuando donde""".split())

# Símbolos matemáticos → palabras, ANTES de quitar acentos (NFKD pegaría '²' al token
# anterior, p. ej. 'P1²' → 'p12'; acá se separa primero).
_SIMBOLOS = str.maketrans({
    "√": " sqrt ", "θ": " theta ", "α": " alpha ", "β": " beta ", "π": " pi ",
    "Σ": " sum ", "Φ": " phi ", "φ": " phi ", "ω": " omega ", "μ": " mu ",
    "λ": " lambda ", "°": " deg ", "²": " 2 ", "³": " 3 ", "^": " ", "*": " ",
    "·": " ",
})

# Glosario ES↔EN estático (cognados técnicos frecuentes). Ampliable; no rompe determinismo.
SINONIMOS = {
    "force": ["fuerza"], "fuerza": ["force"], "angle": ["angulo"], "angulo": ["angle"],
    "beam": ["viga"], "viga": ["beam"], "load": ["carga"], "carga": ["load"],
    "stress": ["esfuerzo", "tension"], "esfuerzo": ["stress"], "shear": ["cortante", "cizalla"],
    "work": ["trabajo"], "trabajo": ["work"], "speed": ["velocidad"],
    "velocity": ["velocidad"], "current": ["corriente"], "corriente": ["current"],
    "voltage": ["voltaje", "tension"], "strength": ["resistencia"],
    "weight": ["peso"], "peso": ["weight"],
    "perro": ["dog", "can"], "dog": ["perro", "can"], "can": ["perro", "dog"],
}


def _stem(t):
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    return t[:6]  # pseudo-stem por prefijo: resultant/resultante→result, moment/momento→moment


def _tokens(s, quitar_stop=True):
    s = _quitar_acentos(str(s).translate(_SIMBOLOS)).lower()
    s = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", s)  # 2p1p2cos → 2 p 1 p 2 cos
    toks = re.findall(r"[a-z]+|\d+", s)
    return [_stem(t) for t in toks if not (quitar_stop and t in STOP)]


def _campos(tipo, item):
    """Devuelve [(texto, peso)]: campos de alta señal (clave/contexto) pesan más."""
    if tipo == "hecho":
        return [(item.get("clave", ""), 3), (item.get("valor", ""), 1)]
    v = item.get("variables") or []
    if isinstance(v, dict):
        v = [f"{k} {x}" for k, x in v.items()]
    return [(item.get("contexto", ""), 3), (" ".join(map(str, v)), 2),
            (item.get("expresion", ""), 1)]


def _construir_indice(stores):
    docs, df = [], Counter()
    for st in stores:
        proc = st.get("procedencia", {})
        for tipo, lista in (("hecho", st.get("hechos", [])), ("formula", st.get("formulas", []))):
            for item in lista:
                if tipo == "hecho" and _es_metadata(item.get("clave", "")):
                    continue
                tf = Counter()
                for texto, peso in _campos(tipo, item):
                    for t in _tokens(texto):
                        tf[t] += peso
                if tf:
                    docs.append((tipo, item, proc, tf, sum(tf.values())))
                    df.update(tf.keys())
    n = len(docs)
    idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    return {"docs": docs, "idf": idf, "avgdl": sum(d[4] for d in docs) / max(n, 1),
            "idf_oov": math.log(1 + (n + 0.5) / 0.5)}


def _cargar_indice():
    stores = _cargar_stores()
    if _stores_cache.get("indice_key") != _stores_cache["mtime"]:
        _stores_cache["indice"] = _construir_indice(stores) if stores else None
        _stores_cache["indice_key"] = _stores_cache["mtime"]
    return _stores_cache.get("indice")


def _expandir_query(query):
    q = Counter()
    for t in _tokens(query):
        q[t] += 1.0
        for s in SINONIMOS.get(t, ()):
            q[_stem(s)] += 0.7  # sinónimo pesa menos que el término original
    return q


def _cargar_stores():
    """Carga (y cachea por mtime) los stores de conocimiento ingeridos."""
    try:
        mtimes = [os.path.getmtime(os.path.join(CONOCIMIENTO_DIR, f))
                  for f in os.listdir(CONOCIMIENTO_DIR)
                  if f.endswith(".json") and not f.endswith(".manifest.json")]
    except Exception:
        return []
    key = tuple(sorted(mtimes))
    if key and key == _stores_cache["mtime"]:
        return _stores_cache["data"]
    data = []
    for f in sorted(os.listdir(CONOCIMIENTO_DIR)):
        if not f.endswith(".json") or f.endswith(".manifest.json"):
            continue
        try:
            data.append(json.load(open(os.path.join(CONOCIMIENTO_DIR, f),
                                       encoding="utf-8")))
        except Exception:
            continue
    _stores_cache["mtime"] = key
    _stores_cache["data"] = data
    return data


def _recuperar_local(query, top_k=40, k1=1.2, b=0.75):
    """BM25 determinista con pesos por campo, stopwords y glosario ES↔EN.

    Score normalizado a [0,1] (fracción de la masa IDF de la query cubierta) para
    mantener compatible `min_score`. Devuelve [(score, tipo, item, procedencia)]."""
    idx = _cargar_indice()
    if not idx:
        return []
    q = _expandir_query(query)
    if not q:
        return []
    idf, avgdl, oov = idx["idf"], idx["avgdl"], idx["idf_oov"]
    norm = sum(w * idf.get(t, oov) * (k1 + 1) for t, w in q.items()) or 1.0
    hits = []
    for i, (tipo, item, proc, tf, dl) in enumerate(idx["docs"]):
        s = 0.0
        for t, w in q.items():
            f = tf.get(t)
            if f:
                s += w * idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl))
        if s > 0:
            hits.append((round(s / norm, 6), tipo, item, proc, i))
    hits.sort(key=lambda x: (-x[0], x[4]))  # desempate explícito y estable
    return [h[:4] for h in hits[:top_k]]


DENSE_MODEL = os.environ.get("ARQ_DENSE_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
_dense_cache = {"model": None, "docs": None, "embs": None}


def _texto_item(tipo, item):
    """Texto indexable de un ítem (hecho o fórmula) para el retrieval denso."""
    if tipo == "hecho":
        return f"{item.get('clave', '')}: {item.get('valor', '')}"
    v = item.get("variables") or []
    if isinstance(v, dict):
        v = " ".join(f"{k} {x}" for k, x in v.items())
    elif isinstance(v, (list, tuple)):
        v = " ".join(map(str, v))
    return f"{item.get('expresion', '')} {item.get('contexto', '')} {v}"


def _construir_dense():
    """Indexa el store con embeddings multilingües (CPU). Se calcula una sola vez."""
    idx = _cargar_indice()
    if not idx:
        return None
    import torch
    torch.set_num_threads(1)  # determinismo en CPU
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(DENSE_MODEL)
    docs = idx["docs"]
    textos = [_texto_item(tipo, item) for (tipo, item, proc, tf, dl) in docs]
    embs = model.encode(textos, normalize_embeddings=True, convert_to_numpy=True,
                        show_progress_bar=False)
    return model, docs, embs


def _recuperar_dense(query, top_k=40):
    """Retrieval denso (embeddings multilingües MiniLM) — CPU, cosine sobre vectores
    normalizados. Devuelve [(score, tipo, item, procedencia)] con score = coseno."""
    import numpy as np
    if _dense_cache["model"] is None:
        _dense_cache["model"], _dense_cache["docs"], _dense_cache["embs"] = _construir_dense()
    model, docs, embs = _dense_cache["model"], _dense_cache["docs"], _dense_cache["embs"]
    if model is None:
        return []
    q = model.encode([query], normalize_embeddings=True, convert_to_numpy=True)[0]
    sims = embs @ q  # coseno (embeddings normalizados)
    order = np.argsort(-sims, kind="stable")
    hits = []
    for i in order[:top_k]:
        tipo, item, proc, tf, dl = docs[i]
        hits.append((round(float(sims[i]), 6), tipo, item, proc))
    return hits


def _recuperar_hibrido(query, top_k=40, k=60):
    """Fusión RRF (Reciprocal Rank Fusion) de BM25 + denso: combina la precisión
    léxica de BM25 con el recall semántico/cross-lingual del denso. Determinista."""
    bm = _recuperar_local(query, top_k=100)
    dn = _recuperar_dense(query, top_k=100)
    if not dn:
        return bm
    if not bm:
        return dn

    def _clave(h):
        tipo, item, proc = h[1], h[2], h[3]
        if tipo == "hecho":
            return ("hecho", item.get("clave"), proc.get("id"))
        return ("formula", item.get("expresion"), proc.get("id"))

    rrf = {}
    for rank, h in enumerate(bm, 1):
        rrf.setdefault(_clave(h), [0.0, h])[0] += 1.0 / (k + rank)
    for rank, h in enumerate(dn, 1):
        rrf.setdefault(_clave(h), [0.0, h])[0] += 1.0 / (k + rank)
    fused = sorted(rrf.values(), key=lambda x: -x[0])
    return [(round(s, 6), h[1], h[2], h[3]) for s, h in fused[:top_k]]


def _formato_candidatos(hits):
    out = []
    for i, (s, tipo, item, proc) in enumerate(hits):
        if tipo == "hecho":
            texto = f"{item.get('clave')} = {item.get('valor')}"
        else:
            texto = (f"fórmula: {item.get('expresion')} "
                     f"(variables: {item.get('variables')}; contexto: {item.get('contexto')})")
        out.append({"id": i, "tipo": tipo, "texto": texto,
                    "fuente": f"{proc.get('titulo','')} · {proc.get('autor','')} · {proc.get('anio','')}"})
    return out


def buscar_local(query, top_k=6, min_score=0.35):
    """Retrieval determinista (sin re-rank) — para uso directo/debug."""
    hits = _recuperar_local(query, top_k=top_k)
    if not hits or hits[0][0] < min_score:
        return None
    hechos, procedencia = {}, []
    for s, tipo, item, proc in hits:
        if tipo == "hecho":
            hechos[item["clave"]] = item["valor"]
        else:
            hechos[f"formula:{item.get('expresion', '')[:40]}"] = {
                "expresion": item.get("expresion"),
                "variables": item.get("variables"),
                "contexto": item.get("contexto")}
        procedencia.append({"titulo": proc.get("titulo"), "autor": proc.get("autor"),
                            "anio": proc.get("anio"), "licencia": proc.get("licencia"),
                            "score": round(s, 3)})
    return {"tema": query, "hechos": hechos, "via": "local", "vigencia": "estable",
            "procedencia": procedencia,
            "nota": "conocimiento ingerido (OCR, UNVERIFIED) — validar antes de usar como canónico"}


SYS_RERANK = (
    "Eres un lector/reranker de conocimiento de ingeniería recuperado de libros de dominio "
    "público. Recibís una consulta y una lista de candidatos con su procedencia. Devolvé SOLO "
    "JSON (json_object) con:\n"
    '- "relevantes": lista de IDs (enteros) de los candidatos que realmente responden la consulta\n'
    '- "respuesta": string breve con la respuesta, usando SOLO los candidatos elegidos\n'
    '- "confianza": número 0-1 (qué tan bien cubren los candidatos la consulta)\n'
    '- "sin_datos": bool (true si NINGÚN candidato permite responder)\n'
    '- "nota": string breve sobre fiabilidad\n'
    "Reglas: NO inventes hechos, números ni fórmulas que no estén en los candidatos. "
    "Si los candidatos no alcanzan, poné sin_datos=true y respuesta vacía. "
    "Citá la fuente (libro/año) en la respuesta cuando corresponda."
)


def re_rank_local(query, hits):
    """Re-rank con el modelo sobre los candidatos locales (retriever → reader).

    El modelo NO inventa: está limitado a los candidatos. Devuelve hechos + procedencia
    con via='local_rerank', o sin_datos=True si los candidatos no cubren la consulta."""
    if not hits:
        return None
    candidatos = _formato_candidatos(hits)
    prompt = json.dumps({"consulta": query, "candidatos": candidatos}, ensure_ascii=False)
    raw = call_llm(prompt, MODEL, system=SYS_RERANK)
    out = parse_json(raw)
    if not out:
        return None
    if out.get("sin_datos"):
        return {"tema": query, "hechos": {}, "via": "local_rerank", "vigencia": "estable",
                "sin_datos": True, "nota": out.get("nota", "candidatos locales no cubren la consulta")}
    relevantes = out.get("relevantes") or []
    hechos, procedencia = {}, []
    for i in relevantes:
        if isinstance(i, int) and 0 <= i < len(hits):
            s, tipo, item, proc = hits[i]
            if tipo == "hecho":
                hechos[item["clave"]] = item["valor"]
            else:
                hechos[f"formula:{item.get('expresion', '')[:40]}"] = {
                    "expresion": item.get("expresion"),
                    "variables": item.get("variables"),
                    "contexto": item.get("contexto")}
            procedencia.append({"titulo": proc.get("titulo"), "autor": proc.get("autor"),
                                "anio": proc.get("anio"), "licencia": proc.get("licencia"),
                                "score": round(s, 3)})
    if not hechos and out.get("respuesta"):
        hechos = {"respuesta": out["respuesta"]}
        procedencia = [{"titulo": c["fuente"]} for c in candidatos[:2]]
    return {"tema": query, "hechos": hechos, "respuesta": out.get("respuesta", ""),
            "confianza": out.get("confianza"), "via": "local_rerank", "vigencia": "estable",
            "fuente_tipo": "libro", "fuentes": procedencia, "procedencia": procedencia,
            "nota": "conocimiento ingerido (OCR, UNVERIFIED) — re-rankeado por modelo sobre candidatos locales"}


def buscar_en_memoria(hcma, query, top_k=5):
    """Recupera hechos de la MEMORIA HCMA (solo verified/canónicos) por consulta.

    Es el 'hipocampo': lo que se escribió en la sesión. Delega en `hcma_recall.resolver` (el
    gate de recall determinista COMPARTIDO con el chat del dashboard): RECALL_PURO/HIT_CONTEXTUAL
    devuelven el hecho; NO_HIT devuelve None (cae a córtex/modelo). Así el orquestador y el chat
    usan UNA sola fuente de verdad para decidir si la memoria responde."""
    if hcma is None:
        return None
    try:
        from hcma_recall import resolver
        dec = resolver(query, hcma, top_k=top_k)
    except Exception:
        return None
    if dec.get("tipo") == "NO_HIT" or not dec.get("hechos"):
        return None
    return {"tema": query,
            "hechos": {f["key"]: f["value"] for f in dec["hechos"]},
            "via": "memoria", "vigencia": "estable", "verified": True,
            "procedencia": [{"fuente": f.get("fuente"), "unit": f.get("unit"),
                             "score": f.get("score")} for f in dec["hechos"]],
            "decision": dec["tipo"],
            "nota": "recuperado de la memoria HCMA (verified/canónico)"}


def busqueda(query, estado, hcma=None):
    """Módulo de búsqueda HÍBRIDO con modo determinista por defecto.

    Orden: 1) memoria HCMA (verified) → 2) retrieval local → 3) modelo → web.
    - ARQ_DETERMINISTA=1 (default): SOLO BM25 (sin LLM, sin embeddings, sin web).
      Cero error probabilístico: si no hay match, devuelve sin_datos y NO consulta
      modelo/web. La decisión la toma el camino determinista + validación.
    - ARQ_DETERMINISTA=0 (advisory): re-rank con LLM (+ denso con ARQ_DENSE=1),
      luego modelo → web. Mayor recall, pero NO determinista. Sus hechos entran
      UNVERIFIED a HCMA igual que los deterministas."""
    # 1) memoria (verified/canónico) — siempre primero, en ambos modos
    mem = buscar_en_memoria(hcma, query)
    if mem:
        return mem
    determinista = os.environ.get("ARQ_DETERMINISTA", "1") == "1"
    if determinista:
        res = buscar_local(query, top_k=40, min_score=0.2)
        if res:
            res["via"] = "local_determinista"
            return res
        return {"tema": query, "hechos": {}, "via": "local_determinista",
                "vigencia": "estable", "sin_datos": True,
                "nota": "sin match determinista (no se consulta LLM ni web)"}
    try:
        hits = (_recuperar_hibrido(query) if os.environ.get("ARQ_DENSE", "0") == "1"
                else _recuperar_local(query))
        if hits:
            rr = re_rank_local(query, hits)
            if rr and not rr.get("sin_datos") and rr.get("hechos"):
                return rr
    except Exception:
        pass  # si el retrieval/re-rank falla (red/modelo), degradar a modelo/web
    r = busqueda_modelo(query)
    if not isinstance(r.get("hechos"), dict):
        r["hechos"] = {}
    if r.get("vigencia") == "drift_temporal" and getattr(estado, "web_enabled", True):
        w = busqueda_web(query)
        if not isinstance(w.get("hechos"), dict):
            w["hechos"] = {}
        if w.get("hechos"):
            w["vigencia"] = "drift_temporal"
            w["modelo_dijo"] = r.get("hechos", {})
            w["conflicto"] = _hay_conflicto(r.get("hechos", {}), w.get("hechos", {}))
            return w
    r["via"] = "modelo"
    r["conflicto"] = False
    return r


def _token_num(s):
    """Parsea un token numérico con heurística de miles (locale AR/ES): grupos de 3 con
    cualquier separador → miles; si no, el separador es decimal."""
    s = s.strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    elif "." in s:
        if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
    try:
        x = float(s)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _num(v):
    """Convierte un valor a float (primer número, con heurística de miles). None si no hay."""
    if isinstance(v, bool) or isinstance(v, (dict, list, tuple)):
        return None
    m = re.search(r"-?\d[\d.,]*", str(v))
    return _token_num(m.group()) if m else None


def _separar_valor_unidad(v):
    """Devuelve (magnitud, unidad) de un valor tipo '1.5 USD/kg' o '1500 USD/t'."""
    if isinstance(v, bool) or isinstance(v, (dict, list, tuple)):
        return None, ""
    if isinstance(v, (int, float)):
        return float(v), ""
    s = str(v).strip()
    m = re.search(r"-?\d[\d.,]*", s)
    if not m:
        return None, ""
    num = _token_num(m.group())
    unidad = s[m.end():].strip().strip("=~:.").strip()
    return num, unidad


def _hay_conflicto(modelo_hechos, web_hechos, tol=0.25):
    """Compara valores numéricos por clave, convirtiendo unidades (pint) cuando ambas
    parsean. True si hay conflicto, False si todo coincide, None si no se puede concluir."""
    comparados = 0
    no_comparables = 0
    conflicto = False
    for k, v_modelo in modelo_hechos.items():
        if k not in web_hechos:
            continue
        a_num, a_unit = _separar_valor_unidad(v_modelo)
        b_num, b_unit = _separar_valor_unidad(web_hechos[k])
        if a_num is None or b_num is None:
            no_comparables += 1
            continue
        if a_unit and b_unit and a_unit != b_unit:
            n_a = _normalizar_unidad(a_unit)
            n_b = _normalizar_unidad(b_unit)
            if n_a and n_b:
                b_num = _convertir_valor(b_num, b_unit, a_unit)
            else:
                no_comparables += 1  # moneda/sin dimensión → no comparable
                continue
        comparados += 1
        denom = max(abs(a_num), abs(b_num), 1e-9)
        if abs(a_num - b_num) / denom > tol:
            conflicto = True
    if conflicto:
        return True
    if comparados == 0:
        return None
    if no_comparables > 0:
        return None
    return False


class HCMAMemoria:
    """Memoria versionada: solo `verified` es canónico; los derivados se invalidan.

    - `current(key)`  → última versión VERIFIED (para cálculo).
    - `candidates(key)`→ observaciones posteriores unverified/stale (conflictos).
    - Al escribir una nueva versión verified, se marcan `stale` sus descendientes
      (`derived_from`), así un resultado derivado nunca queda verified con un input viejo.
    """

    def __init__(self, seq_start=0):
        self.facts = {}
        self.log = []
        # Contador monotónico. Si algún día se persiste HCMA, pasar seq_start = última versión
        # + 1 para que las versiones no colisionen y el linaje (< versión) siga siendo válido.
        self._seq = seq_start

    def write(self, key, value, unit="", fuente="base", verified=False, meta=None,
              derived_from=None):
        # `verified` es DERIVADO, no declarado. Un resultado de `calculadora` (derivado) sin
        # `derived_from` NO puede auto-certificarse: la fuente confiable exige linaje no vacío.
        derived_from = derived_from or {}
        # no-op: re-escribir el MISMO valor verified no debe invalidar descendientes (B2)
        cur = self.current(key)
        if (cur is not None and verified and cur["value"] == value
                and cur["unit"] == unit and cur["fuente"] == fuente
                and cur["derived_from"] == derived_from):
            return cur["version"]
        if fuente == "calculadora" and not derived_from:
            verified = False
        elif self._auto_dependencia(key, derived_from):
            # auto-dependencia (directa o transitiva) de una versión anterior de la propia
            # clave: crea un ciclo de linaje. No auto-verificar.
            verified = False
        else:
            verified = bool(verified) and fuente in FUENTE_CONFIABLE and self._linaje_ok(derived_from)
        self._seq += 1
        version = self._seq
        fact = {"key": key, "value": value, "unit": unit, "version": version,
                "authority": verified, "verified": verified, "stale": False,
                "fuente": fuente, "meta": meta or {}, "derived_from": derived_from}
        if verified:
            for f in self.facts.get(key, []):
                if f["authority"]:
                    f["authority"] = False
            self._invalidate_descendants(key)
        self.facts.setdefault(key, []).append(fact)
        self.log.append({"op": "write", "key": key, "value": value, "unit": unit,
                         "version": version, "verified": verified, "fuente": fuente})
        return version

    def _auto_dependencia(self, key, derived_from):
        """True si `key` es alcanzable transitivamente desde las deps (auto-dependencia/ciclo)."""
        visitados = set()
        pila = list((derived_from or {}).keys())
        while pila:
            k = pila.pop()
            if k == key:
                return True
            if k in visitados:
                continue
            visitados.add(k)
            f = self.current(k)
            if f is not None:
                pila.extend((f.get("derived_from") or {}).keys())
        return False

    def _linaje_ok(self, derived_from):
        """True si cada dependencia declarada sigue siendo current, verified y con la misma versión."""
        for k, v in (derived_from or {}).items():
            f = self.current(k)
            if f is None or not f["verified"] or f["stale"] or f["version"] != v:
                return False
        return True

    def _invalidate_descendants(self, key):
        """Marca stale (transitivamente) todo fact derivado de una versión previa de `key`.

        Se propaga por VERSIÓN EXACTA: cuando un fact `kk@v` queda stale, solo se invalidan
        los descendientes con `derived_from[kk] == v` (no los de `kk@v'` con v'>v)."""
        def invalidar(k, ver):
            for kk, facts in self.facts.items():
                for f in facts:
                    df = f.get("derived_from") or {}
                    if df.get(k) == ver and not f["stale"]:
                        f["stale"] = True
                        f["authority"] = False
                        invalidar(kk, f["version"])
        for old in list(self.facts.get(key, [])):
            if old.get("verified") and not old.get("stale"):
                invalidar(key, old["version"])

    def current(self, key):
        for f in reversed(self.facts.get(key, [])):
            if f["authority"] and f["verified"] and not f["stale"]:
                return f
        return None

    def candidates(self, key):
        """Observaciones posteriores a la versión vigente (unverified/stale), no las ya superadas."""
        cur = self.current(key)
        cv = cur["version"] if cur else -1
        return [f for f in self.facts.get(key, []) if f["version"] > cv
                and (not f["verified"] or f["stale"])]

    def state(self):
        """Estado canónico para el LLM y reporte: SIN `verified` (no exponer el estado interno
        de confianza al LLM, para no sesgarlo ni dejarlo auto-certificarse)."""
        out = {}
        for k in self.facts:
            f = self.current(k)
            if f is not None:
                out[k] = {"value": f["value"], "unit": f["unit"], "fuente": f["fuente"]}
        return out

    def all_verified(self, keys):
        """Propagación de contaminación: True solo si TODOS los inputs existen y son verified.
        (Evita la verdad vacía de all([]).)"""
        keys = list(keys)
        if not keys:
            return False
        for k in keys:
            f = self.current(k)
            if f is None or not f["verified"] or f["stale"]:
                return False
        return True

    def pending_unverified(self):
        """Hechos que todavía necesitan validación: unverified/stale y posteriores a la versión
        vigente (si existe). No reporta versiones ya superadas por una versión current verified."""
        out = {}
        for k, v in self.facts.items():
            cur = self.current(k)
            if cur is None:
                pend = [f for f in v if not f["verified"] or f["stale"]]
            else:
                pend = [f for f in v if f["version"] > cur["version"]
                        and (not f["verified"] or f["stale"])]
            if pend:
                out[k] = pend
        return out

    def buscar(self, query, top_k=5, min_score=0.5):
        """Recupera hechos vigentes (verified) por consulta. Determinista (overlap de tokens
        sobre state()). Misma interfaz que HCMAMemoriaSQLite.buscar (que usa FTS5).
        `min_score` evita cortar el pipeline córtex por un match débil de una sola palabra."""
        estado = self.state()
        if not estado:
            return []
        qt = set(_tokens(query))
        if not qt:
            return []
        scored = []
        for key, v in estado.items():
            txt = f"{key} {v.get('value', '')} {v.get('unit', '')}"
            inter = qt & set(_tokens(txt))
            if inter:
                scored.append((len(inter) / len(qt), key, v))
        scored = [(s, k, v) for (s, k, v) in scored if s >= min_score]
        if not scored:
            return []
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [{"key": k, "value": v.get("value"), "unit": v.get("unit"),
                 "fuente": v.get("fuente"), "score": round(s, 3)}
                for (s, k, v) in scored[:top_k]]


class Estado:
    def __init__(self):
        self.web_enabled = os.environ.get("ARQ_WEB", "1") == "1"   # búsqueda web real presente
        self.frontier_budget = 1
        self.frontier_calls = 0
        self.frontier_failed = set()


# La verificación real es la PROCEDENCIA; el rango y la dimensión son red de seguridad.
# `refuerzo`/`orquestador` NO están aquí a propósito: la confianza de un uso de skill se
# hereda de sus dependencias (linaje), no de una etiqueta de fuente (ver skills.py).
# Las fuentes confiables son SOLO autoridades independientes. `test` NO es una autoridad:
# es un resultado mecánico, y su confianza se HEREDA por linaje (`calculadora`), no por una
# etiqueta de fuente. (La auto-promoción `fuente='test', verified=True` fue el bypass del
# postmortem de skills; jamás reintroducir una fuente para desbloquear un verified bloqueado.)
FUENTE_CONFIABLE = {"calculadora", "usuario", "ingeniero", "tabla"}

# Dimensión/unidad canónica esperada por cantidad (la del rango). Se usa para convertir el
# valor a esa unidad antes de comparar el rango y para rechazar unidades de dimensión equivocada.
DIMENSIONES = {
    "carga_diseno_kN": "kN",
    "momento_maximo_kNm": "kN*m",
    "cortante_kN": "kN",
    "seccion_requerida_cm3": "cm**3",
    "potencia_W": "W",
    "potencia_kW": "kW",
    "corriente_A": "A",
    "seccion_cable_mm2": "mm**2",
    "diametro_cable_mm": "mm",
    "resistencia_ohm": "ohm",
    "peso_cobre_kg": "kg",
    "presion_buje_MPa": "MPa",
    "velocidad_superficial_m_s": "m/s",
    "pv_MPa_m_s": "MPa*m/s",
    "peso_viga_t_hormigon_kg": "kg",
}

# Ambigüedad dimensional: unidades con la MISMA dimensión pero distinto significado
# (momento kN·m vs energía J). Acá se fuerza la allowlist textual (formas normalizadas).
UNIDADES_VALIDAS = {
    "momento_maximo_kNm": {"N*m", "kN*m", "N*mm", "kN*cm"},
    "seccion_requerida_cm3": {"cm**3", "mm**3", "m**3"},
    "seccion_cable_mm2": {"mm**2", "cm**2", "m**2"},
}


def _normalizar_unidad(u):
    """Convierte una unidad legible ('kN·m', 'cm3', 'mm2', 'MPa·m/s') a pint-parseable.
    Devuelve None si es moneda (sin dimensión) o vacía."""
    u = str(u).strip()
    if u in ("", "1", "dimensionless"):
        return "dimensionless"
    if "USD" in u or "ARS" in u or "$" in u:
        return None  # moneda: no aplica chequeo dimensional
    u = (u.replace("·", "*").replace("²", "**2").replace("³", "**3")
          .replace("Ω", "ohm").replace("µ", "u").replace("μ", "u"))
    # unidades pegadas comunes (antes del exponente): kNm→kN*m, Nmm→N*mm, etc.
    for a, b in (("kNmm", "kN*mm"), ("Nmm", "N*mm"), ("kNm", "kN*m"),
                 ("kgm", "kg*m"), ("Nm", "N*m"), ("kNcm", "kN*cm")):
        u = u.replace(a, b)
    # exponente: m2/mm2/cm3/s2/... → m**2/mm**2/cm**3/s**2
    u = re.sub(r"\b([a-zA-Z]+)([23])\b", r"\1**\2", u)
    return u


def _convertir_a_canonica(val, unit, canonica):
    """Convierte val a la unidad canónica (pint). Devuelve (magnitud, error).
    El error es None si la unidad parsea y la dimensión coincide."""
    n_unit = _normalizar_unidad(unit)
    n_canon = _normalizar_unidad(canonica)
    if n_unit is None or n_canon is None:
        return val, "moneda/sin dimensión no comparable"
    try:
        q = ureg.Quantity(val, n_unit)
        c = ureg.Quantity(1.0, n_canon)
    except Exception as e:
        return val, f"unidad no parseable: {e}"
    if q.dimensionality != c.dimensionality:
        return val, f"dimensión {q.dimensionality} ≠ esperada {c.dimensionality}"
    return float(q.to(n_canon).magnitude), None


def verificar(quantities, ranges, dimensiones=None, allowlist=None):
    """Verifica rango + dimensión de cantidades. `dimensiones`/`allowlist` vienen del
    dominio (B1): el núcleo solo aporta defaults para no romper tests sueltos."""
    if dimensiones is None:
        dimensiones = DIMENSIONES
    if allowlist is None:
        allowlist = UNIDADES_VALIDAS
    errs = []
    for q in quantities:
        name = q.get("name", "?")
        val = q.get("value")
        unit = q.get("unit", "")
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            errs.append(f"{name}: valor no numérico ({val!r})")
            continue
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            errs.append(f"{name}: valor NaN/inf")
            continue
        if not unit:
            errs.append(f"{name}: falta unidad")
            continue
        canon = dimensiones.get(name)
        mag = val
        if canon is not None:
            n_unit = _normalizar_unidad(unit)
            allow = allowlist.get(name)
            if allow is not None and n_unit not in allow:
                errs.append(f"{name}: unidad '{unit}' no permitida (esperada {allow})")
                continue
            mag, dimerr = _convertir_a_canonica(val, unit, canon)
            if dimerr:
                errs.append(f"{name}: {dimerr}")
                continue
        else:
            norm = _normalizar_unidad(unit)
            if norm is not None:
                try:
                    ureg.Quantity(val, norm)
                except Exception:
                    errs.append(f"{name}: unidad '{unit}' no reconocida")
        lo, hi = ranges.get(name, (None, None))
        if lo is not None and hi is not None and not (lo <= mag <= hi):
            errs.append(f"{name}: {val} {unit} fuera de rango [{lo}, {hi}]")
    return (not errs, errs)


PLAN_SYS = (
    "Eres un orquestador. Dada una tarea y una lista de dominios disponibles, dividí la tarea "
    "en pasos secuenciales, cada uno con el dominio que lo resuelve. Devolvé SOLO JSON "
    "(json_object) con:\n"
    '{"pasos":[{"dominio":"<id exacto de la lista>","paso":"<descripción>"}]}\n'
    "Reglas: usá SOLO ids de la lista de dominios disponibles; ordená los pasos para que las "
    "salidas de un dominio alimenten al siguiente; 1-5 pasos."
)


def sys_especialista(dominio):
    return (
        f"Eres un especialista en {dominio['nombre']}. Devuelve SOLO JSON (json_object) con:\n"
        '- "status": uno de ["ok","needs_input","needs_compute","needs_lookup","uncertain"]\n'
        '- "missing_inputs": lista de {"name","question"} (solo si needs_input)\n'
        '- "calc_request": {"formula_id","inputs":[{"name","value","unit"}]} (solo si needs_compute)\n'
        '- "quantities": lista de {"name","value","unit"} (solo si status=ok)\n'
        '- "assumptions": lista de {"name","value","unit","why"}\n'
        '- "note": frase corta (máx 200 chars)\n'
        "REGLAS DURAS:\n"
        "1. NUNCA hagas aritmética: si hay que calcular, status=needs_compute.\n"
        "2. Todo número lleva 'unit'.\n"
        "3. Si te falta un dato del usuario, status=needs_input.\n"
        "4. Si no sabés o es un juicio difícil, status=uncertain. No inventes.\n"
        f"\nFÓRMULAS DISPONIBLES: {list(dominio['formulas'].keys())}\n"
        f"EJEMPLOS DE RUTEO:\n{dominio['ejemplos']}"
    )


SYS_COMPRAS = (
    "Eres un especialista en compras/abastecimiento en Argentina. Dada la lista de materiales, "
    "determina QUÉ comprar y DÓNDE (proveedor real: Acindar/Ternium/Siderar para acero, "
    "Sodimac/Easy para construcción, casas de eléctrica para cobre/bobinados). Devuelve SOLO JSON "
    "(json_object) con el mismo contrato: status, quantities, missing_inputs, lookup_query, "
    "assumptions, note. Si no sabés el precio real de un proveedor argentino, status=needs_lookup "
    "y no inventes precios."
)


SYS_BUSQUEDA_MODELO = (
    "Eres un módulo de búsqueda/consulta técnica (materiales, precios, normativa, geología, "
    "etc.). Dado un tema, devolvé SOLO JSON (json_object) con:\n"
    '- "tema": string (el tema consultado)\n'
    '- "hechos": objeto {clave: valor} con los datos concretos (números con unidad si aplica)\n'
    '- "fuente_tipo": "constante_fisica" | "norma_tecnica" | "precio_mercado" | "catalogo" | "otro"\n'
    '- "vigencia": "estable" | "drift_temporal"\n'
    '- "nota": aclaración breve sobre fiabilidad y fuente\n'
    "Reglas: si es un precio, fecha o norma que cambia con el tiempo, vigencia=drift_temporal y "
    "aclará que es aproximado; si es una constante física o tabla estándar, vigencia=estable. "
    "No inventes un precio exacto: dale un rango y marcá drift_temporal."
)


SYS_BUSQUEDA_WEB = (
    "Eres un módulo de búsqueda con acceso a la WEB. Basate SOLO en los resultados de búsqueda "
    "obtenidos; citá la fuente de cada dato. Devolvé SOLO JSON (json_object) con:\n"
    '- "hechos": objeto {clave: valor} con los datos encontrados (números con unidad)\n'
    '- "nota": aclaración breve\n'
    "Si no hay resultado fiable, devolvé hechos vacíos. No inventes."
)


def preguntar_usuario(faltantes, demo_inputs, demo_lookup=None):
    demo_lookup = demo_lookup or {}
    print(f"  👤 Se pide al usuario: {len(faltantes)} input(s).")
    if DEMO:
        resp = {}
        for i in faltantes:
            name = i.get("name")
            if name == "lookup":
                resp.update(demo_lookup)
            else:
                resp[name] = demo_inputs.get(name, "demo")
        print(f"     [demo] responde: {json.dumps(resp, ensure_ascii=False)}")
        return resp
    out = {}
    for i in faltantes:
        name = i.get("name", "?")
        out[name] = input(f"     {i.get('question', name)} ({name}): ").strip()
    return out


def _req(hcma, name):
    f = hcma.current(name)
    if f is None:
        raise ValueError(f"input requerido ausente: {name}")
    return f["value"]


def _registrar_uso_skill(hcma, dominio, res):
    """Registra el USO de un skill (dominio) como EVENTO con linaje, a prueba de fallos.

    Solo se registran los usos EXITOSOS (accion 'in_range'). Cada uso es UN hecho
    `skill_uso:<id>:<seq>` con linaje hacia (a) la versión del skill que se ejecutó y (b)
    todos los resultados canónicos que lo justificaron. Si alguno se invalida, version
    collapse marca ese uso `stale` y el contador (vista) se recomputa — retractación real.

    Es opcional: si `skills` no está disponible, no interrumpe la corrida (como telemetría).
    """
    if _skills_mod is None:
        return
    qs = [q for q in res.get("quantities", []) if isinstance(q, dict) and q.get("name")]
    if not qs:
        return
    skill_id = dominio.get("clave") or dominio.get("nombre")
    if not isinstance(skill_id, str) or not skill_id:
        return
    try:
        # resultados canónicos (verified ∧ authority ∧ ¬stale) que justifican este uso
        refs = []
        for q in qs:
            f = hcma.current(q["name"])
            if f is not None and f["verified"] and f["authority"] and not f["stale"]:
                refs.append((q["name"], f["version"]))
        if not refs:
            return  # sin resultado canónico no hay nada que justifique un uso exitoso
        # declarar el skill (no-op si ya está igual); obtengo su versión para el linaje
        v_skill = _skills_mod.registro(hcma, skill_id, dominio.get("nombre", skill_id),
                                       fuente="ingeniero", verified=True,
                                       meta={"dominio": skill_id})
        _skills_mod.registrar_uso(hcma, skill_id, v_skill, refs, exito=True)
    except Exception as e:
        print(f"     ⚠️ registro de skill falló (no crítico): {e}")


def escribir_resultado(hcma, res):
    fuente = res.get("fuente", "base")
    verified = fuente in FUENTE_CONFIABLE
    derived_from = res.get("derived_from") or {}
    for q in res.get("quantities", []):
        hcma.write(q["name"], q["value"], unit=q.get("unit", ""),
                   fuente=fuente, verified=verified, derived_from=derived_from)


def _escribir_inputs(hcma, resp, dominio):
    """Escribe inputs del usuario con la UNIDAD del spec y convierte a número si es numérico.

    Solo acepta nombres declarados en `dominio["inputs"]` (A9) y valida el TIPO antes de
    escribir (B8): un input no numérico NO queda verified."""
    specs = {i["name"]: i for i in dominio["inputs"]}
    for n, v in resp.items():
        spec = specs.get(n)
        if spec is None:
            print(f"     ↳ ignorando input no declarado del LLM: {n}")
            continue
        unit = spec.get("unit", "")
        tipo = spec.get("tipo", "num" if unit not in ("", "1") else "str")
        if tipo == "num":
            try:
                v = float(v)
            except (TypeError, ValueError):
                print(f"     ⚠️ input {n}='{v}' no numérico → NO se escribe como verified")
                continue
            if not math.isfinite(v):
                print(f"     ⚠️ input {n}={v} no finito → NO se escribe")
                continue
        hcma.write("in_" + n, v, unit=unit, fuente="usuario", verified=True)


def _validar_dominio(dominio):
    """Guardrail de diseño de dominio (ver INVARIANTES_DOMINIO.md).

    Evita la clase de bug "parámetro del caso presupuesto como constante":
    (1) todo nombre que lee el cálculo debe estar DECLARADO como input (se pregunta,
        no se presupone); (2) toda constante universal debe estar DOCUMENTADA en
        `constantes` con `fuente` y `porque_no_input` (auditable por un revisor)."""
    errs = []
    declarados = {i["name"] for i in dominio.get("inputs", [])}
    salidas = set(dominio.get("dimensiones", {}))
    for n in dominio.get("calc_inputs", []):
        if n not in declarados:
            errs.append(f"calc_input '{n}' no está declarado en 'inputs' (se presupone)")
    for fid, spec in dominio.get("formulas", {}).items():
        if isinstance(spec, dict):
            for n in spec.get("inputs", []):
                # input de fórmula puede ser: input declarado, o salida de OTRA fórmula del dominio
                if n not in declarados and n not in salidas:
                    errs.append(f"fórmula '{fid}' lee input '{n}' no declarado ni salida del dominio")
    consts = dominio.get("constantes")
    if consts is None:
        print(f"   ⚠️ dominio '{dominio.get('nombre')}' no documenta 'constantes' "
              "(revisar qué se presupone y por qué)")
    else:
        for c in consts:
            if not c.get("fuente"):
                errs.append(f"constante '{c.get('name')}' sin 'fuente'")
            if not c.get("porque_no_input"):
                errs.append(f"constante '{c.get('name')}' sin 'porque_no_input'")
    dims, ranges = dominio.get("dimensiones", {}), dominio.get("ranges", {})
    for n in dims:
        if n not in ranges:
            errs.append(f"salida '{n}' tiene dimensión pero no rango")
    if errs:
        raise RuntimeError("dominio inválido: " + "; ".join(errs))


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRATO CANÓNICO DE ENTREGABLE (ver INVARIANTES_DOMINIO.md, Invariante 2)
# El deliverable es un DATO (build_entregable); reporte y PDF son renderers genéricos
# que recorren la MISMA tupla de secciones. No hay render por dominio.
# ═══════════════════════════════════════════════════════════════════════════════
SECCIONES_ENTREGABLE = (
    "especificaciones",   # derivada de dom["inputs"]
    "esquema",            # plano/unifilar: fn(inputs, Q) -> str monoespaciado
    "calculo",            # derivada de dom["dimensiones"]
    "materiales",         # BOM: fn(inputs, Q) -> [(item, cant, unit)]
    "presupuesto",        # derivada: join(materiales, precios) + subtotal
    "exclusiones",        # lista NO vacía de limitaciones/supuestos
)
TITULOS = {
    "especificaciones": "Especificaciones",
    "esquema": "Plano / esquema",
    "calculo": "Cálculo",
    "materiales": "Lista de materiales",
    "presupuesto": "Presupuesto aproximado (drift temporal)",
    "exclusiones": "Supuestos y exclusiones",
}
# Dominios viejos aún sin 'entregable': warning (no abort) para no romperlos.
DEUDA_ENTREGABLE = {"barra_dominadas", "motor_electrico", "motor_bobinado"}

PREAMBULO_TEX = r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{geometry}
\geometry{margin=2.2cm}
\setlength{\parindent}{0pt}
\setlength{\parskip}{6pt}
\usepackage{booktabs}
\usepackage{hyperref}
\usepackage{graphicx}
\hypersetup{colorlinks=true,urlcolor=blue}

\title{\vspace{-1.2cm}<<TITULO>>\\[2mm]
\large HCMA — memoria de cálculo y cómputo (deliverable canónico)}
\author{Mauro Javier Silva}
\date{2026-11-03}

\begin{document}
\maketitle
"""


def esc_latex(s):
    s = str(s)
    return (s.replace("\\", r"\textbackslash{}")
             .replace("&", r"\&").replace("%", r"\%").replace("#", r"\#")
             .replace("_", r"\_").replace("$", r"\$")
             .replace("^", r"\^{}").replace("~", r"\~{}")
             .replace("{", r"\{").replace("}", r"\}"))


def _fmt_unit(u):
    return "—" if u in ("", "1") else u


def _bloque_txt(b):
    t = b.get("tipo")
    if t == "tabla":
        lineas = ["   " + "  ".join(str(c) for c in b["columnas"])]
        for f in b["filas"]:
            lineas.append("   " + "  ".join(str(x) for x in f))
        if b.get("pie"):
            lineas.append("   " + str(b["pie"]))
        return "\n".join(lineas)
    if t == "mono":
        return b["texto"]
    if t == "lista":
        return "\n".join("   - " + str(i) for i in b["items"])
    return ""


def _bloque_tex(b):
    t = b.get("tipo")
    if t == "tabla":
        n = len(b["columnas"])
        spec = "l" + "r" * (n - 1)
        hdr = " & ".join(esc_latex(c) for c in b["columnas"])
        filas = [" & ".join(esc_latex(x) for x in f) for f in b["filas"]]
        cuerpo = " \\\\".join(filas) + " \\\\"
        pie = (f"\n\\par\\small {esc_latex(b['pie'])}" if b.get("pie") else "")
        return (f"\\begin{{table}}[h]\n\\centering\n\\small\n"
                f"\\begin{{tabular}}{{{spec}}}\n\\toprule\n{hdr} \\\\\n\\midrule\n"
                f"{cuerpo}\n\\bottomrule\n\\end{{tabular}}\n{pie}\n\\end{{table}}")
    if t == "mono":
        return "\\begin{verbatim}\n" + b["texto"] + "\n\\end{verbatim}"
    if t == "lista":
        items = "\n".join("  \\item " + esc_latex(i) for i in b["items"])
        return "\\begin{itemize}\n" + items + "\n\\end{itemize}"
    return ""


def build_entregable(dom_id, dom, inputs, Q):
    """Construye el Entregable (dato puro) desde la spec del dominio. Única fuente."""
    e = dom["entregable"]
    moneda = e.get("moneda", "USD")
    etq = e.get("etiquetas", {})
    mat = e["materiales"](inputs, Q) if e["materiales"] else []
    precios = {p[0]: p for p in e["precios"]}
    filas, total, usados = [], 0.0, []
    for item, cant, _unit in mat:
        # Pieza sin precio catalogado -> NO revienta: se marca como 'pendiente de cotización'
        # (drift temporal honesto) con costo None, en vez de un KeyError que cortaba el BOM.
        if item in precios:
            precio = precios[item][2]
            costo = round(cant * precio, 2)
            total += costo
            usados.append(precios[item])
        else:
            precio = "pendiente de cotización"
            costo = None
            usados.append((item, "unidad", None, "sin precio catalogado (drift temporal)"))
        filas.append([item, cant, precio, costo])
    notas = e["notas"](inputs, Q) if e.get("notas") else []
    filas_espec = [[i["question"], inputs[i["name"]], _fmt_unit(i["unit"])]
                   for i in dom["inputs"]]
    filas_calc = [[etq.get(k, k), Q[k], dom["dimensiones"][k]] for k in dom["dimensiones"]]
    # título: string fijo, o callable(inputs) para derivarlo del caso (ej. potencia/carga).
    titulo = e["titulo"]
    if callable(titulo):
        titulo = titulo(inputs)
    return {
        "dominio": dom_id, "titulo": titulo, "inputs": inputs, "Q": Q,
        "presupuesto_total": round(total, 2), "precios_usados": usados,
        "secciones": {
            "especificaciones": {"tipo": "tabla", "columnas": ["Parámetro", "Valor", "Unidad"],
                                 "filas": filas_espec},
            "esquema": {"tipo": "mono", "texto": e["esquema"](inputs, Q)},
            "calculo": {"tipo": "tabla", "columnas": ["Cantidad", "Valor", "Unidad"],
                        "filas": filas_calc},
            "materiales": {"tipo": "tabla", "columnas": ["Item", "Cant.", "Unidad"],
                           "filas": [[m[0], m[1], m[2]] for m in mat]},
            "presupuesto": {"tipo": "tabla", "columnas": ["Item", "Cant.", f"{moneda}/u", moneda],
                            "filas": filas,
                            "pie": f"Subtotal materiales: {total:.2f} {moneda} (drift temporal, validar proveedor)"},
            "exclusiones": {"tipo": "lista", "items": list(e["exclusiones"]) + list(notas)},
        },
    }


def render_consola(ent):
    lineas = ["=" * 60, ent["titulo"]]
    for s in SECCIONES_ENTREGABLE:
        lineas.append(f"\n   {TITULOS[s]}:")
        lineas.append(_bloque_txt(ent["secciones"][s]))
    return "\n".join(lineas)


def render_latex(ent):
    cuerpo = []
    for i, s in enumerate(SECCIONES_ENTREGABLE, 1):
        cuerpo.append(f"\\section*{{{i}. {esc_latex(TITULOS[s])}}}\n")
        cuerpo.append(_bloque_tex(ent["secciones"][s]))
        cuerpo.append("")
    return (PREAMBULO_TEX.replace("<<TITULO>>", esc_latex(ent["titulo"]))
            + "\n".join(cuerpo) + "\n\\end{document}\n")


def registrar_presupuesto(hcma, ent):
    """Efecto secundario HCMA: los precios entran UNVERIFIED (drift_temporal), nunca canónicos."""
    for item, unit, precio, proveedor in ent["precios_usados"]:
        hcma.write(f"precio_{item}", precio, unit=unit, fuente="busqueda", verified=False,
                   meta={"vigencia": "drift_temporal", "proveedor": proveedor})
    hcma.write("presupuesto_total_usd", ent["presupuesto_total"], unit="USD",
               fuente="busqueda", verified=False, meta={"vigencia": "drift_temporal"})


def report_generico(dom_id, dom):
    """Reemplaza a todos los report_<dominio> a mano: build + registrar + imprimir + gate."""
    def _report(hcma):
        inputs = {i["name"]: hcma.current(f"in_{i['name']}")["value"] for i in dom["inputs"]}
        Q = {k: hcma.current(k)["value"] for k in dom["dimensiones"]}
        ent = build_entregable(dom_id, dom, inputs, Q)
        registrar_presupuesto(hcma, ent)
        print(render_consola(ent))
        if dom.get("requerimientos"):
            matriz = _validar_cobertura(dom, inputs, Q, ent)
            print("\n" + "=" * 60)
            print("✔ Gate de aceptación — cobertura de requerimientos:")
            for m in matriz:
                print(f"   [{m['estado']:>11}] {m['id']}: {m['texto']}")
                for d in m["detalles"]:
                    print(f"              {d}")
        return ent
    return _report


def _validar_entregable(dom_id, dom):
    """Guardrail del contrato de entregable (Invariante 2). Aborta si un dominio NUEVO
    omite una sección; warning para dominios viejos en DEUDA_ENTREGABLE."""
    e = dom.get("entregable")
    if e is None:
        if dom_id in DEUDA_ENTREGABLE:
            print(f"   ⚠️ dominio '{dom_id}' en deuda: sin 'entregable' (retroajustar)")
            return
        raise RuntimeError(f"dominio '{dom_id}' sin 'entregable' (contrato de deliverable)")
    for k in ("titulo", "esquema", "materiales", "precios", "exclusiones"):
        if k not in e:
            raise RuntimeError(f"{dom_id}: entregable sin '{k}'")
    if e["materiales"] is None and not e.get("porque_sin_materiales"):
        raise RuntimeError(f"{dom_id}: materiales=None sin justificación")
    if not e["exclusiones"]:
        raise RuntimeError(f"{dom_id}: exclusiones vacías")
    inp = dict(dom["demo_inputs"])
    Q = {o["name"]: o["value"] for o in dom["calcular"](inp)}
    ent = build_entregable(dom_id, dom, inp, Q)  # KeyError si un item no tiene precio
    for s in SECCIONES_ENTREGABLE:
        b = ent["secciones"][s]
        if not (b.get("filas") or b.get("texto") or b.get("items")):
            raise RuntimeError(f"{dom_id}: sección '{s}' vacía")
    items = {m[0] for m in e["materiales"](inp, Q)} if e["materiales"] else set()
    precios = {p[0] for p in e["precios"]}
    if not items <= precios:
        raise RuntimeError(f"{dom_id}: items sin precio: {items - precios}")
    corr = dom.get("correccion", {})
    nombre = corr.get("input", "").removeprefix("in_")
    if nombre in inp:
        inp2 = dict(inp)
        inp2[nombre] = corr.get("valor")
        Q2 = {o["name"]: o["value"] for o in dom["calcular"](inp2)}
        if e["esquema"](inp2, Q2) == e["esquema"](inp, Q):
            raise RuntimeError(f"{dom_id}: esquema no depende del caso (hardcodeado)")
    total = sum(f[-1] for f in ent["secciones"]["presupuesto"]["filas"])
    if abs(total - ent["presupuesto_total"]) >= 0.01:
        raise RuntimeError(f"{dom_id}: presupuesto_total ≠ suma de filas")
    for render in (render_consola, render_latex):
        txt = render(ent)
        for s in SECCIONES_ENTREGABLE:
            if TITULOS[s] not in txt:
                raise RuntimeError(f"{dom_id}: {render.__name__} omite '{s}'")


def _eval_evidencia(ev, dom, inputs, Q, ent):
    """Evalúa UNA evidencia de un requerimiento → dict(ok, seccion, detalle)."""
    t = ev["tipo"]
    S = ent["secciones"]
    if t == "salida":
        k = ev["name"]
        if k not in Q or Q[k] is None:
            return {"ok": False, "seccion": "calculo", "detalle": f"salida '{k}' ausente"}
        return {"ok": True, "seccion": "calculo", "detalle": f"{k}={Q[k]}"}
    if t == "input":
        k = ev["name"]
        if inputs.get(k) in (None, ""):
            return {"ok": False, "seccion": "especificaciones", "detalle": f"input '{k}' no provisto"}
        return {"ok": True, "seccion": "especificaciones", "detalle": f"{k}={inputs[k]}"}
    if t == "material":
        item = ev["item"]
        filas = [f for f in S["materiales"]["filas"] if f[0] == item and f[1] > 0]
        if not filas:
            return {"ok": False, "seccion": "materiales", "detalle": f"BOM sin ítem '{item}'"}
        if "cant_min" in ev and filas[0][1] < ev["cant_min"]:
            return {"ok": False, "seccion": "materiales", "detalle": f"cant {filas[0][1]} < {ev['cant_min']}"}
        return {"ok": True, "seccion": "materiales", "detalle": f"{item} x{filas[0][1]}"}
    if t == "esquema":
        txt = S["esquema"]["texto"]
        m = re.search(ev["regex"], txt, re.IGNORECASE)
        return {"ok": bool(m), "seccion": "esquema",
                "detalle": f"regex /{ev['regex']}/ " + ("OK" if m else "no encontrada")}
    if t == "seccion":
        b = S.get(ev["name"])
        ok = bool(b and (b.get("filas") or b.get("texto") or b.get("items")))
        return {"ok": ok, "seccion": ev["name"], "detalle": "presente" if ok else "vacía/ausente"}
    if t == "derivacion":
        k = ev["salida"]
        declarados = {i["name"] for i in dom["inputs"]}
        if ev.get("no_input") and k in declarados:
            return {"ok": False, "seccion": "calculo", "detalle": f"'{k}' sigue siendo input (debía derivarse)"}
        if k not in Q:
            return {"ok": False, "seccion": "calculo", "detalle": f"'{k}' no es salida"}
        return {"ok": True, "seccion": "calculo", "detalle": f"{k}={Q[k]} derivada"}
    if t == "precio":
        malos = [p for p in ent["precios_usados"] if "demo" in str(p[3]).lower()]
        return {"ok": not malos, "seccion": "presupuesto",
                "detalle": ("precios demo detectados: " + ", ".join(p[0] for p in malos))
                if malos else f"{len(ent['precios_usados'])} precios no-demo"}
    raise RuntimeError(f"tipo de evidencia desconocido: {t}")


def _validar_cobertura(dom, inputs, Q, ent):
    """Evalúa todos los requerimientos → matriz [{id, texto, estado, detalles}]."""
    matriz = []
    for r in dom.get("requerimientos", []):
        evs = [dict(ev, **_eval_evidencia(ev, dom, inputs, Q, ent)) for ev in r["satisface_con"]]
        n_ok = sum(e["ok"] for e in evs)
        estado = "cubierto" if n_ok == len(evs) else ("parcial" if n_ok > 0 else "no_cubierto")
        matriz.append({"id": r["id"], "texto": r["texto"], "estado": estado,
                       "detalles": [f"{'OK' if e['ok'] else 'FALTA'}: {e['detalle']}" for e in evs]})
    return matriz


def _validar_requerimientos(dom_id, dom):
    """Guardrail ESTÁTICO de requerimientos: estructura + referencias válidas (no evalúa)."""
    reqs = dom.get("requerimientos")
    if reqs is None:
        if dom_id in DEUDA_ENTREGABLE:
            return
        raise RuntimeError(f"dominio '{dom_id}' sin 'requerimientos' (contrato de completitud)")
    if not reqs:
        raise RuntimeError(f"dominio '{dom_id}': requerimientos vacíos")
    declarados = {i["name"] for i in dom["inputs"]}
    dims = set(dom["dimensiones"])
    precios = {p[0] for p in dom["entregable"]["precios"]}
    for r in reqs:
        for campo in ("id", "texto", "satisface_con"):
            if campo not in r:
                raise RuntimeError(f"{dom_id}: requerimiento sin '{campo}'")
        if not r["satisface_con"]:
            raise RuntimeError(f"{dom_id}: requerimiento {r.get('id')} sin evidencias")
        for ev in r["satisface_con"]:
            t = ev["tipo"]
            if t == "salida" and ev.get("name") not in dims:
                raise RuntimeError(f"{dom_id}: evidencia salida '{ev.get('name')}' no es salida")
            if t == "input" and ev.get("name") not in declarados:
                raise RuntimeError(f"{dom_id}: evidencia input '{ev.get('name')}' no declarado")
            if t == "material" and ev.get("item") not in precios:
                raise RuntimeError(f"{dom_id}: evidencia material '{ev.get('item')}' sin precio")


def _run_tests(dominio, dom_id=None):
    """Valida la spec (cálculo + entregable + requerimientos) y corre tests dorados."""
    _validar_dominio(dominio)
    if dom_id is not None:
        _validar_entregable(dom_id, dominio)
        _validar_requerimientos(dom_id, dominio)
        if dominio.get("requerimientos"):
            inp = dict(dominio["demo_inputs"])
            Q = {o["name"]: o["value"] for o in dominio["calcular"](inp)}
            ent = build_entregable(dom_id, dominio, inp, Q)
            matriz = _validar_cobertura(dominio, inp, Q, ent)
            fallas = [m["id"] for m in matriz if m["estado"] != "cubierto"]
            if fallas:
                raise RuntimeError(f"{dom_id}: requerimientos NO cubiertos: {fallas}")
    tests = dominio.get("tests", [])
    for i, (inputs, esperado) in enumerate(tests):
        try:
            got = dominio["calcular"](inputs)
        except Exception as e:
            raise RuntimeError(f"test dorado {i} falló (excepción): {e}")
        got_d = {q["name"]: q["value"] for q in got}
        for k, v in esperado.items():
            if k not in got_d or abs(got_d[k] - v) > 0.01:
                raise RuntimeError(f"test dorado {i} falló: {k} = {got_d.get(k)} vs {v}")
    print(f"   ✔ tests dorados ({len(tests)}) pasaron")


def _expected_unit(dominio, name):
    """Unidad esperada para un input: la del spec (in_*) o la canónica de su salida (dimensiones)."""
    for i in dominio["inputs"]:
        if i["name"] == name:
            return i.get("unit", "")
    return dominio.get("dimensiones", {}).get(name, "")


def _convertir_valor(val, unit_actual, unit_esperada):
    """Convierte val de unit_actual a unit_esperada con pint si ambas parsean y son
    conmensurables; si no, devuelve val sin tocar (B2)."""
    if not unit_actual or not unit_esperada or unit_actual == unit_esperada:
        return val
    n_a = _normalizar_unidad(unit_actual)
    n_e = _normalizar_unidad(unit_esperada)
    if not n_a or not n_e:
        return val
    try:
        q = ureg.Quantity(val, n_a)
        return float(q.to(n_e).magnitude)
    except Exception:
        return val


def _ejecutar_calculo(hcma, dominio, fid=None):
    """Corre la calculadora (o una fórmula puntual) y devuelve
    (qs, verified, inputs_ok, derived_from, errs, missing).

    Lee de HCMA SOLO los inputs declarados: primero `in_<name>` (input de usuario) y si no
    existe `<name>` (salida previa en HCMA, p. ej. `potencia_W`). Guarda `derived_from` con
    la versión exacta de cada hecho consumido para poder invalidar el resultado si cambia.
    """
    if fid is not None:
        spec = dominio["formulas"].get(fid)
        fn = spec["fn"] if isinstance(spec, dict) else spec
        in_names = (spec["inputs"] if isinstance(spec, dict)
                    else dominio.get("calc_inputs") or [i["name"] for i in dominio["inputs"]])
    else:
        fn = dominio["calcular"]
        in_names = dominio.get("calc_inputs") or [i["name"] for i in dominio["inputs"]]

    inputs = {}
    derived_from = {}
    missing = []
    inputs_ok = bool(in_names)
    for name in in_names:
        f = hcma.current("in_" + name)
        key = "in_" + name
        if f is None:
            f = hcma.current(name)
            key = name
        if f is None:
            missing.append(name)
            inputs_ok = False
            continue
        if not f["verified"] or f["stale"]:
            inputs_ok = False
        inputs[name] = _convertir_valor(f["value"], f.get("unit", ""), _expected_unit(dominio, name))
        derived_from[key] = f["version"]
    if missing:
        return [], False, inputs_ok, derived_from, [], missing
    try:
        qs = fn(inputs)
    except Exception as e:
        return [], False, inputs_ok, derived_from, [f"fórmula falló: {e}"], []
    ok, errs = verificar(qs, dominio["ranges"], dominio.get("dimensiones"), dominio.get("allowlist"))
    verified = ok and inputs_ok and bool(qs)
    return qs, verified, inputs_ok, derived_from, errs, []


STATUS_VALIDOS = {"ok", "needs_input", "needs_compute", "needs_lookup", "uncertain"}


def _normalizar_salida(out):
    """Valida/normaliza el contrato JSON del LLM: un output malformado degrada a
    'uncertain' en vez de lanzar AttributeError/TypeError (E7)."""
    if not isinstance(out, dict):
        return {}
    if not isinstance(out.get("calc_request"), dict):
        out["calc_request"] = {}
    # A6: formula_id debe ser str (hashable); si no, None
    fid = out["calc_request"].get("formula_id")
    out["calc_request"]["formula_id"] = fid if isinstance(fid, str) else None
    for lista in ("quantities", "missing_inputs", "assumptions"):
        if not isinstance(out.get(lista), list):
            out[lista] = []
        out[lista] = [x for x in out[lista] if isinstance(x, dict)]
    # A6: missing_inputs con name str hashable
    out["missing_inputs"] = [x for x in out["missing_inputs"] if isinstance(x.get("name"), str)]
    # B9: cada quantity debe tener name str y value numérico (no bool)
    out["quantities"] = [q for q in out["quantities"]
                         if isinstance(q.get("name"), str)
                         and isinstance(q.get("value"), (int, float))
                         and not isinstance(q.get("value"), bool)]
    st = out.get("status")
    out["status"] = st if isinstance(st, str) and st in STATUS_VALIDOS else "uncertain"
    return out


def ejecutar_paso(dominio, paso, hcma, estado, system=None):
    """Router por status (genérico): usa las fórmulas y rangos del dominio.

    La acción devuelta es 'in_range'/'out_of_range' (el chequeo de RANGO+DIMENSIÓN),
    NO 'verified': la canonicidad la decide HCMA por PROCEDENCIA en el write."""
    sys_prompt = system or sys_especialista(dominio)
    ctx = json.dumps(hcma.state(), ensure_ascii=False) if hcma.state() else "(vacío)"
    out = _normalizar_salida(parse_json(call_llm(f"Tarea: {paso}\n\nEstado vigente (HCMA):\n{ctx}",
                                                MODEL, system=sys_prompt)))
    status = out.get("status", "uncertain")

    if status == "needs_input":
        return "user", {"faltan": out.get("missing_inputs", [])}

    if status == "needs_compute":
        fid = (out.get("calc_request") or {}).get("formula_id")
        if fid not in dominio["formulas"]:
            return "user", {"faltan": [{"name": "formula_id",
                                        "question": f"fórmula desconocida: {fid}"}]}
        # El LLM valida la fórmula; el PIPELINE determinista produce TODOS los outputs del
        # dominio (así cross-domain no depende de que el LLM acierte qué fórmula correr).
        qs, verified, inputs_ok, derived_from, errs, missing = _ejecutar_calculo(hcma, dominio)
        if missing:
            return "user", {"faltan": [{"name": m, "question": f"falta input: {m}"}
                                         for m in missing]}
        accion = "in_range" if verified else "out_of_range"
        return accion, {"quantities": qs,
                        "errores": errs or ([] if verified else ["inputs no verificados"]),
                        "fuente": "calculadora", "derived_from": derived_from}

    if status == "needs_lookup":
        # busqueda() ya degrada a modelo cuando no hay web; no gatear acá (B7).
        q = out.get("lookup_query") or paso
        query = q if isinstance(q, str) else json.dumps(q, ensure_ascii=False)
        print(f"     ↳ status=needs_lookup → búsqueda híbrida: {query[:80]}")
        return "lookup", busqueda(query, estado, hcma=hcma)

    if status == "uncertain":
        key = (dominio["nombre"], hashlib.sha256(
            json.dumps(hcma.state(), sort_keys=True).encode()).hexdigest()[:12])
        if key in estado.frontier_failed or estado.frontier_calls >= estado.frontier_budget:
            return "user", {"faltan": [{"name": "uncertain",
                                        "question": out.get("note") or "juicio sin resolver"}]}
        estado.frontier_calls += 1
        print("     ↳ status=uncertain → llamo a modelo frontera (Luna, breaker 1/corrida)")
        try:
            luna = _normalizar_salida(parse_json(call_llm(
                f"Tarea difícil: {paso}\n\nEstado (HCMA):\n{ctx}\n"
                f"El modelo base la marcó como uncertain. Respondé con el MISMO contrato JSON.",
                MODEL_FRONTERA, system=sys_prompt)))
        except Exception as e:
            print(f"     ⚠️ modelo frontera falló: {e} → devuelvo a usuario")
            estado.frontier_failed.add(key)
            return "user", {"faltan": [{"name": "uncertain",
                                        "question": out.get("note") or "juicio sin resolver"}]}
        ls = luna.get("status", "uncertain")
        if ls == "ok":
            qs = luna.get("quantities", [])
            if not qs:
                return "out_of_range", {"quantities": [], "errores": ["sin cantidades"],
                                        "fuente": "frontera"}
            ok, errs = verificar(qs, dominio["ranges"], dominio.get("dimensiones"), dominio.get("allowlist"))
            return ("in_range" if ok else "out_of_range"), {"quantities": qs, "errores": errs,
                                                              "fuente": "frontera"}
        if ls == "needs_compute":
            fid = (luna.get("calc_request") or {}).get("formula_id")
            if fid in dominio["formulas"]:
                # inputs se leen de HCMA (única fuente), pipeline completo (A5 + cross-domain)
                qs, verified, inputs_ok, derived_from, errs, missing = _ejecutar_calculo(hcma, dominio)
                if missing:
                    return "user", {"faltan": [{"name": m, "question": f"falta input: {m}"}
                                                 for m in missing]}
                return ("in_range" if verified else "out_of_range"), \
                       {"quantities": qs, "errores": errs, "fuente": "calculadora",
                        "derived_from": derived_from}
        estado.frontier_failed.add(key)
        return "user", {"faltan": [{"name": "uncertain",
                                    "question": luna.get("note") or "juicio sin resolver"}]}

    qs = out.get("quantities", [])
    if not qs:
        return "out_of_range", {"quantities": [], "errores": ["sin cantidades"],
                                "fuente": "especialista"}
    ok, errs = verificar(qs, dominio["ranges"], dominio.get("dimensiones"), dominio.get("allowlist"))
    return ("in_range" if ok else "out_of_range"), {"quantities": qs, "errores": errs,
                                                      "fuente": "especialista"}


def _norm_dominio(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _resolver_dominio(dom_id, dominios):
    """Devuelve (key, spec) de un id del plan; None si no resuelve o es ambiguo (B10)."""
    if not isinstance(dom_id, str):
        return None
    if dom_id in dominios:
        return dom_id, dominios[dom_id]
    n = _norm_dominio(dom_id)
    matches = [(k, v) for k, v in dominios.items()
               if _norm_dominio(k) == n or (n and n in _norm_dominio(v.get("nombre", "")))]
    return matches[0] if len(matches) == 1 else None


def _preflight_dominio(hcma, dominio):
    """Pide SOLO los inputs que aún no están en HCMA (input previo u output de otro dominio)."""
    faltantes = []
    for i in dominio["inputs"]:
        name = i["name"]
        if hcma.current("in_" + name) is None and hcma.current(name) is None:
            faltantes.append(i)
    if faltantes:
        resp = preguntar_usuario(faltantes, dominio["demo_inputs"], dominio.get("demo_lookup"))
        _escribir_inputs(hcma, resp, dominio)


def _manejar_accion(accion, res, dominio, hcma, estado):
    if accion == "in_range":
        escribir_resultado(hcma, res)  # HCMA decide verified por fuente
        _registrar_uso_skill(hcma, dominio, res)  # refuerza el skill (uso con linaje)
        print(f"   ✔ [{dominio['nombre']}] en rango: {res.get('quantities')}")
    elif accion == "out_of_range":
        # escribir como borrador unverified para trazabilidad (no todo-o-nada; B3)
        for q in res.get("quantities", []):
            if isinstance(q, dict) and q.get("name"):
                hcma.write(q["name"], q.get("value"), unit=q.get("unit", ""),
                           fuente="especialista", verified=False,
                           meta={"errores": res.get("errores", [])})
        print(f"   ✗ [{dominio['nombre']}] fuera de rango / no verificable: {res.get('errores')}")
    elif accion == "lookup":
        hechos = res.get("hechos", {}) or {}
        if not isinstance(hechos, dict):
            hechos = {}
        vigencia = res.get("vigencia", "drift_temporal")
        via = res.get("via", "modelo")
        fuentes = res.get("fuentes") or res.get("procedencia") or []
        for k, v in hechos.items():
            hcma.write(k, v, fuente="busqueda", verified=False,
                       meta={"vigencia": vigencia, "fuente_tipo": res.get("fuente_tipo", "otro"),
                             "via": via, "fuentes": fuentes,
                             "procedencia": res.get("procedencia", []),
                             "conflicto": res.get("conflicto", False)})
        print(f"   🔎 [{dominio['nombre']}] búsqueda [{vigencia}] vía {via}: {hechos}")
        if res.get("conflicto") is True:
            print("      ⚠️ CONFLICTO modelo vs web → NO se colapsa")
        elif res.get("conflicto") is None:
            print("      ⚠️ comparación no concluyente")
    else:
        print(f"   ⚠️ acción desconocida: {accion!r}")


def _aplicar_correccion(hcma, dominio):
    """Demo de version collapse al final del plan: corrige un input y verifica stale-use=0.
    Sin `assert` (se pierden con -O): reporta errores en vez de abortar (B5/B7)."""
    corr = dominio.get("correccion")
    if not corr:
        return
    f_actual = hcma.current(corr["input"])
    if f_actual is not None and f_actual["value"] == corr["valor"]:
        return  # ya corregido
    clave = dominio["clave"]
    print("=" * 60)
    print(f"✏️  El ingeniero corrige: {corr['input']} = {corr['valor']}.")
    nombre = corr["input"].replace("in_", "", 1)
    spec = next((i for i in dominio["inputs"] if i["name"] == nombre), {})
    hcma.write(corr["input"], corr["valor"], unit=spec.get("unit", ""),
               fuente="ingeniero", verified=True)
    try:
        qs2, verified2, inputs_ok2, derived_from2, errs2, missing2 = _ejecutar_calculo(hcma, dominio)
    except Exception as e:
        print(f"⚠️ recálculo falló: {e}\n")
        return
    for q in qs2:
        hcma.write(q["name"], q["value"], unit=q.get("unit", ""), fuente="calculadora",
                   verified=verified2, meta={"inputs_verified": inputs_ok2},
                   derived_from=derived_from2)
    f = hcma.current(clave)
    if f is None or not qs2:
        print(f"⚠️ no hay resultado canónico tras la corrección (errs={errs2}, missing={missing2}).\n")
        return
    esperado = next((q["value"] for q in qs2 if q["name"] == clave), None)
    if esperado is None:
        print(f"⚠️ '{clave}' no está en las salidas del recálculo.\n")
        return
    if abs(f["value"] - esperado) >= 1e-9:
        print(f"⚠️ ¡stale-use! {clave}={f['value']} vs recalculado {esperado}")
    else:
        print(f"🔎 '{clave}' vigente = {f['value']} {f['unit']} "
              f"(version {f['version']}, fuente {f['fuente']})")
        print("✅ stale-use = 0: usa la versión corregida, no la vieja.\n")


def _normalizar_plan(plan, ids, max_pasos=5):
    """Normaliza el plan del LLM: lista de dicts con dominio/paso str, dedup y tope (B9)."""
    pasos = plan.get("pasos", []) if isinstance(plan, dict) else []
    out = []
    vistos = set()
    for p in pasos:
        if not isinstance(p, dict):
            continue
        dom, paso = p.get("dominio"), p.get("paso")
        if not isinstance(dom, str) or not isinstance(paso, str):
            continue
        if (dom, paso) in vistos:
            continue
        vistos.add((dom, paso))
        out.append({"dominio": dom, "paso": paso})
        if len(out) >= max_pasos:
            break
    return out


def _ejecutar_paso_con_reintento(dominio, paso, hcma, estado, max_intentos=3):
    """Ejecuta un paso con reintento tras 'user' y aislamiento de fallos (B1/B2/B11).
    Solo pide al usuario inputs DECLARADOS; los internos (formula_id, salidas) → paso fallido."""
    declarados = {i["name"] for i in dominio["inputs"]}
    for intento in range(max_intentos):
        try:
            accion, res = ejecutar_paso(dominio, paso, hcma, estado)
        except Exception as e:
            print(f"   ⚠️ paso falló (excepción): {e}")
            return
        if accion != "user":
            _manejar_accion(accion, res, dominio, hcma, estado)
            return
        faltan = [f for f in (res.get("faltan") or []) if f.get("name") in declarados]
        if not faltan:
            print(f"   ⚠️ el modelo pidió inputs no declarados/internos → paso fallido: "
                  f"{[f.get('name') for f in (res.get('faltan') or [])]}")
            return
        resp = preguntar_usuario(faltan, dominio["demo_inputs"], dominio.get("demo_lookup"))
        _escribir_inputs(hcma, resp, dominio)
        print(f"   ↳ reintento {intento + 1} tras pedir inputs")
    print(f"   ⚠️ paso sin resolver tras {max_intentos} intentos")


def _recalcular_dominio(hcma, dominio):
    """Re-ejecuta el pipeline del dominio y escribe sus salidas (re-propagación downstream)."""
    qs, verified, inputs_ok, derived_from, _, _ = _ejecutar_calculo(hcma, dominio)
    for q in qs:
        hcma.write(q["name"], q["value"], unit=q.get("unit", ""), fuente="calculadora",
                   verified=verified, meta={"inputs_verified": inputs_ok},
                   derived_from=derived_from)
    return verified


def orquestar(tarea, dominios=None):
    """Orquestador REAL: plan → ejecuta cada paso (dominio) → router por status → reportes.

    Una sola HCMA compartida entre pasos: las salidas de un dominio (p. ej. `corriente_A` del
    motor) alimentan al siguiente (bobinado) vía `_ejecutar_calculo` (fallback in_<name> → <name>)."""
    dominios = dominios or DOMINIOS
    for did, d in dominios.items():
        _run_tests(d, did)
    memoria_db = os.environ.get("ARQ_MEMORIA_DB")
    if memoria_db:
        from memoria_sqlite import HCMAMemoriaSQLite
        hcma = HCMAMemoriaSQLite(memoria_db)
        print(f"🗄️  Memoria persistida en SQLite: {memoria_db}")
    else:
        hcma = HCMAMemoria()
    estado = Estado()
    print(f"🏗️  Tarea: {tarea}\n")

    ids = list(dominios)
    plan = parse_json(call_llm(
        f"Tarea: {tarea}\n\nDominios disponibles (ids exactos): {json.dumps(ids, ensure_ascii=False)}",
        MODEL, system=PLAN_SYS))
    pasos = _normalizar_plan(plan, ids) or [{"dominio": ids[0], "paso": tarea}]
    print(f"📋 Plan ({len(pasos)} pasos):")
    for p in pasos:
        print(f"   - [{p.get('dominio')}] {p.get('paso')}")
    print()

    orden = []
    for p in pasos:
        dom_id = p.get("dominio")
        resuelto = _resolver_dominio(dom_id, dominios)
        if resuelto is None:
            print(f"⚠️ dominio no reconocido/ambiguo '{dom_id}' → salteado\n")
            continue
        dom_key, dominio = resuelto
        paso = p.get("paso") or tarea
        print(f"▶ [{dominio['nombre']}] {paso}")
        try:
            _preflight_dominio(hcma, dominio)
        except Exception as e:
            print(f"   ⚠️ preflight falló: {e}")
            continue
        estado.frontier_calls = 0  # breaker de frontera por paso (B8)
        _ejecutar_paso_con_reintento(dominio, paso, hcma, estado)
        orden.append(dom_key)
        print()

    # corrección (demo version collapse) al final, solo dominios ejecutados, en orden de plan
    for dom_key in orden:
        _aplicar_correccion(hcma, dominios[dom_key])
    # re-propagación downstream tras las correcciones
    for dom_key in orden:
        _recalcular_dominio(hcma, dominios[dom_key])

    for dom_id, dominio in dominios.items():
        if dominio.get("report") and hcma.current(dominio["clave"]) is not None:
            dominio["report"](hcma)

    print("=" * 60)
    print("📄 Documento final (estado vigente en HCMA):")
    for k, v in sorted(hcma.state().items()):
        val = v["value"]
        if isinstance(val, (dict, list)):
            val = json.dumps(val, ensure_ascii=False)
        print(f"   {k}: {val} {v['unit']}  [{v['fuente']}]")
    pend = hcma.pending_unverified()
    if pend:
        print(f"\n⚠️  Hechos `unverified` (borradores NO canónicos): {list(pend)}")
    report_cost()
    return hcma


def correr_tarea(tarea, dominio=None):
    """Wrapper: orquesta todos los dominios, o uno solo si se pasa una spec."""
    if dominio is None:
        return orquestar(tarea, DOMINIOS)
    dom_id = next((k for k, v in DOMINIOS.items() if v is dominio), next(iter(DOMINIOS)))
    return orquestar(tarea, {dom_id: dominio})


# ═══════════════════════════════════════════════════════════════════════════════
# DOMINIOS (specs declarativas — acá se agrega un dominio nuevo sin tocar el núcleo)
# ═══════════════════════════════════════════════════════════════════════════════

# ── Dominio 1: barra de dominadas ──────────────────────────────────────────────
def calc_barra_dominadas(inputs):
    m = float(inputs["masa_kg"])
    L = float(inputs["luz_m"])
    k = float(inputs.get("factor_dinamico", 2.0))
    fy = float(inputs.get("fy_mpa", 235.0))
    P = m * G * k
    M_nm = P * L / 4.0
    V_n = P / 2.0
    fb = 0.66 * fy
    W_mm3 = M_nm * 1000.0 / fb
    return [
        {"name": "carga_diseno_kN", "value": round(P / 1000.0, 3), "unit": "kN"},
        {"name": "momento_maximo_kNm", "value": round(M_nm / 1000.0, 3), "unit": "kN·m"},
        {"name": "cortante_kN", "value": round(V_n / 1000.0, 3), "unit": "kN"},
        {"name": "seccion_requerida_cm3", "value": round(W_mm3 / 1000.0, 2), "unit": "cm3"},
    ]


SECTIONS = [
    {"id": "tubo_42.4x3.2", "W_cm3": 3.6,  "peso_kg_m": 3.14,  "cat": "tubo"},
    {"id": "tubo_48.3x3.2", "W_cm3": 4.8,  "peso_kg_m": 3.56,  "cat": "tubo"},
    {"id": "tubo_60.3x3.6", "W_cm3": 8.6,  "peso_kg_m": 5.03,  "cat": "tubo"},
    {"id": "tubo_76.1x3.6", "W_cm3": 14.2, "peso_kg_m": 6.44,  "cat": "tubo"},
    {"id": "barra_maciza_50", "W_cm3": 12.3, "peso_kg_m": 15.41, "cat": "maciza"},
    {"id": "ipe_80", "W_cm3": 20.0, "peso_kg_m": 6.0,  "cat": "doble_t"},
    {"id": "ipe_100", "W_cm3": 34.2, "peso_kg_m": 8.1,  "cat": "doble_t"},
]

MATERIALES = {
    "acero": {"resistencia_MPa": 235.0, "densidad_kg_m3": 7850.0, "costo_usd_kg": 1.5},
    "hormigon": {"resistencia_MPa": 25.0, "densidad_kg_m3": 2400.0, "costo_m3": 100.0},
}


def calcular_barra(inputs):
    return calc_barra_dominadas(inputs)


def report_barra(hcma):
    fW = hcma.current("seccion_requerida_cm3")
    fL = hcma.current("in_luz_m")
    if fW is None or fL is None:
        print("⚠️ faltan datos canónicos para el reporte.")
        return
    W_req = fW["value"]
    L = fL["value"]
    derived_from = {"seccion_requerida_cm3": fW["version"], "in_luz_m": fL["version"]}
    print("=" * 60)
    print(f"📊 Comparativa de sección (W_req = {W_req} cm³, L = {L} m):")
    print(f"   {'Opción':<24}{'W_el cm³':>9}{'Cumple':>8}{'Peso kg':>9}")
    opciones = []
    for s in SECTIONS:
        cumple = s["W_cm3"] >= W_req
        peso = round(s["peso_kg_m"] * L, 1)
        opciones.append({"id": s["id"], "W_cm3": s["W_cm3"], "cumple": cumple, "peso_kg": peso})
        print(f"   {s['id']:<24}{s['W_cm3']:>9}{'sí' if cumple else 'no':>8}{peso:>9}")
    vol_ha = 0.2 * 0.3 * L
    masa_ha = round(vol_ha * MATERIALES["hormigon"]["densidad_kg_m3"] + 8, 0)
    hcma.write("opciones_acero", opciones, fuente="calculadora", verified=True, derived_from=derived_from)
    hcma.write("peso_viga_t_hormigon_kg", masa_ha, unit="kg", fuente="calculadora",
               verified=True, derived_from=derived_from)
    print(f"   {'viga T hormigón 200x300':<24}{'—':>9}{'sí':>8}{masa_ha:>9.0f}  (no agarrable)")
    print("\n⚠️  Este cálculo cubre flexión/corte/sección. El ANCLAJE y las fijaciones NO se "
          "verifican aquí y suelen ser lo que falla en una barra de dominadas. Revisar por separado.\n")


DOMINIO_BARRA = {
    "nombre": "barra de dominadas",
    "inputs": [
        {"name": "masa_kg", "question": "masa de la persona (kg)", "unit": "kg"},
        {"name": "luz_m", "question": "luz de la viga (m)", "unit": "m"},
        {"name": "factor_dinamico", "question": "factor de carga dinámica", "unit": "1", "tipo": "num"},
        {"name": "fy_mpa", "question": "límite elástico del acero (MPa)", "unit": "MPa"},
        {"name": "grado_acero", "question": "grado del acero", "unit": "1", "tipo": "str"},
        {"name": "tipo_apoyo", "question": "tipo de apoyo", "unit": "1", "tipo": "str"},
    ],
    "demo_inputs": {
        "masa_kg": 180.0, "luz_m": 2.0, "factor_dinamico": 2.0, "fy_mpa": 235.0,
        "grado_acero": "S235JR", "tipo_apoyo": "simples",
    },
    "demo_lookup": {"precio_acero_usd_kg": 1.5, "proveedor": "Acindar (demo)"},
    "formulas": {"barra_dominadas": {"fn": calc_barra_dominadas,
                                     "inputs": ["masa_kg", "luz_m", "factor_dinamico", "fy_mpa"]}},
    "calcular": calcular_barra,
    "calc_inputs": ["masa_kg", "luz_m", "factor_dinamico", "fy_mpa"],
    "ranges": {
        "carga_diseno_kN": (0.5, 10.0),
        "momento_maximo_kNm": (0.1, 10.0),
        "cortante_kN": (0.05, 20.0),
        "seccion_requerida_cm3": (1.0, 100.0),
    },
    "dimensiones": {
        "carga_diseno_kN": "kN",
        "momento_maximo_kNm": "kN*m",
        "cortante_kN": "kN",
        "seccion_requerida_cm3": "cm**3",
    },
    "allowlist": {"momento_maximo_kNm": {"N*m", "kN*m", "N*mm", "kN*cm"},
                  "seccion_requerida_cm3": {"cm**3", "mm**3", "m**3"}},
    "clave": "momento_maximo_kNm",
    "correccion": {"input": "in_factor_dinamico", "valor": 3.0},
    "ejemplos": (
        'Ejemplo: "calcular el momento de una viga simplemente apoyada" → '
        '{"status":"needs_compute","calc_request":{"formula_id":"barra_dominadas",'
        '"inputs":[{"name":"masa_kg","value":180,"unit":"kg"},'
        '{"name":"luz_m","value":2,"unit":"m"}]}}'
    ),
    "report": report_barra,
    "materiales_prompt": ("Con la lista de materiales (viga de acero para barra de dominadas), "
                          "determina qué comprar (especificación) y dónde (proveedor en Argentina) "
                          "+ precio."),
    "tests": [
        ({"masa_kg": 180, "luz_m": 2, "factor_dinamico": 2.0, "fy_mpa": 235},
         {"momento_maximo_kNm": 1.765, "seccion_requerida_cm3": 11.38}),
        ({"masa_kg": 180, "luz_m": 2, "factor_dinamico": 3.0, "fy_mpa": 235},
         {"momento_maximo_kNm": 2.648, "seccion_requerida_cm3": 17.07}),
        ({"masa_kg": 250, "luz_m": 3, "factor_dinamico": 2.0, "fy_mpa": 235},
         {"momento_maximo_kNm": 3.677, "seccion_requerida_cm3": 23.71}),
    ],
}


# ── Dominio 2: motor eléctrico (agregado SIN tocar el núcleo) ──────────────────
def calc_potencia_motor(inputs):
    par_nm = float(inputs["par_nm"])
    rpm = float(inputs["rpm"])
    omega = 2.0 * math.pi * rpm / 60.0
    P_w = par_nm * omega
    return [
        {"name": "potencia_W", "value": round(P_w, 1), "unit": "W"},
        {"name": "potencia_kW", "value": round(P_w / 1000.0, 3), "unit": "kW"},
    ]


def calc_corriente_motor(inputs):
    P_w = float(inputs["potencia_W"])
    eta = float(inputs.get("eficiencia", 0.9))
    V = float(inputs["tension_V"])
    I = P_w / (eta * V)
    return [{"name": "corriente_A", "value": round(I, 2), "unit": "A"}]


def calcular_motor(inputs):
    qs = calc_potencia_motor({"par_nm": inputs["par_nm"], "rpm": inputs["rpm"]})
    P_w = next(q["value"] for q in qs if q["name"] == "potencia_W")
    qs += calc_corriente_motor({"potencia_W": P_w,
                                "tension_V": inputs["tension_V"],
                                "eficiencia": inputs["eficiencia"]})
    return qs


MOTOR_ESTANDAR = [0.75, 1.1, 1.5, 2.2, 3.0, 4.0, 5.5, 7.5]


def report_motor(hcma):
    fP = hcma.current("potencia_kW")
    fI = hcma.current("corriente_A")
    if fP is None or fI is None:
        print("⚠️ faltan datos canónicos para el reporte del motor.")
        return
    P_kW = fP["value"]
    I = fI["value"]
    sug = next((m for m in MOTOR_ESTANDAR if m >= P_kW * 1.15), MOTOR_ESTANDAR[-1])
    print("=" * 60)
    print(f"📊 Motor eléctrico: P = {P_kW} kW, I = {I} A")
    print(f"   Tamaño comercial sugerido (con margen de servicio ~15%): {sug} kW")
    print("\n⚠️  Esto dimensiona potencia/corriente desde par y velocidad. No cubre: térmica, "
          "aislamiento, ventilación, par de arranque ni selección del bobinado. Revisar por separado.\n")


DOMINIO_MOTOR = {
    "nombre": "motor eléctrico (dimensionado básico)",
    "inputs": [
        {"name": "par_nm", "question": "par requerido (N·m)", "unit": "N·m"},
        {"name": "rpm", "question": "velocidad nominal (rpm)", "unit": "rpm"},
        {"name": "tension_V", "question": "tensión de alimentación (V)", "unit": "V"},
        {"name": "eficiencia", "question": "eficiencia (0..1)", "unit": "1", "tipo": "num"},
    ],
    "demo_inputs": {"par_nm": 10.0, "rpm": 1500.0, "tension_V": 220.0, "eficiencia": 0.9},
    "demo_lookup": {"precio_cobre_usd_kg": 9.0, "proveedor": "casa eléctrica (demo)"},
    "formulas": {
        "potencia_motor": {"fn": calc_potencia_motor, "inputs": ["par_nm", "rpm"]},
        "corriente_motor": {"fn": calc_corriente_motor,
                            "inputs": ["potencia_W", "tension_V", "eficiencia"]},
    },
    "calcular": calcular_motor,
    "calc_inputs": ["par_nm", "rpm", "tension_V", "eficiencia"],
    "ranges": {
        "potencia_W": (1.0, 100000.0),
        "potencia_kW": (0.001, 100.0),
        "corriente_A": (0.1, 1000.0),
    },
    "dimensiones": {
        "potencia_W": "W",
        "potencia_kW": "kW",
        "corriente_A": "A",
    },
    "allowlist": {},
    "clave": "potencia_kW",
    "correccion": {"input": "in_rpm", "valor": 1750.0},
    "ejemplos": (
        'Ejemplo: "potencia a partir de par y velocidad" → '
        '{"status":"needs_compute","calc_request":{"formula_id":"potencia_motor",'
        '"inputs":[{"name":"par_nm","value":10,"unit":"N·m"},{"name":"rpm","value":1500,"unit":"rpm"}]}}\n'
        'Ejemplo: "corriente del motor" → '
        '{"status":"needs_compute","calc_request":{"formula_id":"corriente_motor",'
        '"inputs":[{"name":"potencia_W","value":1570.8,"unit":"W"},'
        '{"name":"tension_V","value":220,"unit":"V"}]}}'
    ),
    "report": report_motor,
    "procurement": False,
    "tests": [
        ({"par_nm": 10, "rpm": 1500, "tension_V": 220, "eficiencia": 0.9},
         {"potencia_kW": 1.571, "corriente_A": 7.93}),
        ({"par_nm": 20, "rpm": 1750, "tension_V": 380, "eficiencia": 0.9},
         {"potencia_kW": 3.665, "corriente_A": 10.72}),
    ],
}


# ── Dominio 3: bobinado de motor (alambre de cobre + buje) ─────────────────────
RHO_CU = 0.0171   # Ω·mm²/m (cobre, ~20 °C)
DENS_CU = 0.00896  # kg por (mm² × m)
AWG = [
    (10, 5.26), (11, 4.17), (12, 3.31), (13, 2.62), (14, 2.08),
    (15, 1.65), (16, 1.31), (17, 1.04), (18, 0.823), (19, 0.653),
    (20, 0.518), (21, 0.410), (22, 0.326), (23, 0.258), (24, 0.205),
]
PV_LIMITE_BUJE = 1.8  # MPa·m/s (bronce autolubricado, límite conservador)


def calc_seccion_cable(inputs):
    I = float(inputs["corriente_A"])
    J = float(inputs["densidad_corriente_A_mm2"])
    A = I / J
    d = math.sqrt(4.0 * A / math.pi)
    return [
        {"name": "seccion_cable_mm2", "value": round(A, 3), "unit": "mm2"},
        {"name": "diametro_cable_mm", "value": round(d, 3), "unit": "mm"},
    ]


def calc_resistencia_bobina(inputs):
    L = float(inputs["largo_alambre_m"])
    A = float(inputs["seccion_cable_mm2"])
    R = RHO_CU * L / A
    return [{"name": "resistencia_ohm", "value": round(R, 3), "unit": "ohm"}]


def calc_peso_cobre(inputs):
    A = float(inputs["seccion_cable_mm2"])
    L = float(inputs["largo_alambre_m"])
    m = A * L * DENS_CU
    return [{"name": "peso_cobre_kg", "value": round(m, 3), "unit": "kg"}]


def calc_buje_pv(inputs):
    d_mm = float(inputs["diametro_eje_mm"])
    n = float(inputs["velocidad_rpm"])
    W = float(inputs["carga_radial_N"])
    Lb = float(inputs["longitud_buje_mm"])
    P = W / (d_mm * Lb)                     # N/mm² = MPa
    V = math.pi * (d_mm / 1000.0) * n / 60.0  # m/s
    return [
        {"name": "presion_buje_MPa", "value": round(P, 3), "unit": "MPa"},
        {"name": "velocidad_superficial_m_s", "value": round(V, 3), "unit": "m/s"},
        {"name": "pv_MPa_m_s", "value": round(P * V, 3), "unit": "MPa·m/s"},
    ]


def calcular_bobinado(inputs):
    qs = calc_seccion_cable({"corriente_A": inputs["corriente_A"],
                             "densidad_corriente_A_mm2": inputs["densidad_corriente_A_mm2"]})
    A = next(q["value"] for q in qs if q["name"] == "seccion_cable_mm2")
    qs += calc_resistencia_bobina({"largo_alambre_m": inputs["largo_alambre_m"],
                                   "seccion_cable_mm2": A})
    qs += calc_peso_cobre({"seccion_cable_mm2": A, "largo_alambre_m": inputs["largo_alambre_m"]})
    qs += calc_buje_pv({
        "diametro_eje_mm": inputs["diametro_eje_mm"],
        "velocidad_rpm": inputs["velocidad_rpm"],
        "carga_radial_N": inputs["carga_radial_N"],
        "longitud_buje_mm": inputs["longitud_buje_mm"],
    })
    return qs


def report_bobinado(hcma):
    cur = {k: hcma.current(k) for k in ("seccion_cable_mm2", "diametro_cable_mm",
                                        "resistencia_ohm", "peso_cobre_kg", "pv_MPa_m_s")}
    if any(v is None for v in cur.values()):
        print("⚠️ faltan datos canónicos para el reporte del bobinado.")
        return
    A = cur["seccion_cable_mm2"]["value"]
    d = cur["diametro_cable_mm"]["value"]
    R = cur["resistencia_ohm"]["value"]
    m = cur["peso_cobre_kg"]["value"]
    PV = cur["pv_MPa_m_s"]["value"]
    awg = None
    for a, area in AWG:
        if area >= A:
            awg = a
    fuera_tabla = awg is None and A > AWG[0][1]
    print("=" * 60)
    print("📊 Bobinado del motor:")
    if fuera_tabla:
        print(f"   Sección del alambre: {A} mm² excede la tabla AWG (máx AWG {AWG[0][0]} = "
              f"{AWG[0][1]} mm²) → usar multifilar / conductores en paralelo. Diámetro ≈ {d} mm")
    else:
        if awg is None:
            awg = AWG[0][0]
        print(f"   Sección del alambre: {A} mm² (≈ AWG {awg}), diámetro ≈ {d} mm")
    print(f"   Resistencia de la bobina: {R} Ω")
    print(f"   Peso del cobre: {m} kg")
    print(f"   Buje: PV = {PV} MPa·m/s (límite bronce autolubricado ≈ {PV_LIMITE_BUJE})")
    if PV > PV_LIMITE_BUJE:
        print("   ⚠️  PV excede el límite: aumentar diámetro/longitud del buje o reducir carga/velocidad.")
    print("\n⚠️  No cubre: aislación, factor de llenado de ranura, pérdidas en el hierro ni térmica. "
          "Revisar por separado.\n")


DOMINIO_BOBINA = {
    "nombre": "bobinado de motor (alambre de cobre + buje)",
    "inputs": [
        {"name": "corriente_A", "question": "corriente de fase (A)", "unit": "A"},
        {"name": "densidad_corriente_A_mm2", "question": "densidad de corriente (A/mm², típico 3-6)", "unit": "A/mm2"},
        {"name": "largo_alambre_m", "question": "longitud total del alambre (m)", "unit": "m"},
        {"name": "diametro_eje_mm", "question": "diámetro del eje (mm)", "unit": "mm"},
        {"name": "velocidad_rpm", "question": "velocidad (rpm)", "unit": "rpm"},
        {"name": "carga_radial_N", "question": "carga radial sobre el buje (N)", "unit": "N"},
        {"name": "longitud_buje_mm", "question": "longitud del buje (mm)", "unit": "mm"},
    ],
    "demo_inputs": {
        "corriente_A": 7.93, "densidad_corriente_A_mm2": 4.0, "largo_alambre_m": 50.0,
        "diametro_eje_mm": 20.0, "velocidad_rpm": 1500.0,
        "carga_radial_N": 500.0, "longitud_buje_mm": 25.0,
    },
    "demo_lookup": {"precio_cobre_usd_kg": 9.0, "proveedor": "casa eléctrica (demo)"},
    "formulas": {
        "seccion_cable": {"fn": calc_seccion_cable,
                          "inputs": ["corriente_A", "densidad_corriente_A_mm2"]},
        "resistencia_bobina": {"fn": calc_resistencia_bobina,
                               "inputs": ["largo_alambre_m", "seccion_cable_mm2"]},
        "peso_cobre": {"fn": calc_peso_cobre,
                       "inputs": ["seccion_cable_mm2", "largo_alambre_m"]},
        "buje_pv": {"fn": calc_buje_pv,
                    "inputs": ["diametro_eje_mm", "velocidad_rpm",
                               "carga_radial_N", "longitud_buje_mm"]},
    },
    "calcular": calcular_bobinado,
    "calc_inputs": ["corriente_A", "densidad_corriente_A_mm2", "largo_alambre_m",
                    "diametro_eje_mm", "velocidad_rpm", "carga_radial_N", "longitud_buje_mm"],
    "ranges": {
        "seccion_cable_mm2": (0.05, 50.0),
        "diametro_cable_mm": (0.1, 10.0),
        "resistencia_ohm": (0.01, 100.0),
        "peso_cobre_kg": (0.001, 100.0),
        "presion_buje_MPa": (0.01, 50.0),
        "velocidad_superficial_m_s": (0.01, 50.0),
        "pv_MPa_m_s": (0.01, 50.0),
    },
    "dimensiones": {
        "seccion_cable_mm2": "mm**2",
        "diametro_cable_mm": "mm",
        "resistencia_ohm": "ohm",
        "peso_cobre_kg": "kg",
        "presion_buje_MPa": "MPa",
        "velocidad_superficial_m_s": "m/s",
        "pv_MPa_m_s": "MPa*m/s",
    },
    "allowlist": {"seccion_cable_mm2": {"mm**2", "cm**2", "m**2"}},
    "clave": "seccion_cable_mm2",
    "correccion": {"input": "in_densidad_corriente_A_mm2", "valor": 5.0},
    "ejemplos": (
        'Ejemplo: "sección del alambre a partir de corriente y densidad" → '
        '{"status":"needs_compute","calc_request":{"formula_id":"seccion_cable",'
        '"inputs":[{"name":"corriente_A","value":7.93,"unit":"A"},'
        '{"name":"densidad_corriente_A_mm2","value":4,"unit":"A/mm2"}]}}\n'
        'Ejemplo: "verificar el buje" → '
        '{"status":"needs_compute","calc_request":{"formula_id":"buje_pv","inputs":[...]}}'
    ),
    "report": report_bobinado,
    "procurement": False,
    "tests": [
        ({"corriente_A": 7.93, "densidad_corriente_A_mm2": 4.0, "largo_alambre_m": 50.0,
          "diametro_eje_mm": 20.0, "velocidad_rpm": 1500.0,
          "carga_radial_N": 500.0, "longitud_buje_mm": 25.0},
         {"seccion_cable_mm2": 1.982, "resistencia_ohm": 0.431, "peso_cobre_kg": 0.888,
          "pv_MPa_m_s": 1.571}),
        ({"corriente_A": 15.0, "densidad_corriente_A_mm2": 5.0, "largo_alambre_m": 30.0,
          "diametro_eje_mm": 25.0, "velocidad_rpm": 3000.0,
          "carga_radial_N": 800.0, "longitud_buje_mm": 30.0},
         {"seccion_cable_mm2": 3.0, "resistencia_ohm": 0.171, "peso_cobre_kg": 0.806,
          "pv_MPa_m_s": 4.189}),
    ],
}


# ── Dominio 4: habitación (planta rectangular + cómputo de mampostería) ───────
# Constantes de anteproyecto (mampostería de ladrillo común 15 cm, Argentina).
# Son ESTIMACIONES de cómputo, no un cálculo estructural.
LADRILLOS_M2 = 60.0          # unid/m² de muro (ladrillo común 15 cm, con mezcla)
CEMENTO_M2_BOLSAS = 0.35     # bolsas de 50 kg por m² de muro (mortero 1:3, aprox.)
ARENA_M2_M3 = 0.02           # m³ de arena por m² de muro
ENCADENADO_REFUERZOS = 4     # barras longitudinales por encadenado (2 inferiores + 2 superiores)
HIERRO_PESOS = {8: 0.395, 10: 0.617, 12: 0.888}  # kg/m (Ø8/Ø10/Ø12, IRAM-IAS U 500-528)
# Diámetro de la armadura del encadenado según zona sísmica (INPRES-CIRSOC 103).
# Se PREGUNTA al usuario (input `zona_sismica`), NO se presupone.
ZONA_SISMICA_DIAMETRO = {"baja": 8, "media": 8, "moderada": 8, "alta": 10}
DIAMETRO_FALLBACK = 10  # solo si la zona declarada no se reconoce (lado conservador)


def calc_habitacion(inputs):
    a = float(inputs["ancho_m"])
    l = float(inputs["largo_m"])
    h = float(inputs["alto_m"])
    e_cm = float(inputs["espesor_muro_cm"])
    ap = float(inputs["ancho_puerta_m"])
    hp = float(inputs["alto_puerta_m"])
    zona = str(inputs.get("zona_sismica", "")).strip().lower()
    diametro = ZONA_SISMICA_DIAMETRO.get(zona, DIAMETRO_FALLBACK)
    superficie = a * l
    perimetro = 2.0 * (a + l)
    area_puerta = ap * hp
    area_muros = perimetro * h - area_puerta
    volumen = area_muros * (e_cm / 100.0)
    ladrillos = float(math.ceil(area_muros * LADRILLOS_M2))
    cemento = round(area_muros * CEMENTO_M2_BOLSAS, 2)
    arena = round(area_muros * ARENA_M2_M3, 3)
    ml_enc = 2.0 * perimetro
    hierro = round(ml_enc * ENCADENADO_REFUERZOS * HIERRO_PESOS[diametro], 1)
    revoque = 2.0 * area_muros
    return [
        {"name": "superficie_m2", "value": round(superficie, 2), "unit": "m2"},
        {"name": "perimetro_m", "value": round(perimetro, 2), "unit": "m"},
        {"name": "area_puerta_m2", "value": round(area_puerta, 2), "unit": "m2"},
        {"name": "area_muros_m2", "value": round(area_muros, 2), "unit": "m2"},
        {"name": "volumen_muros_m3", "value": round(volumen, 3), "unit": "m3"},
        {"name": "cantidad_ladrillos", "value": ladrillos, "unit": "unidad"},
        {"name": "cemento_muro_bolsas", "value": cemento, "unit": "bolsa"},
        {"name": "arena_muro_m3", "value": arena, "unit": "m3"},
        {"name": "ml_encadenado_m", "value": round(ml_enc, 2), "unit": "m"},
        {"name": "diametro_hierro_mm", "value": float(diametro), "unit": "mm"},
        {"name": "hierro_encadenado_kg", "value": hierro, "unit": "kg"},
        {"name": "revoque_m2", "value": round(revoque, 2), "unit": "m2"},
        {"name": "contrapiso_m2", "value": round(superficie, 2), "unit": "m2"},
        {"name": "piso_m2", "value": round(superficie, 2), "unit": "m2"},
    ]


# Precios de referencia (drift_temporal: cambian con mercado/fecha/ubicación).
# El reporte los escribe como UNVERIFIED en HCMA para que quede explícito que
# NO son canónicos y requieren validación del proveedor.
PRECIOS_HABITACION = [
    ("ladrillo_comun", "unidad", 0.12, "corralón de materiales (demo)"),
    ("cemento_50kg", "bolsa", 10.0, "corralón de materiales (demo)"),
    ("arena", "m3", 25.0, "corralón de materiales (demo)"),
    ("hierro", "kg", 1.5, "acero (demo)"),
    ("puerta_madera_0.8x2.0", "unidad", 120.0, "maderera (demo)"),
    ("ceramica_piso", "m2", 12.0, "corralón (demo)"),
    ("revoque_materiales", "m2", 4.0, "corralón (demo)"),
]


def esquema_habitacion(inputs, Q):
    a, l, h = inputs["ancho_m"], inputs["largo_m"], inputs["alto_m"]
    return (
        f"Planta {a:.2f} m x {l:.2f} m, altura libre {h:.2f} m\n"
        f"      +{'-' * 24}+\n"
        "      |                        |\n"
        f"      |   superficie {Q['superficie_m2']:.2f} m2   |\n"
        "      |                        |\n"
        f"      +--[puerta {Q['area_puerta_m2']:.2f} m2]--+"
    )


def materiales_habitacion(inputs, Q):
    return [
        ("ladrillo_comun", Q["cantidad_ladrillos"], "unidad"),
        ("cemento_50kg", Q["cemento_muro_bolsas"], "bolsa"),
        ("arena", Q["arena_muro_m3"], "m3"),
        ("hierro", Q["hierro_encadenado_kg"], "kg"),
        ("puerta_madera_0.8x2.0", 1.0, "unidad"),
        ("ceramica_piso", Q["piso_m2"], "m2"),
        ("revoque_materiales", Q["revoque_m2"], "m2"),
    ]


def notas_habitacion(inputs, Q):
    z = inputs["zona_sismica"]
    d = int(Q["diametro_hierro_mm"])
    return [f"Zona sísmica declarada: {z} -> armadura del encadenado: Ø{d}"]


DOMINIO_HABITACION = {
    "nombre": "habitación (planta + mampostería + presupuesto)",
    "inputs": [
        {"name": "ancho_m", "question": "ancho interior de la habitación (m)", "unit": "m"},
        {"name": "largo_m", "question": "largo interior de la habitación (m)", "unit": "m"},
        {"name": "alto_m", "question": "altura libre (m)", "unit": "m"},
        {"name": "espesor_muro_cm", "question": "espesor del muro (cm)", "unit": "cm"},
        {"name": "ancho_puerta_m", "question": "ancho de la puerta (m)", "unit": "m"},
        {"name": "alto_puerta_m", "question": "alto de la puerta (m)", "unit": "m"},
        {"name": "zona_sismica", "question": "zona sísmica (baja/moderada/alta)", "unit": "1", "tipo": "str"},
        {"name": "tipo_muro", "question": "tipo de muro", "unit": "1", "tipo": "str"},
        {"name": "tipo_techo", "question": "tipo de techo", "unit": "1", "tipo": "str"},
        {"name": "tipo_piso", "question": "tipo de piso", "unit": "1", "tipo": "str"},
    ],
    "demo_inputs": {
        "ancho_m": 4.0, "largo_m": 4.0, "alto_m": 2.6, "espesor_muro_cm": 15.0,
        "ancho_puerta_m": 0.80, "alto_puerta_m": 2.0, "zona_sismica": "baja",
        "tipo_muro": "ladrillo_comun", "tipo_techo": "losa", "tipo_piso": "ceramica",
    },
    "demo_lookup": {"precio_ladrillo_usd": 0.12, "proveedor": "corralón (demo)"},
    "formulas": {"habitacion": {"fn": calc_habitacion,
                                 "inputs": ["ancho_m", "largo_m", "alto_m", "espesor_muro_cm",
                                            "ancho_puerta_m", "alto_puerta_m", "zona_sismica"]}},
    "calcular": calc_habitacion,
    "calc_inputs": ["ancho_m", "largo_m", "alto_m", "espesor_muro_cm",
                    "ancho_puerta_m", "alto_puerta_m", "zona_sismica"],
    # Documentación AUDITABLE de lo que se presupone como constante (no se pregunta).
    # Regla (INVARIANTES_DOMINIO.md): solo van acá valores UNIVERSALES (independientes
    # del caso); todo lo que dependa del caso (zona, dimensiones, ubicación, precios)
    # DEBE ser `input`. Cada entrada lleva `fuente` y `porque_no_input`.
    "constantes": [
        {"name": "LADRILLOS_M2", "valor": 60.0, "unit": "unidad/m2",
         "fuente": "cómputo estándar muro ladrillo común 15 cm (Argentina)",
         "porque_no_input": "ratio universal del tipo de muro, no del caso"},
        {"name": "CEMENTO_M2_BOLSAS", "valor": 0.35, "unit": "bolsa/m2",
         "fuente": "dosificación mortero 1:3 (aprox.)",
         "porque_no_input": "ratio universal del tipo de muro, no del caso"},
        {"name": "ARENA_M2_M3", "valor": 0.02, "unit": "m3/m2",
         "fuente": "dosificación mortero 1:3 (aprox.)",
         "porque_no_input": "ratio universal del tipo de muro, no del caso"},
        {"name": "ENCADENADO_REFUERZOS", "valor": 4, "unit": "barras",
         "fuente": "práctica estándar encadenado H°A° (2 inf. + 2 sup.)",
         "porque_no_input": "configuración estándar del encadenado"},
        {"name": "HIERRO_PESOS", "valor": {8: 0.395, 10: 0.617, 12: 0.888}, "unit": "kg/m",
         "fuente": "IRAM-IAS U 500-528 (peso nominal de barras)",
         "porque_no_input": "tabla normativa universal (no depende del caso)"},
        {"name": "ZONA_SISMICA_DIAMETRO", "valor": {"baja": 8, "media": 8, "moderada": 8, "alta": 10},
         "unit": "zona->Ømm",
         "fuente": "INPRES-CIRSOC 103 (armadura mínima de encadenado según zona sísmica)",
         "porque_no_input": "tabla normativa universal; lo que se pregunta es la zona (input)"},
    ],
    "ranges": {
        "superficie_m2": (1.0, 1000.0),
        "perimetro_m": (1.0, 200.0),
        "area_puerta_m2": (0.5, 20.0),
        "area_muros_m2": (1.0, 1000.0),
        "volumen_muros_m3": (0.1, 500.0),
        "cantidad_ladrillos": (1.0, 200000.0),
        "cemento_muro_bolsas": (0.0, 10000.0),
        "arena_muro_m3": (0.0, 500.0),
        "ml_encadenado_m": (1.0, 1000.0),
        "diametro_hierro_mm": (6.0, 25.0),
        "hierro_encadenado_kg": (1.0, 100000.0),
        "revoque_m2": (1.0, 2000.0),
        "contrapiso_m2": (1.0, 1000.0),
        "piso_m2": (1.0, 1000.0),
    },
    "dimensiones": {
        "superficie_m2": "m2",
        "perimetro_m": "m",
        "area_puerta_m2": "m2",
        "area_muros_m2": "m2",
        "volumen_muros_m3": "m3",
        "cantidad_ladrillos": "unidad",
        "cemento_muro_bolsas": "bolsa",
        "arena_muro_m3": "m3",
        "ml_encadenado_m": "m",
        "diametro_hierro_mm": "mm",
        "hierro_encadenado_kg": "kg",
        "revoque_m2": "m2",
        "contrapiso_m2": "m2",
        "piso_m2": "m2",
    },
    "allowlist": {},
    "clave": "area_muros_m2",
    "correccion": {"input": "in_alto_m", "valor": 3.0},
    "ejemplos": (
        'Ejemplo: "calcular los materiales de una habitación de 4x4 m con puerta estándar" → '
        '{"status":"needs_compute","calc_request":{"formula_id":"habitacion",'
        '"inputs":[{"name":"ancho_m","value":4,"unit":"m"},'
        '{"name":"largo_m","value":4,"unit":"m"},'
        '{"name":"alto_m","value":2.6,"unit":"m"},'
        '{"name":"espesor_muro_cm","value":15,"unit":"cm"},'
        '{"name":"ancho_puerta_m","value":0.8,"unit":"m"},'
        '{"name":"alto_puerta_m","value":2.0,"unit":"m"},'
        '{"name":"zona_sismica","value":"baja","unit":"1"}]}}'
    ),
    "entregable": {
        "titulo": "Habitación 4x4 — plano, cómputo de mampostería y presupuesto",
        "etiquetas": {
            "superficie_m2": "superficie",
            "area_muros_m2": "muros (sin hueco)",
            "volumen_muros_m3": "volumen muros",
            "cantidad_ladrillos": "ladrillos",
            "cemento_muro_bolsas": "cemento (50 kg)",
            "arena_muro_m3": "arena",
            "ml_encadenado_m": "encadenado (m lineales)",
            "diametro_hierro_mm": "diámetro hierro",
            "hierro_encadenado_kg": "hierro (encadenado)",
            "revoque_m2": "revoque (ambas caras)",
            "contrapiso_m2": "contrapiso",
            "piso_m2": "piso",
        },
        "esquema": esquema_habitacion,
        "materiales": materiales_habitacion,
        "precios": PRECIOS_HABITACION,
        "exclusiones": [
            "Cómputo de anteproyecto (mampostería + piso + puerta), no un proyecto ejecutivo apto para construir sin revisión profesional.",
            "NO incluye losa/techo estructural (armadura, espesor, apuntalamiento), fundaciones, instalaciones (eléctrica/sanitaria), aberturas de ventana, mano de obra ni impuestos.",
            "No incluye los estribos Ø6 del encadenado.",
            "Precios de referencia (drift_temporal): validar contra proveedor.",
        ],
        "notas": notas_habitacion,
    },
    "requerimientos": [
        {"id": "RE-01", "texto": "Dimensiones de la habitación declaradas y superficie calculada",
         "satisface_con": [
            {"tipo": "input", "name": "ancho_m"},
            {"tipo": "input", "name": "largo_m"},
            {"tipo": "input", "name": "alto_m"},
            {"tipo": "salida", "name": "superficie_m2"},
            {"tipo": "salida", "name": "perimetro_m"},
        ]},
        {"id": "RE-02", "texto": "Cómputo de mampostería (muros, ladrillos, mortero)",
         "satisface_con": [
            {"tipo": "salida", "name": "area_muros_m2"},
            {"tipo": "salida", "name": "volumen_muros_m3"},
            {"tipo": "salida", "name": "cantidad_ladrillos"},
            {"tipo": "material", "item": "ladrillo_comun"},
            {"tipo": "material", "item": "cemento_50kg"},
        ]},
        {"id": "RE-03", "texto": "Encadenado según zona sísmica declarada (no presupuesta)",
         "satisface_con": [
            {"tipo": "input", "name": "zona_sismica"},
            {"tipo": "salida", "name": "diametro_hierro_mm"},
            {"tipo": "salida", "name": "hierro_encadenado_kg"},
            {"tipo": "material", "item": "hierro"},
        ]},
        {"id": "RE-04", "texto": "Plano de planta incluido",
         "satisface_con": [
            {"tipo": "esquema", "regex": "Planta"},
        ]},
        {"id": "RE-05", "texto": "Lista de materiales y presupuesto presentes",
         "satisface_con": [
            {"tipo": "seccion", "name": "materiales"},
            {"tipo": "seccion", "name": "presupuesto"},
        ]},
    ],
    "procurement": False,
    "tests": [
        ({"ancho_m": 4, "largo_m": 4, "alto_m": 2.6, "espesor_muro_cm": 15,
          "ancho_puerta_m": 0.8, "alto_puerta_m": 2.0, "zona_sismica": "baja"},
         {"superficie_m2": 16.0, "area_muros_m2": 40.0, "volumen_muros_m3": 6.0,
          "cantidad_ladrillos": 2400.0, "diametro_hierro_mm": 8.0, "hierro_encadenado_kg": 50.6}),
        ({"ancho_m": 3, "largo_m": 5, "alto_m": 2.4, "espesor_muro_cm": 20,
          "ancho_puerta_m": 0.9, "alto_puerta_m": 2.1, "zona_sismica": "alta"},
         {"superficie_m2": 15.0, "area_muros_m2": 36.51, "cantidad_ladrillos": 2191.0,
          "diametro_hierro_mm": 10.0, "hierro_encadenado_kg": 79.0}),
    ],
}


# ── Dominio 5: instalación eléctrica (motor + iluminación + toma + p.a.t.) ─────
# Tablas/constantes UNIVERSALES (AEA 90364, Argentina). No dependen del caso.
CAPACIDAD_CABLE_A = {1.5: 15.0, 2.5: 21.0, 4.0: 28.0, 6.0: 36.0, 10.0: 50.0}  # A, Cu PVC, 2 cargados en caño
SECCIONES_CABLE = [1.5, 2.5, 4.0, 6.0, 10.0]
TERMICAS_A = [6, 10, 16, 20, 25, 32, 40, 50]
RHO_CU_OHM_MM2_M = 0.022     # Ω·mm²/m (cobre ~70 °C, para caída de tensión)
CAIDA_ADMISIBLE_PCT = 3.0    # % caída admisible en circuito terminal
SECCION_MIN_ILUMINACION = 1.5
SECCION_MIN_TOMA = 2.5
SECCION_MIN_MOTOR = 2.5
DIFERENCIAL_MA = 30
JABALINA_MM2 = 16.0          # conductor tablero→jabalina (mínimo AEA 90364)


def _seccion_por_capacidad(I):
    for s in SECCIONES_CABLE:
        if CAPACIDAD_CABLE_A[s] >= I:
            return s
    return SECCIONES_CABLE[-1]


def _seccion_por_caida(I, L, cosphi, V, fase):
    dv = CAIDA_ADMISIBLE_PCT / 100.0 * V
    k = 2.0 if fase in ("monofasica", "monofásica") else math.sqrt(3.0)
    s = k * RHO_CU_OHM_MM2_M * L * I * cosphi / dv
    for sec in SECCIONES_CABLE:
        if sec >= s:
            return sec
    return SECCIONES_CABLE[-1]


def _termica_minima(corriente, margen=1.0):
    for t in TERMICAS_A:
        if t >= corriente * margen:
            return float(t)
    return float(TERMICAS_A[-1])


# Alturas de montaje (AEA 90364 / práctica normalizada): constantes UNIVERSALES.
ALTURA_TABLERO_M = 1.5
ALTURA_TECLA_M = 1.2
ALTURA_TOMA_M = 0.3
ALTURA_MOTOR_M = 0.5


def _manh(p0, p1):
    return abs(p1[0] - p0[0]) + abs(p1[1] - p0[1])


def derivar_longitudes(inputs):
    """Deriva las longitudes de cable de la DISPOSICIÓN física (no un input genérico).

    Supuestos documentados: puerta en pared Sur (y=0), tecla junto a la puerta, tablero junto
    a la tecla, lámpara en el centro del techo, tomacorriente en la pared opuesta (Norte)
    centrado, motor junto al tablero. Recorrido por cielorraso: subida + Manhattan + bajada."""
    L, A, H = float(inputs["largo_m"]), float(inputs["ancho_m"]), float(inputs["alto_m"])
    tablero = (1.0, 0.0)      # pared Sur, junto a la puerta
    tecla = (2.2, 0.0)        # pared Sur, junto a la puerta
    lampara = (L / 2.0, A / 2.0)
    toma = (L / 2.0, A)       # pared Norte (opuesta a la puerta)
    motor = (1.0, 0.0)        # junto al tablero

    def tramo(p0, h0, p1, h1):
        return (H - h0) + _manh(p0, p1) + (H - h1)

    return {
        "longitud_circ_iluminacion_m": round(tramo(tablero, ALTURA_TABLERO_M, tecla, ALTURA_TECLA_M)
                                              + tramo(tecla, ALTURA_TECLA_M, lampara, H), 2),
        "longitud_circ_toma_m": round(tramo(tablero, ALTURA_TABLERO_M, toma, ALTURA_TOMA_M), 2),
        "longitud_circ_motor_m": round(tramo(tablero, ALTURA_TABLERO_M, motor, ALTURA_MOTOR_M), 2),
    }


def calc_red_electrica(inputs):
    V = float(inputs["tension_V"])
    fase = str(inputs.get("fase", "monofasica")).lower()
    cosphi = float(inputs["cos_phi"])
    eta = float(inputs["eficiencia_motor"])
    P_motor = float(inputs["potencia_motor_W"])
    P_luz = float(inputs["potencia_lampara_W"])
    P_toma = float(inputs["potencia_toma_W"])
    longs = derivar_longitudes(inputs)
    L_ilum = longs["longitud_circ_iluminacion_m"]
    L_toma = longs["longitud_circ_toma_m"]
    L_motor = longs["longitud_circ_motor_m"]

    if fase in ("trifasica", "trifásica"):
        I_motor = P_motor / (math.sqrt(3.0) * V * cosphi * eta)
    else:
        I_motor = P_motor / (V * cosphi * eta)
    I_luz = P_luz / V
    I_toma = P_toma / V

    s_ilum = max(SECCION_MIN_ILUMINACION, _seccion_por_capacidad(I_luz),
                 _seccion_por_caida(I_luz, L_ilum, 1.0, V, fase))
    s_toma = max(SECCION_MIN_TOMA, _seccion_por_capacidad(I_toma),
                 _seccion_por_caida(I_toma, L_toma, 1.0, V, fase))
    s_motor = max(SECCION_MIN_MOTOR, _seccion_por_capacidad(I_motor),
                  _seccion_por_caida(I_motor, L_motor, cosphi, V, fase))

    k = 2.0 if fase in ("monofasica", "monofásica") else math.sqrt(3.0)
    dv_motor = k * RHO_CU_OHM_MM2_M * L_motor * I_motor * cosphi / s_motor

    t_ilum = 10.0   # protege el conductor de 1.5 mm² (estándar)
    t_toma = 16.0   # protege el conductor de 2.5 mm² (estándar)
    t_motor = _termica_minima(I_motor, 1.25)  # margen por arranque, curva C

    s_pe = max(2.5, s_motor)          # PE acompaña al circuito más exigente (mín. 2.5 mm²)
    d_canio = 20.0 if max(s_ilum, s_toma, s_motor) <= 2.5 else 25.0

    return [
        {"name": "corriente_motor_A", "value": round(I_motor, 2), "unit": "A"},
        {"name": "corriente_lampara_A", "value": round(I_luz, 2), "unit": "A"},
        {"name": "corriente_toma_A", "value": round(I_toma, 2), "unit": "A"},
        {"name": "seccion_iluminacion_mm2", "value": s_ilum, "unit": "mm2"},
        {"name": "seccion_toma_mm2", "value": s_toma, "unit": "mm2"},
        {"name": "seccion_motor_mm2", "value": s_motor, "unit": "mm2"},
        {"name": "termica_iluminacion_A", "value": t_ilum, "unit": "A"},
        {"name": "termica_toma_A", "value": t_toma, "unit": "A"},
        {"name": "termica_motor_A", "value": t_motor, "unit": "A"},
        {"name": "caida_tension_motor_V", "value": round(dv_motor, 2), "unit": "V"},
        {"name": "seccion_pe_mm2", "value": s_pe, "unit": "mm2"},
        {"name": "seccion_jabalina_mm2", "value": JABALINA_MM2, "unit": "mm2"},
        {"name": "diametro_canio_mm", "value": d_canio, "unit": "mm"},
        {"name": "longitud_circ_iluminacion_m", "value": L_ilum, "unit": "m"},
        {"name": "longitud_circ_toma_m", "value": L_toma, "unit": "m"},
        {"name": "longitud_circ_motor_m", "value": L_motor, "unit": "m"},
    ]


# Precios de referencia en ARS, obtenidos vía búsqueda web (drift_temporal) — NO demo.
# Fuentes: obramaestra.com / propdata.com.ar (2026). Los marcados "estimado" no salieron
# en la búsqueda y se aproximan; todos quedan UNVERIFIED en HCMA.
PRECIOS_RED_ELECTRICA = [
    ("cable_1.5_mm2", "m", 1200.0, "obramaestra.com (2026, ARS, estimado)"),
    ("cable_2.5_mm2", "m", 1800.0, "obramaestra.com (2026, ARS)"),
    ("cable_pe_2.5_mm2", "m", 1800.0, "obramaestra.com (2026, ARS)"),
    ("cable_16_mm2", "m", 8500.0, "obramaestra.com (2026, ARS, estimado)"),
    ("termica_10A", "unidad", 7000.0, "propdata.com.ar (2026, ARS, estimado)"),
    ("termica_16A", "unidad", 7500.0, "propdata.com.ar (2026, ARS)"),
    ("termica_16A_curvaC", "unidad", 9000.0, "propdata.com.ar (2026, ARS, estimado)"),
    ("diferencial_30mA", "unidad", 18000.0, "propdata.com.ar (2026, ARS)"),
    ("canio_corrugado_20mm_tramo3m", "tramo", 8500.0, "propdata.com.ar (2026, ARS)"),
    ("caja_paso", "unidad", 2500.0, "propdata.com.ar (2026, ARS, estimado)"),
    ("tecla_punto", "unidad", 3500.0, "propdata.com.ar (2026, ARS, estimado)"),
    ("tomacorriente_10A", "unidad", 1730.0, "propdata.com.ar (2026, ARS)"),
    ("portalamparas", "unidad", 2500.0, "propdata.com.ar (2026, ARS, estimado)"),
    ("jabalina_bornes", "unidad", 12000.0, "propdata.com.ar (2026, ARS, estimado)"),
    ("lampara_led_100w", "unidad", 8000.0, "propdata.com.ar (2026, ARS, estimado)"),
]


def cantidades_red_electrica(L_ilum, L_toma, L_motor):
    """Cantidades de materiales a partir de las longitudes de circuito DERIVADAS de la
    disposición. Aproximación de anteproyecto: fase+neutro por circuito, PE acompaña,
    3 m de conductor tablero→jabalina, caño corrugado en tramos de 3 m."""
    return {
        "cable_1.5_mm2": round(2.0 * L_ilum, 1),                        # fase + neutro (iluminación)
        "cable_2.5_mm2": round(2.0 * L_toma + 2.0 * L_motor, 1),        # fase+neutro (toma y motor)
        "cable_pe_2.5_mm2": round(L_ilum + L_toma + L_motor, 1),        # PE acompaña a los circuitos
        "cable_16_mm2": 3.0,                                             # tablero → jabalina
        "termica_10A": 1.0,
        "termica_16A": 1.0,
        "termica_16A_curvaC": 1.0,
        "diferencial_30mA": 1.0,
        "canio_corrugado_20mm_tramo3m": round((L_ilum + L_toma + L_motor) / 3.0, 0),
        "caja_paso": 4.0,
        "tecla_punto": 1.0,
        "tomacorriente_10A": 1.0,
        "portalamparas": 1.0,
        "jabalina_bornes": 1.0,
        "lampara_led_100w": 1.0,
    }


def esquema_red_electrica(inputs, Q):
    L, A = inputs["largo_m"], inputs["ancho_m"]
    planta = (
        "   PLANTA (disposicion fisica):\n"
        f"   +{'-' * 28}+\n"
        "   |          [O] toma        |   <- pared opuesta a la puerta\n"
        "   |                          |\n"
        f"   |          (L) lampara     |   {A:.1f} m\n"
        "   |          centro          |\n"
        "   |                          |\n"
        "   | [tablero][T tecla] [=]   |   <- pared de la puerta\n"
        f"   +{'-' * 28}+\n"
        f"            {L:.1f} m\n"
        "   T = tecla junto a la puerta · O = tomacorriente · L = lampara · = = puerta"
    )
    unifilar = (
        "   UNIFILAR:\n"
        "   TABLERO\n"
        f"   +-- Diferencial {DIFERENCIAL_MA} mA (clase AC)\n"
        f"   |   +-- TM {Q['termica_iluminacion_A']:.0f} A -- iluminacion (LED + tecla) --- {Q['seccion_iluminacion_mm2']} mm2\n"
        f"   |   +-- TM {Q['termica_toma_A']:.0f} A -- tomacorriente ---------------------- {Q['seccion_toma_mm2']} mm2\n"
        f"   |   +-- TM {Q['termica_motor_A']:.0f} A C -- motor --------------------------- {Q['seccion_motor_mm2']} mm2\n"
        f"   +-- PE {Q['seccion_pe_mm2']} mm2 (verde-amarillo) -- todos los circuitos\n"
        f"        +-- Jabalina p.a.t. ({Q['seccion_jabalina_mm2']:.0f} mm2, R <= 40 ohm)"
    )
    return planta + "\n\n" + unifilar


def materiales_red_electrica(inputs, Q):
    cant = cantidades_red_electrica(Q["longitud_circ_iluminacion_m"],
                                    Q["longitud_circ_toma_m"],
                                    Q["longitud_circ_motor_m"])
    unidades = {p[0]: p[1] for p in PRECIOS_RED_ELECTRICA}
    return [(item, cant[item], unidades[item]) for item in cant]


def notas_red_electrica(inputs, Q):
    pct = Q["caida_tension_motor_V"] / inputs["tension_V"] * 100.0
    tipo = str(inputs["tipo_instalacion"]).lower()
    cano = ("caño corrugado PVC (embutido)" if tipo in ("embutida", "embutido", "embutidas")
            else "caño rígido PVC (a la vista)")
    return [
        f"Caída de tensión (motor): {Q['caida_tension_motor_V']} V ({pct:.2f} %) <= {CAIDA_ADMISIBLE_PCT} %",
        f"Cañería {tipo}: {cano}, Ø{Q['diametro_canio_mm']:.0f} mm",
    ]


DOMINIO_RED = {
    "nombre": "instalación eléctrica (motor + iluminación + toma + p.a.t.)",
    "inputs": [
        {"name": "potencia_motor_W", "question": "potencia del motor (W)", "unit": "W"},
        {"name": "tension_V", "question": "tensión nominal (V)", "unit": "V"},
        {"name": "fase", "question": "alimentación (monofásica/trifásica)", "unit": "1", "tipo": "str"},
        {"name": "cos_phi", "question": "factor de potencia del motor (0..1)", "unit": "1", "tipo": "num"},
        {"name": "eficiencia_motor", "question": "eficiencia del motor (0..1)", "unit": "1", "tipo": "num"},
        {"name": "potencia_lampara_W", "question": "potencia de la lámpara LED (W)", "unit": "W"},
        {"name": "potencia_toma_W", "question": "potencia prevista del tomacorriente (W)", "unit": "W"},
        {"name": "largo_m", "question": "largo de la habitación (m)", "unit": "m"},
        {"name": "ancho_m", "question": "ancho de la habitación (m)", "unit": "m"},
        {"name": "alto_m", "question": "altura de cielorraso (m)", "unit": "m"},
        {"name": "tipo_instalacion", "question": "instalación embutida o a la vista", "unit": "1", "tipo": "str"},
    ],
    "demo_inputs": {
        "potencia_motor_W": 1500.0, "tension_V": 220.0, "fase": "monofasica",
        "cos_phi": 0.8, "eficiencia_motor": 0.9,
        "potencia_lampara_W": 100.0, "potencia_toma_W": 2200.0,
        "largo_m": 4.0, "ancho_m": 4.0, "alto_m": 2.6,
        "tipo_instalacion": "embutida",
    },
    "demo_lookup": {},
    "formulas": {"red_electrica": {"fn": calc_red_electrica,
                                    "inputs": ["potencia_motor_W", "tension_V", "fase",
                                               "cos_phi", "eficiencia_motor",
                                               "potencia_lampara_W", "potencia_toma_W",
                                               "largo_m", "ancho_m", "alto_m"]}},
    "calcular": calc_red_electrica,
    "calc_inputs": ["potencia_motor_W", "tension_V", "fase", "cos_phi", "eficiencia_motor",
                    "potencia_lampara_W", "potencia_toma_W", "largo_m", "ancho_m", "alto_m"],
    "constantes": [
        {"name": "CAPACIDAD_CABLE_A", "valor": {1.5: 15.0, 2.5: 21.0, 4.0: 28.0, 6.0: 36.0, 10.0: 50.0},
         "unit": "A", "fuente": "AEA 90364 (capacidad de corriente, Cu PVC, 2 cargados en caño)",
         "porque_no_input": "tabla normativa universal"},
        {"name": "RHO_CU_OHM_MM2_M", "valor": 0.022, "unit": "ohm*mm2/m",
         "fuente": "resistividad del cobre a ~70 °C (para caída de tensión)",
         "porque_no_input": "constante física del material"},
        {"name": "CAIDA_ADMISIBLE_PCT", "valor": 3.0, "unit": "%",
         "fuente": "AEA 90364 (caída admisible en circuito terminal)",
         "porque_no_input": "límite normativo universal"},
        {"name": "SECCION_MIN_*", "valor": "ilum 1.5 / toma 2.5 / motor 2.5", "unit": "mm2",
         "fuente": "AEA 90364 (secciones mínimas por tipo de circuito)",
         "porque_no_input": "mínimos normativos universales"},
        {"name": "DIFERENCIAL_MA", "valor": 30, "unit": "mA",
         "fuente": "AEA 90364 (protección diferencial para personas)",
         "porque_no_input": "valor normativo universal"},
        {"name": "JABALINA_MM2", "valor": 16.0, "unit": "mm2",
         "fuente": "AEA 90364 (conductor tablero→jabalina, mínimo)",
         "porque_no_input": "mínimo normativo universal"},
    ],
    "ranges": {
        "corriente_motor_A": (0.1, 100.0),
        "corriente_lampara_A": (0.01, 50.0),
        "corriente_toma_A": (0.1, 100.0),
        "seccion_iluminacion_mm2": (1.0, 16.0),
        "seccion_toma_mm2": (1.0, 16.0),
        "seccion_motor_mm2": (1.0, 16.0),
        "termica_iluminacion_A": (6.0, 63.0),
        "termica_toma_A": (6.0, 63.0),
        "termica_motor_A": (6.0, 63.0),
        "caida_tension_motor_V": (0.0, 50.0),
        "seccion_pe_mm2": (1.5, 25.0),
        "seccion_jabalina_mm2": (6.0, 50.0),
        "diametro_canio_mm": (16.0, 40.0),
        "longitud_circ_iluminacion_m": (0.5, 100.0),
        "longitud_circ_toma_m": (0.5, 100.0),
        "longitud_circ_motor_m": (0.5, 100.0),
    },
    "dimensiones": {
        "corriente_motor_A": "A",
        "corriente_lampara_A": "A",
        "corriente_toma_A": "A",
        "seccion_iluminacion_mm2": "mm2",
        "seccion_toma_mm2": "mm2",
        "seccion_motor_mm2": "mm2",
        "termica_iluminacion_A": "A",
        "termica_toma_A": "A",
        "termica_motor_A": "A",
        "caida_tension_motor_V": "V",
        "seccion_pe_mm2": "mm2",
        "seccion_jabalina_mm2": "mm2",
        "diametro_canio_mm": "mm",
        "longitud_circ_iluminacion_m": "m",
        "longitud_circ_toma_m": "m",
        "longitud_circ_motor_m": "m",
    },
    "allowlist": {},
    "clave": "corriente_motor_A",
    "correccion": {"input": "in_potencia_motor_W", "valor": 2200.0},
    "ejemplos": (
        'Ejemplo: "calcular la instalación eléctrica para un motor monofásico y una lámpara" → '
        '{"status":"needs_compute","calc_request":{"formula_id":"red_electrica",'
        '"inputs":[{"name":"potencia_motor_W","value":1500,"unit":"W"},'
        '{"name":"tension_V","value":220,"unit":"V"},'
        '{"name":"fase","value":"monofasica","unit":"1"},'
        '{"name":"cos_phi","value":0.8,"unit":"1"},'
        '{"name":"eficiencia_motor","value":0.9,"unit":"1"},'
        '{"name":"potencia_lampara_W","value":100,"unit":"W"},'
        '{"name":"potencia_toma_W","value":2200,"unit":"W"},'
        '{"name":"largo_m","value":4,"unit":"m"},'
        '{"name":"ancho_m","value":4,"unit":"m"},'
        '{"name":"alto_m","value":2.6,"unit":"m"}]}}'
    ),
    "entregable": {
        "titulo": "Instalación eléctrica — motor + iluminación + toma + puesta a tierra",
        "moneda": "ARS",
        "etiquetas": {
            "corriente_motor_A": "corriente motor",
            "corriente_lampara_A": "corriente lámpara",
            "corriente_toma_A": "corriente tomacorriente",
            "seccion_iluminacion_mm2": "sección iluminación",
            "seccion_toma_mm2": "sección tomacorriente",
            "seccion_motor_mm2": "sección motor",
            "termica_iluminacion_A": "térmica iluminación",
            "termica_toma_A": "térmica tomacorriente",
            "termica_motor_A": "térmica motor (curva C)",
            "caida_tension_motor_V": "caída de tensión (motor)",
            "seccion_pe_mm2": "conductor PE",
            "seccion_jabalina_mm2": "conductor tablero->jabalina",
            "diametro_canio_mm": "diámetro caño",
        },
        "esquema": esquema_red_electrica,
        "materiales": materiales_red_electrica,
        "precios": PRECIOS_RED_ELECTRICA,
        "exclusiones": [
            "Dimensionamiento de anteproyecto según AEA 90364; no sustituye la verificación de cortocircuito, selectividad ni la certificación de un electricista/matriculado.",
            "El motor requiere arranque/parada propio y protección térmica de motor (relé) además de la termomagnética del circuito.",
            "BOM asume secciones 1.5/2.5 mm² (motor <= 2.5); si el cálculo diera mayor sección, ampliar el catálogo de precios.",
            "Precios de referencia (drift_temporal): validar contra proveedor.",
        ],
        "notas": notas_red_electrica,
    },
    "requerimientos": [
        {"id": "RE-01", "texto": "Alimentar el motor con circuito propio dimensionado",
         "satisface_con": [
            {"tipo": "salida", "name": "corriente_motor_A"},
            {"tipo": "salida", "name": "seccion_motor_mm2"},
            {"tipo": "salida", "name": "termica_motor_A"},
            {"tipo": "salida", "name": "caida_tension_motor_V"},
            {"tipo": "material", "item": "cable_2.5_mm2"},
            {"tipo": "esquema", "regex": "motor"},
        ]},
        {"id": "RE-02", "texto": "Lámpara LED alimentada desde circuito de iluminación",
         "satisface_con": [
            {"tipo": "salida", "name": "corriente_lampara_A"},
            {"tipo": "salida", "name": "seccion_iluminacion_mm2"},
            {"tipo": "material", "item": "lampara_led_100w"},
            {"tipo": "esquema", "regex": "lampara"},
        ]},
        {"id": "RE-03", "texto": "Un tomacorriente en circuito de tomas",
         "satisface_con": [
            {"tipo": "salida", "name": "corriente_toma_A"},
            {"tipo": "salida", "name": "seccion_toma_mm2"},
            {"tipo": "material", "item": "tomacorriente_10A"},
        ]},
        {"id": "RE-04", "texto": "Tecla de encendido/apagado de la lámpara",
         "satisface_con": [
            {"tipo": "material", "item": "tecla_punto"},
            {"tipo": "esquema", "regex": "tecla"},
        ]},
        {"id": "RE-05", "texto": "Puesta a tierra incluida (PE + jabalina)",
         "satisface_con": [
            {"tipo": "salida", "name": "seccion_pe_mm2"},
            {"tipo": "salida", "name": "seccion_jabalina_mm2"},
            {"tipo": "material", "item": "cable_pe_2.5_mm2"},
            {"tipo": "material", "item": "cable_16_mm2"},
            {"tipo": "material", "item": "jabalina_bornes"},
        ]},
        {"id": "RE-06", "texto": "Protecciones: termomagnéticas por circuito + diferencial 30 mA",
         "satisface_con": [
            {"tipo": "salida", "name": "termica_iluminacion_A"},
            {"tipo": "salida", "name": "termica_toma_A"},
            {"tipo": "salida", "name": "termica_motor_A"},
            {"tipo": "material", "item": "termica_10A"},
            {"tipo": "material", "item": "termica_16A"},
            {"tipo": "material", "item": "termica_16A_curvaC"},
            {"tipo": "material", "item": "diferencial_30mA"},
        ]},
        {"id": "RE-07", "texto": "Cañería adecuada al tipo de instalación",
         "satisface_con": [
            {"tipo": "input", "name": "tipo_instalacion"},
            {"tipo": "salida", "name": "diametro_canio_mm"},
            {"tipo": "material", "item": "canio_corrugado_20mm_tramo3m"},
        ]},
        {"id": "RE-08", "texto": "Plano de planta (disposición) + esquema unifilar",
         "satisface_con": [
            {"tipo": "esquema", "regex": "PLANTA"},
            {"tipo": "esquema", "regex": "UNIFILAR"},
        ]},
        {"id": "RE-09", "texto": "Lista de materiales completa",
         "satisface_con": [
            {"tipo": "seccion", "name": "materiales"},
        ]},
        {"id": "RE-10", "texto": "Presupuesto con precios buscados (no demo) en moneda declarada",
         "satisface_con": [
            {"tipo": "precio"},
            {"tipo": "seccion", "name": "presupuesto"},
        ]},
        {"id": "RE-11", "texto": "Disposición física: tecla junto a puerta, toma en pared opuesta, lámpara centro; longitudes derivadas",
         "satisface_con": [
            {"tipo": "esquema", "regex": "tecla junto a la puerta"},
            {"tipo": "esquema", "regex": "pared opuesta a la puerta"},
            {"tipo": "derivacion", "salida": "longitud_circ_motor_m", "no_input": True},
            {"tipo": "salida", "name": "longitud_circ_iluminacion_m"},
            {"tipo": "salida", "name": "longitud_circ_toma_m"},
            {"tipo": "salida", "name": "longitud_circ_motor_m"},
        ]},
    ],
    "procurement": False,
    "tests": [
        ({"potencia_motor_W": 1500, "tension_V": 220, "fase": "monofasica",
          "cos_phi": 0.8, "eficiencia_motor": 0.9,
          "potencia_lampara_W": 100, "potencia_toma_W": 2200,
          "largo_m": 4, "ancho_m": 4, "alto_m": 2.6},
         {"corriente_motor_A": 9.47, "seccion_motor_mm2": 2.5, "termica_motor_A": 16.0,
          "diametro_canio_mm": 20.0, "seccion_pe_mm2": 2.5}),
        ({"potencia_motor_W": 3000, "tension_V": 380, "fase": "trifasica",
          "cos_phi": 0.85, "eficiencia_motor": 0.9,
          "potencia_lampara_W": 100, "potencia_toma_W": 2200,
          "largo_m": 5, "ancho_m": 3, "alto_m": 2.4},
         {"corriente_motor_A": 5.96, "seccion_motor_mm2": 2.5, "termica_motor_A": 10.0}),
    ],
}


DOMINIOS = {
    "barra_dominadas": DOMINIO_BARRA,
    "motor_electrico": DOMINIO_MOTOR,
    "motor_bobinado": DOMINIO_BOBINA,
    "habitacion": DOMINIO_HABITACION,
    "red_electrica": DOMINIO_RED,
}

# Los dominios sin `report` propio usan el reporte genérico del contrato de entregable.
for _did, _dom in DOMINIOS.items():
    if "report" not in _dom:
        _dom["report"] = report_generico(_did, _dom)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    dominio_id = os.environ.get("ARQ_DOMINIO", "barra_dominadas")
    if dominio_id in ("todos", "motor_completo"):
        tarea = ("Diseñar un motor eléctrico completo: (1) dimensionar potencia y corriente a partir "
                 "de par y velocidad, (2) diseñar el bobinado (sección del alambre, resistencia, peso) "
                 "y verificar el buje usando la corriente ya calculada.")
        return orquestar(tarea, DOMINIOS)
    dominio = DOMINIOS[dominio_id]
    if dominio_id == "motor_electrico":
        tarea = ("Dimensionar un motor eléctrico: calcular la potencia y la corriente necesarias "
                 "a partir del par y la velocidad nominales.")
    elif dominio_id == "motor_bobinado":
        tarea = ("Diseñar el bobinado de un motor: calcular la sección del alambre de cobre, la "
                 "resistencia y el peso de la bobina, y verificar el buje (PV).")
    elif dominio_id == "habitacion":
        tarea = ("Construir una habitación de 4 m × 4 m con puerta estándar de madera "
                 "(0.80 × 2.00 m), sobre terreno regular. Entregar: plano, cómputo de "
                 "mampostería, lista de materiales y presupuesto aproximado.")
    elif dominio_id == "red_electrica":
        tarea = ("Calcular la instalación eléctrica de la habitación: alimentar el motor "
                 "diseñado, una lámpara LED de 100 W y un tomacorriente, con tecla de "
                 "encendido/apagado, puesta a tierra y protecciones. Determinar secciones de "
                 "cable, térmicas, diferencial, puesta a tierra y cañería.")
    else:
        tarea = ("Diseñar una viga para hacer ejercicio (dominadas/barra de tracción) para una "
                 "persona de 180 kg. Luz 2 m, apoyos simples, con factor de carga dinámica. "
                 "Entregar: comparativa de viga T vs doble T, material acero vs hormigón armado, "
                 "peso y estructura necesaria de cada opción, y presupuesto.")
    correr_tarea(tarea, dominio)


if __name__ == "__main__":
    main()
