#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""HCMA architecture dashboard (stdlib only: http.server + sqlite3 + json).

Visualizes telemetry (tokens, cost, latency, bottlenecks) and allows administrative tasks over
memory (governed deletion, statistics, logs).

Usage:
    python3 app.py                    # serves on http://localhost:8787

Configuration (environment variables):
    HCMA_DASH_PORT           port (default 8787)
    HCMA_DASH_TELEMETRY_DB   SQLite telemetry (default: ./telemetry.db)
    HCMA_DASH_MEMORY_DB      HCMA SQLite memory (optional; the orchestrator's ARQ_MEMORIA_DB)
    HCMA_DASH_LLM_CACHE_DIR  historical llm_cache (default: ../scripts/llm_cache)

To populate it from the orchestrator, run the orchestrator with:
    ARQ_TELEMETRY_DB=... ARQ_MEMORIA_DB=...  (see README.md)
"""
import base64
import hashlib
import hmac
import html
import json
import os
import re
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import ipaddress
import socket
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from telemetry import Telemetry  # noqa: E402
import skills  # noqa: E402
import auth  # noqa: E402
import llm_config  # noqa: E402
import llm_gateway  # noqa: E402

PORT = int(os.environ.get("HCMA_DASH_PORT", "8787"))
TELEMETRY_DB = os.environ.get("HCMA_DASH_TELEMETRY_DB", os.path.join(HERE, "telemetry.db"))
MEMORY_DB = os.environ.get("HCMA_DASH_MEMORY_DB", "")
# The monitor's GOVERNANCE socket for HUMAN ACTIONS (review/patch/rehabilitate incidents). In
# production the dashboard does NOT write the store: it sends the op to the monitor (single writer)
# over this separate socket (allowlist of human ops).
MONITOR_SOCK = os.environ.get("HCMA_MONITOR_SOCK", "/run/hcma/gobernanza.sock")
LLM_CACHE_DIR = os.environ.get("HCMA_DASH_LLM_CACHE_DIR", os.path.join(SCRIPTS, "llm_cache"))
INDEX = os.path.join(HERE, "index.html")

COOKIE_NAME = "hcma_session"
TLS = False

tel = Telemetry(TELEMETRY_DB)

MAX_PDF_CHARS = int(os.environ.get("HCMA_DASH_PDF_CHARS", "30000"))
MAX_PDF_BYTES = int(os.environ.get("HCMA_DASH_PDF_BYTES", str(20 * 1024 * 1024)))


def _extract_pdf_text(data_b64):
    """Extracts text from a PDF (base64) via pdftotext (poppler-utils). Returns (text, error).

    The chat attaches PDFs (those produced by HCMA or any other); the extracted text is injected as
    document context (NOT as memory). It is capped to MAX_PDF_CHARS to avoid blowing the LLM context."""
    if not data_b64:
        return "", "sin datos (archivo vacío)"
    try:
        raw = base64.b64decode(data_b64)
    except Exception as e:
        return "", f"base64 inválido: {e}"
    if len(raw) > MAX_PDF_BYTES:
        return "", f"PDF demasiado grande (máx {MAX_PDF_BYTES // (1024*1024)} MB)"
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, raw)
        os.close(fd)
        out = subprocess.run(["pdftotext", "-layout", tmp, "-"],
                             capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return "", "pdftotext no está instalado (poppler-utils)"
    except subprocess.TimeoutExpired:
        return "", "pdftotext agotó el tiempo de extracción"
    except Exception as e:
        return "", f"extracción de PDF: {e}"
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    if out.returncode != 0:
        return "", (out.stderr or "pdftotext no pudo leer el PDF").strip()[:300]
    texto = out.stdout
    return texto[:MAX_PDF_CHARS], ""


MAX_URL_CHARS = int(os.environ.get("HCMA_DASH_URL_CHARS", "30000"))
MAX_URL_BYTES = int(os.environ.get("HCMA_DASH_URL_BYTES", str(2 * 1024 * 1024)))


def _decode_text(raw):
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _strip_html(texto):
    t = re.sub(r"<script[\s\S]*?</script>", " ", texto, flags=re.IGNORECASE)
    t = re.sub(r"<style[\s\S]*?</style>", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", " ", t)
    return html.unescape(t)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Does not follow HTTP redirects: avoids forwarding to an internal host on a 30x (SSRF)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _es_ip_privada(host):
    """True if `host` resolves to a loopback/private/link-local/reserved address (SSRF guard)."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return True  # fail-closed: if it cannot resolve, block it
    for info in infos:
        ip = info[4][0]
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (a.is_loopback or a.is_private or a.is_link_local or a.is_reserved
                or a.is_multicast or a.is_unspecified):
            return True
    return False


def _fetch_url_text(url):
    """Downloads a URL (http/https) and returns its plain text. SSRF-aware: http/https only,
    blocks private/reserved hosts, does NOT follow redirects, with timeout and byte cap."""
    u = (url or "").strip()
    if not u:
        return "", "sin URL"
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        return "", "URL inválida (solo http/https)"
    if not p.hostname or _es_ip_privada(p.hostname):
        return "", "URL bloqueada (host privado/reservado)"
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "HCMA-dashboard/1.0"})
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=30) as r:
            raw = r.read(MAX_URL_BYTES + 1)
    except urllib.error.HTTPError as e:
        return "", f"HTTP {e.code} {e.reason}"
    except Exception as e:
        return "", f"no se pudo descargar: {e}"
    if len(raw) > MAX_URL_BYTES:
        return "", f"contenido demasiado grande (máx {MAX_URL_BYTES // (1024*1024)} MB)"
    texto = _strip_html(_decode_text(raw))
    texto = " ".join(texto.split())
    return texto[:MAX_URL_CHARS], ""


def _extract_documento(filename, data_b64):
    """Extracts text from a document (PDF or other type) from its name and base64 data.
    Returns {tipo, texto, error}. Detects PDF by extension or by %PDF magic."""
    fname = (filename or "").lower()
    raw = None
    if data_b64:
        try:
            raw = base64.b64decode(data_b64)
        except Exception as e:
            return {"tipo": "text", "texto": "", "error": f"base64 inválido: {e}"}
    is_pdf = fname.endswith(".pdf") or (raw is not None and raw[:5] == b"%PDF-")
    if is_pdf:
        texto, err = _extract_pdf_text(data_b64)
        return {"tipo": "pdf", "texto": texto, "error": err}
    texto = _decode_text(raw) if raw is not None else ""
    if not texto:
        return {"tipo": "text", "texto": "", "error": "archivo vacío o no legible como texto"}
    return {"tipo": "text", "texto": texto[:MAX_PDF_CHARS], "error": ""}


def _ssl_context():
    """TLS context with a self-signed certificate (generated once with openssl).

    HCMA_DASH_NO_TLS=1 forces plain HTTP (for local development/tests only)."""
    if os.environ.get("HCMA_DASH_NO_TLS") == "1":
        return None
    cert = os.path.join(HERE, "hcma-dashboard.crt")
    key = os.path.join(HERE, "hcma-dashboard.key")
    if os.path.exists(cert) and os.path.exists(key):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        return ctx
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", key,
             "-out", cert, "-days", "365", "-nodes", "-subj", "/CN=hcma-dashboard"],
            check=True, capture_output=True, timeout=90)
        os.chmod(key, 0o600)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        return ctx
    except Exception as e:
        print(f"⚠️  no se pudo generar cert autofirmado ({e}); sirviendo HTTP plano")
        return None


def _public_user(u):
    return {"username": u["username"], "role": u["role"],
            "totp_enabled": bool(u["totp_enabled"]),
            "must_change_password": bool(u.get("must_change_password")),
            "session_ttl": u.get("session_ttl") or auth.SESSION_TTL}


def _cookie_header(token):
    flags = "Path=/; HttpOnly; SameSite=Strict"
    if TLS:
        flags += "; Secure"
    if token:
        ttl = auth.SESSION_TTL
        s = auth.get_session(token)
        if s and s.get("ttl"):
            ttl = s["ttl"]
        return f"{COOKIE_NAME}={token}; {flags}; Max-Age={ttl}"
    return f"{COOKIE_NAME}=; {flags}; Max-Age=0"


def _components():
    """Health of the HCMA security components: monitor, proxy, gVisor, UID boundary, checkpoint, pin,
    egress gate and multi-agent. It is the 'health of everything' view the dashboard needs to audit the
    deployed system (not just telemetry/memory/skills)."""
    comps = {}

    # 1. systemd services (monitor + proxy)
    for svc in ("hcma-monitor", "hcma-proxy"):
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=5)
            comps[svc] = {"active": r.stdout.strip() == "active",
                           "state": r.stdout.strip()}
        except Exception as e:
            comps[svc] = {"active": False, "state": f"error: {e}"}

    # 2. gVisor (runsc)
    try:
        r = subprocess.run(["runsc", "--version"], capture_output=True, text=True, timeout=5)
        comps["runsc"] = {"installed": r.returncode == 0,
                          "version": r.stdout.strip().splitlines()[0] if r.stdout else ""}
    except Exception:
        comps["runsc"] = {"installed": False, "version": ""}

    # 3. UID boundary: present users
    import pwd
    usuarios = {}
    for u in ("hcma-agent", "hcma-signer", "hcma-monitor", "hcma-proxy"):
        try:
            pwd.getpwnam(u)
            usuarios[u] = True
        except KeyError:
            usuarios[u] = False
    comps["usuarios"] = usuarios

    # 4. UID boundary: can this process (dashboard) read the signer's private key? (should NOT)
    priv_path = "/var/lib/hcma-signer/humano_ed25519.key"
    try:
        open(priv_path, "rb").read(1)
        comps["frontera_uid"] = {"lee_privada": True}   # bad
    except PermissionError:
        comps["frontera_uid"] = {"lee_privada": False}  # good (boundary active)
    except Exception as e:
        comps["frontera_uid"] = {"lee_privada": None, "error": str(e)}

    # 5. trust pin
    pin_path = "/etc/hcma/publica.pin"
    comps["pin"] = {"presente": os.path.exists(pin_path)}

    # 6. checkpoint (frozen store_digest)
    ckp_path = "/etc/hcma/checkpoint.json"
    if os.path.exists(ckp_path):
        try:
            ckp = json.load(open(ckp_path))
            comps["checkpoint"] = {"presente": True,
                                    "store_digest": (ckp.get("store_digest") or "")[:12]}
        except Exception:
            comps["checkpoint"] = {"presente": True, "error": "no parseable"}
    else:
        comps["checkpoint"] = {"presente": False}

    # 7. egress gate (registered http/mcp connectors + static risk)
    try:
        import conectores as CNX
        comps["gate_egreso"] = {"http": "http" in CNX.CONECTORES,
                                 "mcp": "mcp" in CNX.CONECTORES,
                                 "http_riesgo": CNX.RIESGO_POR_CONECTOR.get("http"),
                                 "mcp_riesgo": CNX.RIESGO_POR_CONECTOR.get("mcp")}
    except Exception as e:
        comps["gate_egreso"] = {"error": str(e)}

    # 8. multi-agent (single-writer event loop)
    try:
        import monitor_multiplex  # noqa: F401
        comps["multiagente"] = {"event_loop": True}
    except Exception:
        comps["multiagente"] = {"event_loop": False}

    return comps


def _procedencia_commit():
    """Returns ' · commit <short>' if HEAD can be read, or ''."""
    try:
        import pre_registro as PR
        c = PR.commit_actual()
        return (" · commit " + c[:10]) if c else ""
    except Exception:
        return ""


def _d3_status():
    """Returns the summary of the latest D3 (funnel), or None if there is no scale run."""
    import glob
    base = os.path.join(HERE, "..", "resultados", "d3")
    resumenes = sorted(glob.glob(os.path.join(base, "*", "plain.summary.json")))
    if not resumenes:
        resumenes = sorted(glob.glob(os.path.join(base, "*", "*.summary.json")))
    if not resumenes:
        return None
    ultimo = max(resumenes, key=os.path.getmtime)
    try:
        return json.load(open(ultimo, encoding="utf-8"))
    except Exception:
        return None


def _observabilidad():
    """OBSERVABILITY status (Fable opinion `fable_dashboard_fable.md`).

    Distinguishes LIFE (what /api/components measures) from ENFORCEMENT (what is MISSING:
    reconciliation of external ledgers). Until the external ledgers exist, the P0 enforcement metrics
    are declared 'not instrumented' — instead of faking coverage with the system's own log.
    """
    ej = os.environ.get("HCMA_DASH_EJECUCION_LEDGER", "")
    au = os.environ.get("HCMA_DASH_AUTORIZACION_LEDGER", "")
    # "instrumentado" = the dashboard is CONFIGURED (env set). File existence is SEPARATE: it depends
    # on whether there has already been traffic (no data => the file does not exist yet).
    ej_conf = bool(ej)
    au_conf = bool(au)
    ej_existe = bool(ej_conf and os.path.exists(ej))
    au_existe = bool(au_conf and os.path.exists(au))
    # Arm B (approver sensitivity): deterministic, result in arm_b.json.
    arm_b = None
    arm_b_path = os.path.join(HERE, "..", "resultados", "arm_b", "arm_b.json")
    if os.path.exists(arm_b_path):
        try:
            arm_b = json.load(open(arm_b_path, encoding="utf-8"))
        except Exception:
            arm_b = None
    d3 = _d3_status()
    return {
        "vida": _components(),
        "enforcement": {
            "ledger_ejecucion": {"instrumentado": ej_conf, "existe": ej_existe,
                "detalle": ("ledger de ejecución (proxy)" if ej_conf
                            else "no configurado: set HCMA_DASH_EJECUCION_LEDGER")},
            "ledger_autorizaciones": {"instrumentado": au_conf, "existe": au_existe,
                "detalle": ("nonces consumidos (monitor)" if au_conf
                            else "no configurado: set HCMA_DASH_AUTORIZACION_LEDGER")},
            "reconciliacion": {"instrumentado": ej_conf and au_conf,
                "detalle": ("reconciliación ejecución↔autorización" if (ej_conf and au_conf)
                            else "requiere ambos env (ejecución + autorización)")},
            "embudo_d3": {"instrumentado": d3 is not None,
                "detalle": (f"D3: inyectados={d3['n']} intentados={d3['intentados']} "
                            f"ejecutado_con_gate={d3['ejecutado_con_gate']}"
                            if d3 else "falta correr d3_llm_real.py a escala (oráculo externo + CI)")},
            "atribucion_bloqueo": {"instrumentado": arm_b is not None,
                "detalle": ("Arm B: seguridad 100% mecanismo (oráculo 4/4 maliciosas bloqueadas)"
                            if arm_b else "falta correr arm_b_aprobador.py")},
            "costo_humano": {"instrumentado": arm_b is not None,
                "detalle": ("Arm B: oráculo 3 firmas · por_tipo 6 · ingenuo 7 (por tarea benigna)"
                            if arm_b else "falta correr arm_b_aprobador.py")},
            "procedencia_corrida": {"instrumentado": True,
                "detalle": ("pre_registro.py listo (run_id+commit+modelo+seed+hash dataset)"
                            + _procedencia_commit())},
        },
        "veredicto_fable": (
            "Si no podés mostrar el nº de efectos de riesgo>=1 sin autorización válida (con "
            "control positivo), el dashboard es un espejo complaciente."),
    }


def _reconciliacion():
    """Execution↔authorization reconciliation (P0 Fable), only if both ledgers are configured.

    HCMA_DASH_EJECUCION_LEDGER    -> append-only execution ledger.
    HCMA_DASH_AUTORIZACION_LEDGER -> JSONL ledger of issued and consumed authorizations.
    Without both, reports 'not instrumented' (honest: does not fake coverage).
    """
    ej = os.environ.get("HCMA_DASH_EJECUCION_LEDGER", "")
    au = os.environ.get("HCMA_DASH_AUTORIZACION_LEDGER", "")
    if not ej or not au:
        return {"instrumentado": False,
                "detalle": "requiere HCMA_DASH_EJECUCION_LEDGER y HCMA_DASH_AUTORIZACION_LEDGER"}
    import ledger_ejecucion as LE
    import reconciliacion as RC
    lg = LE.LedgerEjecucion(ej)
    entradas, integra = lg.leer()
    out = {"instrumentado": True,
           "ledger_ejecucion": {"path": ej, "n_entradas": len(entradas),
                               "cadena_integra": integra}}
    auts = []
    if os.path.exists(au):
        for linea in open(au, encoding="utf-8"):
            linea = linea.strip()
            if not linea:
                continue
            try:
                auts.append(json.loads(linea))
            except json.JSONDecodeError:
                pass
    ejecs = [{"tool_id": e["tool_id"], "hash_accion": e["hash_accion"],
              "nonce": e["nonce"], "epoca": e["epoca"]} for e in entradas]
    r = RC.reconciliar(ejecs, auts)
    out["reconciliacion"] = {"instrumentado": True, **r}
    return out


def _memory():
    """Returns a READ-ONLY connection to HCMA memory, or None if not configured.

    PRODUCTION: the dashboard does not write the store (the monitor is the single writer). Opens with
    mode=ro so any write fails with 'readonly database' (defense in depth)."""
    if not MEMORY_DB or not os.path.exists(MEMORY_DB):
        return None
    from memoria_sqlite import HCMAMemoriaSQLite
    return HCMAMemoriaSQLite(MEMORY_DB, crear_esquema=False, read_only=True)


def _skills():
    """Stats + list + ranking + skill lineage tree (navigable/auditable)."""
    m = _memory()
    if m is None:
        return {"disponibles": False, "stats": {"disponibles": False},
                "skills": [], "ranking": [], "arbol": None}
    try:
        return {"disponibles": True,
                "stats": skills.stats(m),
                "skills": skills.list_skills(m),
                "ranking": skills.ranking(m),
                "tests": skills.resultados_test(m),
                "arbol": skills.arbol_skills(m)}
    finally:
        m.close()


def _verify_chain(m):
    """Verifies the facts_log hash chain (tamper-evidence).

    Recomputes h = sha256(prev + '|' + payload) for each entry and compares (1) each stored hash and
    (2) the last one against hcma_meta['last_log_hash']. Returns the state and, if broken, the first
    corrupt seq.

    Reads facts_log and hcma_meta in ONE read transaction (consistent WAL snapshot): before, the
    monitor writes every ~1s (sessions) and the two SELECTs could straddle a write, producing a false
    'BROKEN' (prev≠meta) without the chain actually being broken."""
    c = m.conn
    try:
        c.execute("BEGIN")
        rows = c.execute("SELECT seq, payload, hash FROM facts_log ORDER BY seq").fetchall()
        last = c.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
    finally:
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
    stored_last = last[0] if last else ""
    if not rows:
        return {"ok": True, "entries": 0, "first_bad_seq": None}
    prev = ""
    first_bad = None
    for r in rows:
        h = hashlib.sha256((prev + "|" + r["payload"]).encode()).hexdigest()
        if h != r["hash"]:
            first_bad = r["seq"]
            break
        prev = h
    ok = (first_bad is None) and (prev == stored_last)
    return {"ok": ok, "entries": len(rows), "first_bad_seq": first_bad,
            "chain_matches_meta": prev == stored_last}


def _memory_stats():
    m = _memory()
    if m is None:
        return {"available": False}
    try:
        c = m.conn
        n_facts = c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        n_current = c.execute("SELECT COUNT(*) FROM facts WHERE authority=1").fetchone()[0]
        n_stale = c.execute("SELECT COUNT(*) FROM facts WHERE stale=1").fetchone()[0]
        n_verified = c.execute("SELECT COUNT(*) FROM facts WHERE verified=1").fetchone()[0]
        n_log = c.execute("SELECT COUNT(*) FROM facts_log").fetchone()[0]
        last = c.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
        pending = len(m.pending_unverified())
        return {"available": True, "facts": n_facts, "current": n_current,
                "stale": n_stale, "verified": n_verified, "log_entries": n_log,
                "pending_unverified": pending,
                "last_log_hash": (last[0] if last else ""),
                "integrity": _verify_chain(m)}
    finally:
        m.close()


def _memory_facts(q=None, limit=200):
    m = _memory()
    if m is None:
        return {"available": False, "facts": []}
    try:
        c = m.conn
        cols = "key,value,unit,version,verified,authority,stale,fuente,meta,derived_from"
        if q:
            like = f"%{q}%"
            # searches by key OR by content (value/JSON) OR by unit, to locate a specific text that
            # must be removed (right to be forgotten / legal request).
            rows = c.execute(
                f"SELECT {cols} FROM facts WHERE key LIKE ? OR value LIKE ? OR unit LIKE ? "
                "ORDER BY version DESC LIMIT ?",
                (like, like, like, limit)).fetchall()
        else:
            rows = c.execute(
                f"SELECT {cols} FROM facts ORDER BY version DESC LIMIT ?", (limit,)).fetchall()
        facts = [{"key": r["key"], "value": json.loads(r["value"]), "unit": r["unit"],
                  "version": r["version"], "verified": bool(r["verified"]),
                  "authority": bool(r["authority"]), "stale": bool(r["stale"]),
                  "fuente": r["fuente"], "meta": json.loads(r["meta"] or "{}"),
                  "derived_from": json.loads(r["derived_from"] or "{}")} for r in rows]
        return {"available": True, "facts": facts}
    finally:
        m.close()


def _memory_logs(limit=100):
    m = _memory()
    if m is None:
        return {"available": False, "logs": []}
    try:
        rows = m.conn.execute(
            "SELECT seq,ts,op,key,version,hash FROM facts_log ORDER BY seq DESC LIMIT ?",
            (limit,)).fetchall()
        return {"available": True, "logs": [
            {"seq": r["seq"], "ts": r["ts"], "op": r["op"], "key": r["key"],
             "version": r["version"], "hash": r["hash"]} for r in rows]}
    finally:
        m.close()


# ── Useful memory: timeline, per-key history, provenance ────────────────────
# Fable opinion: the auditor needs (1) a timeline with diff and flags, (2) per-key history,
# (3) provenance (lineage). Flags are ANOMALY SIGNALS computed at read time (not persisted):
# deterministic and without a new table someone could edit.

FUENTE_CONFIABLE = {"calculadora", "usuario", "ingeniero", "tabla"}


def _fjson(row):
    """Decodes a SQLite row from `facts` into a dict with value/meta/derived_from parsed."""
    return {"version": row["version"], "key": row["key"],
            "value": json.loads(row["value"]), "unit": row["unit"],
            "verified": bool(row["verified"]), "authority": bool(row["authority"]),
            "stale": bool(row["stale"]), "fuente": row["fuente"],
            "meta": json.loads(row["meta"] or "{}"),
            "derived_from": json.loads(row["derived_from"] or "{}")}


def _magnitud(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return abs(v)
    if isinstance(v, dict):
        nums = [x for x in v.values() if isinstance(x, (int, float)) and not isinstance(x, bool)]
        return sum(abs(x) for x in nums) if nums else None
    if isinstance(v, (list, tuple)):
        nums = [x for x in v if isinstance(x, (int, float)) and not isinstance(x, bool)]
        return sum(abs(x) for x in nums) if nums else None
    return None


def _tipo(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "numero"
    if isinstance(v, str):
        return "texto"
    if isinstance(v, dict):
        return "objeto"
    if isinstance(v, (list, tuple)):
        return "lista"
    return type(v).__name__


def _flags(f, prev):
    """Anomaly signals of a write (f = new version, prev = the previous one of its key).

    P0: piso_canonico (a canonical was overwritten by a non-canonical), sin_raiz (canonical without a
    trusted root). P1: cambio_unidad, cambio_tipo, cambio_magnitud (>=10x)."""
    flags = []
    if prev and prev["authority"] and prev["verified"] and not prev["stale"]:
        if not f["authority"]:
            flags.append("piso_canonico")
    if f["verified"] and f["authority"] and not f["derived_from"] and f["fuente"] not in FUENTE_CONFIABLE:
        flags.append("sin_raiz")
    if prev:
        if f["unit"] != prev["unit"]:
            flags.append("cambio_unidad")
        if _tipo(f["value"]) != _tipo(prev["value"]):
            flags.append("cambio_tipo")
        m0, m1 = _magnitud(prev["value"]), _magnitud(f["value"])
        if m0 and m1 and m0 > 0:
            r = m1 / m0
            if r >= 10 or r <= 0.1:
                flags.append("cambio_magnitud")
    return flags


def _memory_events():
    """TIMELINE: events (writes + rejections) joining facts_log with facts, with diff (prev→new) and
    anomaly flags. Causal order by `seq` (ts is wall clock)."""
    m = _memory()
    if m is None:
        return {"available": False, "events": []}
    try:
        import soc_memoria as SOC
        c = m.conn
        facts = {}
        by_key = {}
        for r in c.execute("SELECT * FROM facts ORDER BY version").fetchall():
            f = _fjson(r)
            f["_prev"] = by_key.get(f["key"])
            by_key[f["key"]] = f
            facts[f["version"]] = f
        events = []
        for r in c.execute("SELECT * FROM facts_log ORDER BY seq").fetchall():
            op, key, version = r["op"], r["key"], r["version"]
            payload = json.loads(r["payload"]) if r["payload"] else {}
            ev = {"seq": r["seq"], "ts": r["ts"], "op": op, "key": key,
                  "version": version, "hash": (r["hash"] or "")[:16]}
            if op == "write" and version in facts:
                f = facts[version]
                prev = f.get("_prev")
                ev.update({"value": f["value"], "unit": f["unit"], "verified": f["verified"],
                           "authority": f["authority"], "stale": f["stale"], "fuente": f["fuente"],
                           "derived_from": f["derived_from"]})
                if prev:
                    ev["prev_version"] = prev["version"]
                    ev["prev_value"] = prev["value"]
                    ev["prev_fuente"] = prev["fuente"]
                    ev["prev_authority"] = prev["authority"]
                ev["flags"] = _flags(f, prev)
                ev["soc"] = SOC.escanear(f)
            else:
                ev["fuente"] = payload.get("f", "")
                ev["value"] = payload.get("val")
                ev["unit"] = payload.get("u", "")
                ev["verified"] = payload.get("ver")
                ev["derived_from"] = {}
                ev["session_id"] = payload.get("session_id")
                ev["flags"] = []
                ev["soc"] = []
            events.append(ev)
        flagged = [e for e in events if e.get("flags")]
        return {"available": True, "events": events,
                "n_events": len(events), "n_flagged": len(flagged),
                "n_write": sum(1 for e in events if e["op"] == "write")}
    finally:
        m.close()


def _memory_key_history(key):
    """Version HISTORY of a key (how the value changed, who, when, with flags)."""
    m = _memory()
    if m is None:
        return {"available": False, "versions": []}
    try:
        import soc_memoria as SOC
        c = m.conn
        tsmap = {}
        for r in c.execute("SELECT version, ts FROM facts_log WHERE key=? AND op='write'", (key,)).fetchall():
            tsmap.setdefault(r["version"], r["ts"])
        prev = None
        versions = []
        for r in c.execute("SELECT * FROM facts WHERE key=? ORDER BY version", (key,)).fetchall():
            f = _fjson(r)
            f["flags"] = _flags(f, prev)
            f["soc"] = SOC.escanear(f)
            if prev:
                f["prev_value"] = prev["value"]
                f["prev_fuente"] = prev["fuente"]
            f["ts"] = tsmap.get(f["version"], "")
            prev = f
            versions.append(f)
        return {"available": True, "key": key, "versions": versions}
    finally:
        m.close()


def _memory_lineage(version, dir):
    """PROVENANCE: transitive lineage upward (roots) or downward (blast radius)."""
    m = _memory()
    if m is None:
        return {"available": False, "nodes": [], "edges": []}
    try:
        c = m.conn
        fmap = {r["version"]: _fjson(r) for r in c.execute("SELECT * FROM facts").fetchall()}
        nodes, edges, seen, pila = {}, [], set(), [version]
        while pila:
            v = pila.pop()
            if v in seen:
                continue
            seen.add(v)
            f = fmap.get(v)
            if f is None:
                continue
            nodes[v] = f
            if dir == "down":
                for r in c.execute("SELECT child_version FROM lineage WHERE dep_version=?", (v,)).fetchall():
                    edges.append({"de": v, "a": r["child_version"]})
                    pila.append(r["child_version"])
            else:
                for r in c.execute("SELECT dep_version FROM lineage WHERE child_version=?", (v,)).fetchall():
                    edges.append({"de": v, "a": r["dep_version"]})
                    pila.append(r["dep_version"])
        return {"available": True, "version": version, "dir": dir,
                "nodes": list(nodes.values()), "edges": edges}
    finally:
        m.close()


def _memory_arbol(key):
    """COMPLETE TREE of an origin (e.g. doc:pdf:<hash>): root node + transitive descendants. To
    visualize what a legal deletion (right to be forgotten) would touch without affecting others' info."""
    m = _memory()
    if m is None:
        return {"available": False, "raiz": key, "nodes": [], "edges": []}
    try:
        out = m.arbol_documento(key)
        out["available"] = True
        return out
    except Exception as e:
        return {"available": False, "raiz": key, "nodes": [], "edges": [], "error": str(e)}
    finally:
        m.close()


def _memory_documentos():
    """Lists the ingested ORIGINS (doc:*: PDF, text, link) with their meta (filename/url/tipo). For the
    right to be forgotten: from here the tree is seen and the root is deleted (cascade)."""
    m = _memory()
    if m is None:
        return {"available": False, "documentos": []}
    try:
        rows = m.conn.execute(
            "SELECT key, value, version, stale, verified, authority FROM facts "
            "WHERE key LIKE 'doc:%' ORDER BY version DESC").fetchall()
        docs = []
        for r in rows:
            try:
                val = json.loads(r["value"]) if isinstance(r["value"], str) else r["value"]
            except Exception:
                val = r["value"]
            docs.append({"key": r["key"], "value": val, "version": r["version"],
                         "stale": bool(r["stale"]), "verified": bool(r["verified"]),
                         "authority": bool(r["authority"])})
        return {"available": True, "documentos": docs}
    finally:
        m.close()


def _memory_soc():
    """MEMORY SOC: scans all facts with the deterministic structural detector (soc_memoria). Returns
    the flagged ones + those with NO semantic coverage (multi-language), so a fact in another language
    does not stay silently 'clean'."""
    m = _memory()
    if m is None:
        return {"available": False, "flaggeados": [], "sin_cobertura": []}
    try:
        import soc_memoria as SOC
        out = []
        sin_cob = []
        for r in m.conn.execute("SELECT * FROM facts").fetchall():
            f = _fjson(r)
            flags = SOC.escanear(f)
            if flags:
                out.append({"key": f["key"], "version": f["version"], "value": f["value"],
                            "fuente": f["fuente"], "verified": f["verified"],
                            "authority": f["authority"], "stale": f["stale"],
                            "flags": flags})
            elif SOC.cobertura(f).get("regex_semantica") == "n/a":
                # structurally 'clean' but the semantic detector cannot see it (language).
                sin_cob.append({"key": f["key"], "version": f["version"],
                                "fuente": f["fuente"], "verified": f["verified"],
                                "stale": f["stale"],
                                "cobertura": SOC.cobertura(f)})
        out.sort(key=lambda x: (not any(fl["severidad"] == "P0" for fl in x["flags"]), x["key"]))
        sin_cob.sort(key=lambda x: x["key"])
        return {"available": True, "flaggeados": out, "n": len(out),
                "sin_cobertura": sin_cob[:200], "n_sin_cobertura": len(sin_cob)}
    finally:
        m.close()


def _revocaciones_pendientes():
    """PURE READ: proposed (multi-signature) revocations not yet executed + current quorum."""
    m = _memory()
    if m is None:
        return {"available": False, "quorum": None, "pendientes": []}
    try:
        return {"available": True, "quorum": m.quorum_revocacion(),
                "pendientes": m.revocaciones_pendientes()}
    finally:
        m.close()


def _memory_integrity():
    """INTEGRITY: replays the hash chain and reconciles facts_log↔facts (verify). Read-only."""
    m = _memory()
    if m is None:
        return {"available": False}
    try:
        return {"available": True, **m.verify()}
    finally:
        m.close()


def _delete_key(key):
    m = _memory()
    if m is None:
        return {"ok": False, "error": "memoria no configurada"}
    try:
        m.delete(key)
        return {"ok": True, "key": key}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        m.close()


def _incidentes():
    """Lists escape incidents (active + history) from governed memory, with the DERIVED agent state
    (not just the incident) for the dashboard."""
    m = _memory()
    if m is None:
        return {"available": False, "activos": [], "historia": []}
    try:
        import incidentes as INC
        historia = []
        for i in INC.listar(m):
            i = dict(i)
            i["estado_agente"] = INC.estado_agente(m, i.get("agent_id"))
            historia.append(i)
        return {"available": True, "activos": INC.incidentes_activos(m),
                "historia": historia}
    finally:
        m.close()


def _incidente_op_via_monitor(op):
    """Sends ONE incident op to the MONITOR over socket (single writer). Returns {ok, ...}."""
    import socket
    import monitor_ipc as IPC
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10.0)
    try:
        s.connect(MONITOR_SOCK)
    except OSError as e:
        return {"ok": False, "error": f"no se pudo conectar al monitor ({MONITOR_SOCK}): {e}"}
    try:
        cli = IPC.ClienteMonitor(sock=s)
        return cli.pedir(op)
    finally:
        s.close()


def _rehabilitar_directo_via_monitor(iid):
    """HUMAN: QUARANTINE → REHABILITATED (return to production directly, without patch)."""
    return _incidente_op_via_monitor({"tipo": "rehabilitar_incidente", "id": iid})


def _eliminar_via_monitor(iid, motivo):
    """HUMAN: QUARANTINE → DELETED (delete the agent, terminal)."""
    return _incidente_op_via_monitor({"tipo": "eliminar_incidente", "id": iid, "motivo": motivo})


def _escribir_hecho_via_monitor(key, value, unit=""):
    """HUMAN (chat): persists a fact told by the user into governed memory. Goes over the governance
    socket (single writer) as op `escribir_hecho` (source usuario)."""
    return _incidente_op_via_monitor({"tipo": "escribir_hecho", "key": key,
                                      "value": value, "unit": unit})


CHUNK_THRESHOLD = 4000  # if text exceeds this, it is stored mapped in chunks (book)


_REF_RE_LIBRO = re.compile(r"^((?:[A-Za-zÁ-úÑñ0-9]+\s+)+\d+\s*:\s*\d+)")
_REF_RE_CAP = re.compile(r"^(\d+\s*:\s*\d+)")


def _referencia(line):
    """Extracts a 'Book Cap:Vers' (Juan 3:16) or 'Cap:Vers' (3:16) reference at the start, or None."""
    s = (line or "").strip()
    m = _REF_RE_LIBRO.match(s)
    if m:
        return m.group(1).strip()
    m = _REF_RE_CAP.match(s)
    if m:
        return m.group(1).strip()
    return None


def _chunk_text(texto):
    """Splits text into STRUCTURAL chunks with coordinates (page, para) and, if applicable, a REFERENCE
    (verse / numbered section like 'Juan 3:16'). Enables positional retrieval ('page 50, third
    paragraph'), by reference ('Juan 3:16') and by content (FTS).

    Pages are marked with form-feed (\f, which pdftotext emits); within each page, paragraphs by blank
    line. If most paragraphs start with a reference, they are grouped by reference (verse structure)."""
    paginas = re.split(r"\f", texto)
    parrafos = []
    for p_idx, pagina in enumerate(paginas, start=1):
        for q_idx, par in enumerate((p.strip() for p in re.split(r"\n\s*\n", pagina) if p.strip()),
                                    start=1):
            ref = _referencia(par)
            texto_par = par
            if ref and par.startswith(ref):
                # the reference lives in meta.ref; the body does not repeat it
                texto_par = par[len(ref):].strip(" :.-")
            parrafos.append({"text": texto_par, "page": p_idx, "para": q_idx, "ref": ref})
    con_ref = sum(1 for p in parrafos if p["ref"])
    if con_ref and parrafos and con_ref / len(parrafos) >= 0.5:
        chunks = []
        for p in parrafos:
            if p["ref"]:
                chunks.append(p)
            elif chunks:
                chunks[-1]["text"] += "\n" + p["text"]  # line without ref: appended to previous verse
            else:
                chunks.append(p)
        return chunks
    return parrafos


def _ingest_documento(filename, data_b64, url, texto, usuario):
    """Ingests an ORIGIN into governed memory with PROVENANCE (right to be forgotten).

    Supported: PDF (file), text/other documents (file or pasted) and WEB LINK (url).
    1) Extracts/downloads the text.
    2) Creates the root `doc:<tipo>:<sha256>` (claim, meta.tipo='documento', with the URL if applicable).
    3) If it is a LONG text (book): stores it MAPPED in chunks `chunk:<sha>:<n>` with coordinates
       (page, para), to retrieve a PORTION without bringing the whole book. If short: extracts facts
       (LLM) and writes them with `derived_from` → root, so deleting the root reaches the WHOLE tree
       (including what other users re-wrote)."""
    import hashlib as _hl
    if url:
        texto_doc, err = _fetch_url_text(url)
        if err:
            return {"ok": False, "error": err}
        tipo = "url"
        ident = url.strip()
        sha = _hl.sha256(ident.encode("utf-8")).hexdigest()
    elif texto:
        texto_doc = texto[:MAX_PDF_CHARS]
        tipo = "text"
        ident = texto_doc
        sha = _hl.sha256(ident.encode("utf-8")).hexdigest()
    else:
        d = _extract_documento(filename, data_b64)
        if d["error"]:
            return {"ok": False, "error": d["error"]}
        texto_doc = d["texto"]
        tipo = d["tipo"]
        try:
            raw = base64.b64decode(data_b64 or "")
        except Exception:
            raw = None
        ident = (raw or texto_doc.encode("utf-8"))
        sha = _hl.sha256(ident).hexdigest()
    if not texto_doc.strip():
        return {"ok": False, "error": "contenido vacío"}
    doc_key = f"doc:{tipo}:{sha[:16]}"
    doc_meta = {"tipo": "documento", "sub_tipo": tipo, "filename": filename or "",
                "sha256": sha, "chars": len(texto_doc)}
    if url:
        doc_meta["url"] = url.strip()
    r = _incidente_op_via_monitor({"tipo": "escribir_hecho", "key": doc_key,
                                   "value": doc_meta, "unit": "", "usuario": usuario})
    if not r.get("ok"):
        return {"ok": False, "error": "raíz del documento: " + (r.get("detalle") or r.get("error"))}
    doc_version = r.get("version")
    if len(texto_doc) > CHUNK_THRESHOLD:
        # BOOK / long document: store MAPPED in chunks with coordinates (page, para), to retrieve a
        # PORTION (e.g. a verse or a paragraph) without bringing the whole book.
        chunks = _chunk_text(texto_doc)
        n = 0
        for i, c in enumerate(chunks):
            meta_c = {"n": i, "page": c["page"], "para": c["para"], "chars": len(c["text"])}
            if c.get("ref"):
                meta_c["ref"] = c["ref"]
            rr = _incidente_op_via_monitor({"tipo": "escribir_chunk",
                                            "key": f"chunk:{sha[:16]}:{i}",
                                            "value": c["text"], "usuario": usuario,
                                            "meta": meta_c,
                                            "derived_from": {doc_key: doc_version}})
            if rr.get("ok"):
                n += 1
        return {"ok": True, "doc_key": doc_key, "tipo": tipo, "sha256": sha,
                "chars": len(texto_doc), "n_chunks": n,
                "n_pages": (chunks[-1]["page"] if chunks else 0)}
    facts = []
    try:
        import llm_gateway as LG
        facts = LG.extract_facts([{"role": "user", "content": "Texto del documento:\n" + texto_doc[:15000]}])
    except Exception as e:
        return {"ok": False, "error": "extracción de hechos: %s" % e}
    n = 0
    for f in facts[:100]:
        rr = _incidente_op_via_monitor({"tipo": "escribir_hecho", "key": f["key"],
                                        "value": f["value"], "unit": f.get("unit", ""),
                                        "usuario": usuario,
                                        "derived_from": {doc_key: doc_version}})
        if rr.get("ok"):
            n += 1
    return {"ok": True, "doc_key": doc_key, "tipo": tipo, "sha256": sha,
            "chars": len(texto_doc), "n_hechos": n}


def _ingest_pdf(filename, data_b64, usuario):
    """Compat: PDF ingestion via the unified path (tipo 'pdf')."""
    return _ingest_documento(filename, data_b64, None, None, usuario)


def _proponer_revocacion_via_monitor(key, autor, motivo, categoria="otro"):
    """HUMAN: STEP 1 of multi-signature deletion (proposes; does NOT execute until quorum)."""
    return _incidente_op_via_monitor({"tipo": "proponer_revocacion_hecho", "key": key,
                                      "autor": autor, "motivo": motivo,
                                      "categoria": categoria})


def _registrar_pubkey_via_monitor(usuario, pubkey_hex):
    """HUMAN: registers the validator's Ed25519 public key in memory (governed fact)."""
    return _incidente_op_via_monitor({"tipo": "registrar_pubkey_hecho",
                                      "usuario": usuario, "pubkey_hex": pubkey_hex})


def _votar_revocacion_via_monitor(key, autor):
    """HUMAN: STEP 2 (Ed25519 SIGNED vote from a different validator; at quorum it executes the
    tombstone). The dashboard SIGNS the vote with the authenticated user's key and registers their
    public key in memory before voting (P0 A5, real multi-signature)."""
    # 1) hash of the pending proposal (read-only memory read)
    hash_propuesta = None
    m = _memory()
    if m is not None:
        try:
            pend = m.current_raw("revocacion_pendiente:" + key)
            if pend is not None:
                hash_propuesta = (pend.get("value") or {}).get("hash_propuesta")
        finally:
            m.close()
    if not hash_propuesta:
        return {"ok": False, "error": "no hay propuesta pendiente (o sin hash) para firmar"}
    # 2) ensure the user's key + register their public key in memory (idempotent)
    try:
        pub = auth.ensure_ed25519(autor)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    reg = _registrar_pubkey_via_monitor(autor, pub)
    if not reg.get("ok"):
        return {"ok": False, "error": f"registrar pubkey: {reg.get('detalle') or reg.get('error')}"}
    # 3) sign and vote
    ts_exp = int(time.time()) + 300
    try:
        firma = auth.sign_voto(autor, "voto_revocacion", hash_propuesta, ts_exp)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return _incidente_op_via_monitor({"tipo": "votar_revocacion_hecho", "key": key,
                                      "autor": autor, "firma_hex": firma, "ts_exp": ts_exp})


def _set_quorum_revocacion_via_monitor(n, autor):
    """HUMAN (admin): configures how many distinct validators are required to delete."""
    return _incidente_op_via_monitor({"tipo": "set_quorum_revocacion_hecho",
                                      "quorum": n, "autor": autor})


def _restaurar_hecho_via_monitor(key, autor, motivo):
    """HUMAN: removes a key's denylist (allows re-learning it)."""
    return _incidente_op_via_monitor({"tipo": "restaurar_hecho", "key": key,
                                      "autor": autor, "motivo": motivo})


def _parchar_rehabilitar_via_monitor(iid, mitigacion):
    """HUMAN: QUARANTINE → EN_REVISION → PATCHED → REHABILITATED (remove threat and re-enable).

    The dashboard does NOT write the store: forwards the transitions to the single writer (monitor).
    Each transition is recorded in the monitor's journal. Returns {ok, ...} to answer the HTTP."""
    import socket
    import monitor_ipc as IPC
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10.0)
    try:
        s.connect(MONITOR_SOCK)
    except OSError as e:
        return {"ok": False, "error": f"no se pudo conectar al monitor ({MONITOR_SOCK}): {e}"}
    try:
        cli = IPC.ClienteMonitor(sock=s)
        r1 = cli.pedir({"tipo": "revisar_incidente", "id": iid, "detalle": "revisado desde dashboard"})
        if not r1.get("ok"):
            return {"ok": False, "error": f"revisar: {r1.get('detalle')}"}
        r2 = cli.pedir({"tipo": "parchear_incidente", "id": iid, "mitigacion": mitigacion})
        if not r2.get("ok"):
            return {"ok": False, "error": f"parchear: {r2.get('detalle')}"}
        r3 = cli.pedir({"tipo": "rehabilitar_incidente", "id": iid})
        return {"ok": bool(r3.get("ok")), "id": iid,
                "estado": "rehabilitado" if r3.get("ok") else None,
                "detalle": r3.get("detalle")}
    finally:
        s.close()


def _herramientas():
    """Tool CMDB: lists the `herramienta:<id>:estado` table (state/origin/hash)."""
    m = _memory()
    if m is None:
        return {"available": False, "herramientas": []}
    try:
        import herramientas as HR
        return {"available": True, "herramientas": HR.listar(m)}
    finally:
        m.close()


def _herramienta_accion_via_monitor(tool_id, tipo_op, extra=None):
    """Sends a HUMAN tool-governance ACTION to the MONITOR over socket.

    The dashboard does NOT write the store: forwards the transition to the single writer (monitor). The
    ops are those of the GOBERNANZA_OPS allowlist (approve installation/cabling, mark installed,
    reject). The agent never triggers them (only the human operates the dashboard)."""
    import socket
    import monitor_ipc as IPC
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10.0)
    try:
        s.connect(MONITOR_SOCK)
    except OSError as e:
        return {"ok": False, "error": f"no se pudo conectar al monitor ({MONITOR_SOCK}): {e}"}
    try:
        cli = IPC.ClienteMonitor(sock=s)
        op = {"tipo": tipo_op, "tool_id": tool_id}
        if extra:
            op.update(extra)
        return cli.pedir(op)
    finally:
        s.close()


# ── governed action (quorum for security-degrading actions) ────────────────
# De-quarantine/rehabilitate, delete, patch-and-remove (incidents) and approve installation/cabling
# (tools) MUST require quorum (Ed25519 multi-signature) unless there is a single user who can act.
# With a single user there is no co-validator: it executes directly (audited).


def _n_usuarios_activos():
    """How many enabled users can EXECUTE actions (admin/usuario, not visor)."""
    return sum(1 for u in auth.list_users()
               if u.get("enabled") and u.get("role") in ("admin", "usuario"))


def _accion_directa_via_monitor(tipo_accion, objetivo, autor, payload=None):
    """Executes a security action DIRECTLY (without quorum) over the monitor's already-audited channel.
    Only used in single-user mode (no co-validator possible)."""
    payload = payload or {}
    if tipo_accion == "rehabilitar_incidente":
        return _incidente_op_via_monitor({"tipo": "rehabilitar_incidente", "id": objetivo})
    if tipo_accion == "eliminar_incidente":
        return _incidente_op_via_monitor({"tipo": "eliminar_incidente", "id": objetivo,
                                          "motivo": payload.get("motivo", "")})
    if tipo_accion == "parchear_rehabilitar_incidente":
        return _parchar_rehabilitar_via_monitor(objetivo, payload.get("mitigacion", ""))
    if tipo_accion == "aprobar_instalacion_herramienta":
        return _herramienta_accion_via_monitor(objetivo, "aprobar_instalacion_herramienta")
    if tipo_accion == "aprobar_cableado_herramienta":
        return _herramienta_accion_via_monitor(objetivo, "aprobar_cableado_herramienta")
    return {"ok": False, "error": f"acción de seguridad desconocida: {tipo_accion}"}


def _proponer_accion_gobernada_via_monitor(tipo_accion, objetivo, autor, motivo, payload=None):
    """HUMAN: STEP 1 of a governed action (proposes; does NOT execute until quorum)."""
    return _incidente_op_via_monitor({"tipo": "proponer_accion_gobernada",
                                      "tipo_accion": tipo_accion, "objetivo": objetivo,
                                      "autor": autor, "motivo": motivo,
                                      "payload": payload or {}})


def _votar_accion_gobernada_via_monitor(hash_propuesta, autor):
    """HUMAN: STEP 2 (Ed25519 SIGNED vote; at quorum the monitor executes the domain)."""
    m = _memory()
    if m is not None:
        try:
            pend = m.current_raw("accion_gobernada:" + hash_propuesta)
            if pend is not None:
                hash_calc = (pend.get("value") or {}).get("hash_propuesta") or hash_propuesta
            else:
                hash_calc = hash_propuesta
        finally:
            m.close()
    else:
        hash_calc = hash_propuesta
    try:
        pub = auth.ensure_ed25519(autor)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    reg = _registrar_pubkey_via_monitor(autor, pub)
    if not reg.get("ok"):
        return {"ok": False, "error": f"registrar pubkey: {reg.get('detalle') or reg.get('error')}"}
    ts_exp = int(time.time()) + 300
    try:
        firma = auth.sign_voto(autor, "voto_accion_gobernada", hash_calc, ts_exp)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return _incidente_op_via_monitor({"tipo": "votar_accion_gobernada",
                                      "hash_propuesta": hash_propuesta,
                                      "autor": autor, "firma_hex": firma, "ts_exp": ts_exp})


def _acciones_gobernadas():
    """PURE READ: pending governed actions + quorum + number of users who can act."""
    m = _memory()
    if m is None:
        return {"available": False, "quorum": 1, "n_usuarios": _n_usuarios_activos(),
                "pendientes": []}
    try:
        return {"available": True, "quorum": m.quorum_revocacion(),
                "n_usuarios": _n_usuarios_activos(),
                "pendientes": m.acciones_gobernadas_pendientes()}
    finally:
        m.close()


def _inventario():
    """System SBOM (binaries + versions + mathlib + python packages + vulnerabilities)."""
    import inventario as INV
    inv = INV.enumerar()
    return {"binarios": inv["binarios"],
            "mathlib_version": inv["mathlib_version"],
            "n_paquetes_python": inv["n_paquetes_python"],
            "vulnerabilidades": inv["vulnerabilidades"]}


def _inventario_manifesto():
    """Generates the FROZEN manifest (JSON + hash) for separate replication/scanning."""
    import inventario as INV
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resultados",
                        "inventario_manifest.json")
    doc = INV.manifiesto(path)
    return {"ok": True, "hash": doc["hash"], "path": path}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence the standard access log

    def _send(self, status, payload, cookie=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self):
        if not os.path.exists(INDEX):
            self._send(404, {"error": "index.html no encontrado"})
            return
        body = open(INDEX, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── authentication / RBAC ────────────────────────────────────────────────
    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _session_token(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME:
                return v
        return None

    def _current_user(self):
        tok = self._session_token()
        s = auth.get_session(tok) if tok else None
        return auth.user(s["username"]) if s else None

    def _authorized(self):
        u = self._current_user()
        if u is None:
            self._send(401, {"ok": False, "error": "no autenticado"})
            return False
        if not u.get("enabled"):
            self._send(403, {"ok": False, "error": "usuario deshabilitado"})
            return False
        if not auth.check_ip(u, self.client_address[0]):
            self._send(403, {"ok": False, "error": "IP no autorizada"})
            return False
        self._user = u
        return True

    def _require_admin(self):
        if self._user.get("role") != "admin":
            self._send(403, {"ok": False, "error": "requiere rol admin"})
            return False
        return True

    def _can_act(self):
        if not auth.can_act(self._user.get("role")):
            self._send(403, {"ok": False, "error": "rol visor: solo lectura"})
            return False
        return True

    def _login(self):
        body = self._read_json()
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        code = (body.get("code") or "").strip()
        ip = self.client_address[0]
        # anti-brute-force: bloquear tras intentos fallidos excesivos (registrado en login_attempts)
        if auth.is_locked(username):
            return self._send(423, {"ok": False, "locked": True,
                                   "error": "cuenta bloqueada por intentos excesivos (1 día)."})
        u = auth.authenticate(username, password)
        if u is None:
            auth.record_attempt(username, ip, success=False, event="credenciales_invalidas")
            return self._send(401, {"ok": False, "error": "credenciales inválidas"})
        auth.record_attempt(username, ip, success=True, event="login")
        if not auth.check_ip(u, self.client_address[0]):
            return self._send(403, {"ok": False, "error": "IP no autorizada"})
        if u.get("totp_enabled"):
            if not auth.verify_totp(u["totp_secret"], code):
                return self._send(401, {"ok": False, "error": "código 2FA inválido", "need_code": True})
            auth.touch_login(username)
            token = auth.create_session(username)
            return self._send(200, {"ok": True, "user": _public_user(u)},
                              cookie=_cookie_header(token))
        # primer login: forzar activación de 2FA
        secret = auth.ensure_totp_secret(username)
        if not code:
            return self._send(200, {"ok": False, "need_setup": True, "secret": secret,
                                    "otpauth_url": auth.totp_uri(secret, username)})
        if not auth.verify_totp(secret, code):
            return self._send(401, {"ok": False, "error": "código 2FA inválido", "need_setup": True})
        auth.enable_totp(username, secret)
        auth.touch_login(username)
        token = auth.create_session(username)
        return self._send(200, {"ok": True, "user": _public_user(auth.user(username))},
                          cookie=_cookie_header(token))

    def _logout(self):
        tok = self._session_token()
        if tok:
            auth.destroy_session(tok)
        return self._send(200, {"ok": True}, cookie=_cookie_header(None))

    def _change_own_password(self):
        """Own password change (self-service). Clears must_change_password."""
        body = self._read_json()
        try:
            auth.change_own_password(self._user["username"],
                                     body.get("old_password", ""),
                                     body.get("new_password", ""))
        except ValueError as e:
            return self._send(400, {"ok": False, "error": str(e)})
        return self._send(200, {"ok": True})

    def _llm_config_set(self):
        """Saves the LLM config (panel). Whitelist only; the API key is never overwritten with empty nor
        with the masked placeholder. `api_key: null` DELETES the active provider's key. Admin-only (set the
        LLM connection)."""
        body = self._read_json()
        data = {}
        for k in llm_config.FIELDS:
            if k not in body:
                continue
            v = body[k]
            if k == "api_key":
                if v is None:
                    data["api_key"] = None  # explicit deletion
                elif str(v) and not str(v).startswith("••••"):
                    data["api_key"] = v  # new key
                continue
            data[k] = v
        if "model_type" in data:
            mt = data["model_type"]
            if mt in ("", None, False):
                data["model_type"] = ""
            elif mt not in ("text", "image_in", "image_out", "audio_in", "audio_out",
                            "video", "multimodal"):
                return self._send(400, {"ok": False, "error": "model_type inválido: %s" % mt})
        if "local_llm" in body:
            ll = body["local_llm"]
            if not isinstance(ll, dict):
                return self._send(400, {"ok": False, "error": "local_llm debe ser un objeto"})
            try:
                port = int(ll.get("port") or 8080)
            except (TypeError, ValueError):
                return self._send(400, {"ok": False, "error": "local_llm.port inválido"})
            data["local_llm"] = {
                "enabled": bool(ll.get("enabled")),
                "port": max(1, min(port, 65535)),
                "model": str(ll.get("model") or "local").strip() or "local",
            }
        llm_config.save(data)
        return self._send(200, {"ok": True, "config": llm_config.masked()})

    def _llm_key_delete(self):
        """Deletes ONE provider's API key (without changing the active provider). Admin-only.

        Fable (fable_llm_key_perdida): deletion must not go through `save` because `save` would also change
        the active provider."""
        body = self._read_json()
        provider = body.get("provider") or ""
        if not provider:
            return self._send(400, {"ok": False, "error": "falta provider"})
        llm_config.delete_api_key(provider)
        return self._send(200, {"ok": True, "config": llm_config.masked()})

    def _llm_test(self):
        """Tests the provider connection (lists /models). Uses the form values if they come in the body.
        The key is resolved ANTI-LEAK via conn_for: only reuses the provider's saved key if the origin of
        base_url matches the saved one (never sends the key to an arbitrary host)."""
        body = self._read_json()
        base, key, provider = llm_gateway.conn_for(
            body.get("provider"), body.get("base_url"), body.get("api_key"))
        if not key:
            return self._send(400, {"ok": False, "error": "API key no configurada"})
        n, err = llm_gateway.test_models(base, key, provider)
        if err:
            return self._send(502, {"ok": False, "error": err})
        return self._send(200, {"ok": True, "modelos": n,
                                "detalle": "conexión OK (%d modelos)" % n})

    def _restart(self):
        """Restarts the DASHBOARD (not the governed runtime). Admin-only + audited.

        Rootless mechanism: exits with a code != 0 and systemd `Restart=on-failure` relaunches it. Only
        applies when running under hcma-dashboard.service; in dev (python3 app.py) it simply terminates the
        process."""
        import threading
        actor = self._user.get("username") if self._user else "?"
        print(f"[restart] reinicio del dashboard solicitado por {actor}", flush=True)
        def _do():
            time.sleep(1.0)  # give margin for the response to reach the client
            os._exit(1)      # exit code != 0 => systemd Restart=on-failure relaunches
        threading.Thread(target=_do, daemon=True).start()
        return self._send(200, {"ok": True,
                                "detalle": "reiniciando dashboard (logueate de nuevo en ~5s)"})

    def _gateway_authorized(self):
        """OpenAI-compatible gateway auth: Authorization: Bearer <gateway_key>.

        The gateway key is its own (llm_config.gateway_key), distinct from the provider's API key.
        Constant-time comparison (hmac.compare_digest)."""
        auth = self.headers.get("Authorization", "")
        token = ""
        if auth[:7].lower() == "bearer ":
            token = auth[7:].strip()
        if not token or not hmac.compare_digest(token, llm_config.gateway_key()):
            self._send(401, {"error": {"message": "gateway key inválida o ausente",
                                       "type": "invalid_request_error", "code": "invalid_api_key"}})
            return False
        return True

    def _llm_gateway_chat(self):
        body = self._read_json()
        try:
            resp = llm_gateway.chat_completions(body)
        except llm_gateway.GatewayError as e:
            return self._send(e.status, {"error": {"message": e.message,
                                                   "type": "api_error", "code": "gateway_error"}})
        except Exception as e:
            return self._send(500, {"error": {"message": "gateway: %s" % e,
                                               "type": "api_error", "code": "internal"}})
        return self._send(200, resp)

    def _llm_gateway_key_regenerate(self):
        """Regenerates the gateway key and returns the NEW one in clear (shown ONCE). Admin."""
        key = llm_config.regenerate_gateway_key()
        return self._send(200, {"ok": True, "gateway_key": key})

    def _llm_models(self):
        """Lists the provider's FREE models (catalog /models, pricing=0), with their type.

        GET: uses the saved config. POST: body {provider, base_url, api_key?, type?} to use the current
        form. The key is resolved anti-leak via conn_for (only reuses the saved one if the origin of
        base_url matches the saved one)."""
        body = {}
        if getattr(self, "command", "") == "POST":
            try:
                body = self._read_json() or {}
            except Exception:
                body = {}
        qs = parse_qs(urlparse(self.path).query)
        tipo = body.get("type") or qs.get("type", [""])[0] or None
        # `free_only` only makes sense on OpenRouter (the only one with pricing=0). For other
        # providers the frontend sends free_only=False and the catalog returns all.
        free_only = bool(body.get("free_only", True))
        if "free_only" in qs:
            free_only = qs.get("free_only", [""])[0].lower() not in ("0", "false", "no")
        try:
            models = llm_gateway.fetch_models(
                free_only=free_only, type=tipo,
                provider=body.get("provider"), base_url=body.get("base_url"),
                api_key=body.get("api_key"))
        except llm_gateway.GatewayError as e:
            return self._send(e.status, {"ok": False, "error": e.message})
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)})
        return self._send(200, {"ok": True, "models": models})

    def _chat_attach_pdf(self):
        """Attaches a PDF to the chat: extracts its text (pdftotext) and returns it to the frontend.

        The frontend sends {filename, data(base64)}; the backend returns {text, chars} so the text is
        injected as document context (not memory) in the chat turn."""
        body = self._read_json()
        filename = body.get("filename") or "documento.pdf"
        if not isinstance(filename, str) or not filename.strip():
            filename = "documento.pdf"
        texto, err = _extract_pdf_text(body.get("data") or "")
        if err:
            return self._send(400, {"ok": False, "error": err})
        return self._send(200, {"ok": True, "filename": filename,
                                "chars": len(texto), "text": texto})

    def _chat_ingest(self):
        """Ingests an ORIGIN (PDF, text/other document, or web link) into governed memory.

        Body: {filename?, data(base64)?, url?, texto?}. Creates root doc:<tipo>:<sha256> + facts with
        derived_from (lineage), for legal deletion by inheritance."""
        body = self._read_json()
        filename = body.get("filename") or ""
        usuario = (self._user or {}).get("username", "desconocido")
        r = _ingest_documento(filename, body.get("data") or "",
                              body.get("url") or "", body.get("texto") or "", usuario)
        return self._send(200 if r.get("ok") else 400, r)

    def _chat_ingest_pdf(self):
        """Compat: alias of unified ingestion (PDF only)."""
        return self._chat_ingest()

    def _chat(self):
        """Dashboard chat (authenticated session): calls the gateway with optional HCMA memory.

        If `use_memory` is active, besides INJECTING current memory, it extracts the facts declared by the
        user in the last turn and PERSISTS them via the monitor (source usuario), so they remain available
        to any other model/session."""
        body = self._read_json()
        messages = body.get("messages") or []
        if not messages:
            return self._send(400, {"ok": False, "error": "falta messages"})
        usuario = (self._user or {}).get("username", "")
        req = {
            "model": body.get("model") or llm_config.load().get("model"),
            "messages": messages,
            "hcma_memory": bool(body.get("use_memory")),
            "usuario": usuario,
        }
        if body.get("attachments"):
            req["attachments"] = body["attachments"]
        if body.get("skill"):
            req["skill"] = body["skill"]
        if body.get("crear_skill"):
            req["crear_skill"] = True
        if body.get("reasoning") is not None:
            req["reasoning"] = body["reasoning"]
        try:
            resp = llm_gateway.chat_completions(req)
        except llm_gateway.GatewayError as e:
            return self._send(e.status, {"ok": False, "error": e.message})
        except Exception as e:
            return self._send(500, {"ok": False, "error": "chat: %s" % e})
        if req["hcma_memory"]:
            try:
                import writeback_queue as WB
                WB.enqueue(messages, usuario=usuario)
                cand = resp.get("cache_candidate")
                if cand:
                    WB.enqueue_respuesta(cand)
            except Exception:
                pass  # the durable queue fails open (does not break the chat)
        return self._send(200, {"ok": True, "response": resp})

    def _users_op(self, path):
        body = self._read_json()
        try:
            if path == "/api/users":
                auth.create_user(body.get("username", ""), body.get("password", ""),
                                 body.get("role", "visor"), body.get("allowed_ips", ""))
                return self._send(200, {"ok": True})
            if path == "/api/users/update":
                # session_ttl comes in SECONDS (0 or empty => global default)
                ttl = body.get("session_ttl")
                if ttl in ("", None):
                    ttl = None
                auth.update_user(body.get("username", ""), role=body.get("role"),
                                 enabled=body.get("enabled"),
                                 allowed_ips=body.get("allowed_ips"),
                                 session_ttl=ttl)
                return self._send(200, {"ok": True})
            if path == "/api/users/delete":
                auth.delete_user(body.get("username", ""), self._user["username"])
                return self._send(200, {"ok": True})
            if path == "/api/users/reset_password":
                auth.set_password(body.get("username", ""), body.get("password", ""))
                return self._send(200, {"ok": True})
            if path == "/api/users/reset_totp":
                username = body.get("username", "")
                secret = auth.reset_totp(username)
                return self._send(200, {"ok": True, "secret": secret,
                                        "otpauth_url": auth.totp_uri(secret, username)})
            return self._send(404, {"ok": False, "error": "op de usuario desconocida"})
        except ValueError as e:
            return self._send(400, {"ok": False, "error": str(e)})

    def do_GET(self):
        path = urlparse(self.path).path
        # The SPA is ALWAYS served (no auth): login is handled by the JS via /api/me.
        if path == "/":
            return self._send_html()
        if path == "/api/me":
            u = self._current_user()
            if u is None:
                return self._send(401, {"ok": False, "error": "no autenticado"})
            if not u.get("enabled") or not auth.check_ip(u, self.client_address[0]):
                return self._send(403, {"ok": False, "error": "sin acceso"})
            return self._send(200, {"ok": True, "user": _public_user(u)})
        if path == "/v1/models":
            if not self._gateway_authorized():
                return
            return self._send(200, llm_gateway.list_models())
        if not self._authorized():
            return
        if path == "/api/users":
            if not self._require_admin():
                return
            return self._send(200, {"users": auth.list_users()})
        if path == "/api/login_attempts":
            if not self._require_admin():
                return
            return self._send(200, {"attempts": auth.login_attempts(),
                                    "max_attempts": auth.MAX_LOGIN_ATTEMPTS,
                                    "lockout_seconds": auth.LOGIN_LOCKOUT_SECONDS})
        if path == "/api/overview":
            return self._send(200, {"telemetry": tel.overview(),
                                    "memory": _memory_stats(),
                                    "timeline": tel.timeline()})
        if path == "/api/models":
            return self._send(200, {"models": tel.per_model(), "types": tel.per_type()})
        if path == "/api/latency":
            return self._send(200, {"latency": tel.latency(), "slowest": tel.slowest(),
                                    "per_model": tel.latency_per_model()})
        if path == "/api/memory":
            q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            return self._send(200, {"stats": _memory_stats(),
                                    "facts": _memory_facts(q),
                                    "logs": _memory_logs()})
        if path == "/api/memory/events":
            return self._send(200, _memory_events())
        if path == "/api/memory/key":
            qs = parse_qs(urlparse(self.path).query)
            return self._send(200, _memory_key_history(qs.get("key", [""])[0]))
        if path == "/api/memory/lineage":
            qs = parse_qs(urlparse(self.path).query)
            try:
                version = int(qs.get("version", ["0"])[0])
            except ValueError:
                version = 0
            return self._send(200, _memory_lineage(version, qs.get("dir", ["up"])[0]))
        if path == "/api/memory/arbol":
            qs = parse_qs(urlparse(self.path).query)
            return self._send(200, _memory_arbol(qs.get("key", [""])[0]))
        if path == "/api/memory/documentos":
            return self._send(200, _memory_documentos())
        if path == "/api/memory/soc":
            return self._send(200, _memory_soc())
        if path == "/api/memory/revocaciones":
            return self._send(200, _revocaciones_pendientes())
        if path == "/api/memory/integrity":
            return self._send(200, _memory_integrity())
        if path == "/api/skills":
            return self._send(200, _skills())
        if path == "/api/components":
            return self._send(200, {"components": _components()})
        if path == "/api/observabilidad":
            return self._send(200, _observabilidad())
        if path == "/api/reconciliacion":
            return self._send(200, _reconciliacion())
        if path == "/api/incidentes":
            return self._send(200, _incidentes())
        if path == "/api/herramientas":
            return self._send(200, _herramientas())
        if path == "/api/acciones":
            return self._send(200, _acciones_gobernadas())
        if path == "/api/inventario":
            return self._send(200, _inventario())
        if path == "/api/llm/config":
            return self._send(200, llm_config.masked())
        if path == "/api/llm/gateway_key":
            if not self._require_admin():
                return
            return self._send(200, {"ok": True, "gateway_key": llm_config.gateway_key()})
        if path == "/api/llm/models":
            return self._llm_models()
        if path == "/api/health":
            return self._send(200, {"ok": True, "telemetry_db": TELEMETRY_DB,
                                    "memory_db": MEMORY_DB or None})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        # DASHBOARD READ-ONLY (Fable opinion): does NOT mutate evidence. CONTROLLED exception:
        # /api/incidentes/rehabilitar and /api/herramientas/accion are HUMAN governance ACTIONS, not
        # evidence mutations; they go over the MONITOR (single writer) via socket and are recorded in
        # the journal as attributable transitions. The `visor` role cannot execute them.
        path = urlparse(self.path).path
        if path == "/api/login":
            return self._login()
        if path == "/api/logout":
            return self._logout()
        if path == "/v1/chat/completions":
            if not self._gateway_authorized():
                return
            return self._llm_gateway_chat()
        if not self._authorized():
            return
        if path == "/api/llm/gateway_key/regenerate":
            if not self._require_admin():
                return
            return self._llm_gateway_key_regenerate()
        if path == "/api/chat":
            if not self._can_act():
                return
            return self._chat()
        if path == "/api/chat/attach_pdf":
            if not self._can_act():
                return
            return self._chat_attach_pdf()
        if path == "/api/chat/ingest":
            if not self._can_act():
                return
            return self._chat_ingest()
        if path == "/api/chat/ingest_pdf":
            if not self._can_act():
                return
            return self._chat_ingest_pdf()
        if path == "/api/change_own_password":
            return self._change_own_password()
        if path == "/api/llm/config":
            if not self._require_admin():
                return
            return self._llm_config_set()
        if path == "/api/llm/test":
            if not self._can_act():
                return
            return self._llm_test()
        if path == "/api/llm/key/delete":
            if not self._require_admin():
                return
            return self._llm_key_delete()
        if path == "/api/llm/models":
            if not self._can_act():
                return
            return self._llm_models()
        if path == "/api/restart":
            if not self._require_admin():
                return
            return self._restart()
        if path in ("/api/users", "/api/users/update", "/api/users/delete",
                    "/api/users/reset_password", "/api/users/reset_totp"):
            if not self._require_admin():
                return
            return self._users_op(path)
        if path in ("/api/incidentes/rehabilitar", "/api/incidentes/parchar",
                    "/api/incidentes/cuarentenar", "/api/incidentes/eliminar"):
            if not self._can_act():
                return
            return self._incidente_accion(path.rsplit("/", 1)[1])
        if path in ("/api/memory/delete", "/api/memory/restore"):
            if not self._can_act():
                return
            return self._memory_delete(path.rsplit("/", 1)[1])
        if path == "/api/memory/vote":
            if not self._can_act():
                return
            return self._memory_vote()
        if path == "/api/memory/quorum":
            if not self._require_admin():
                return
            return self._memory_quorum()
        if path == "/api/herramientas/accion":
            if not self._can_act():
                return
            return self._herramienta_accion()
        if path == "/api/acciones/proponer":
            if not self._can_act():
                return
            return self._accion_proponer()
        if path == "/api/acciones/votar":
            if not self._can_act():
                return
            return self._accion_votar()
        if path == "/api/inventario/manifiesto":
            if not self._can_act():
                return
            return self._send(200, _inventario_manifesto())
        return self._send(405, {"ok": False, "error":
            "dashboard read-only: las mutaciones van por la fachada gobernada (monitor)"})

    def _incidente_accion(self, accion):
        # PRODUCTION: the mutation goes through the MONITOR (single writer), via socket. The
        # dashboard is read-only over the store; it never writes incidents directly. `accion` ∈
        # {rehabilitar (direct), parchar (remove threat and rehabilitate), eliminar (terminal)}.
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            iid = body.get("id")
            if not isinstance(iid, str) or not iid:
                return self._send(400, {"ok": False, "error": "id requerido"})
            if accion == "rehabilitar":
                r = _rehabilitar_directo_via_monitor(iid)
            elif accion == "parchar":
                r = _parchar_rehabilitar_via_monitor(iid, body.get("mitigacion", "mitigado por humano"))
            elif accion == "cuarentenar":
                r = _incidente_op_via_monitor({"tipo": "cuarentenar_incidente", "id": iid,
                                                "motivo": body.get("motivo", "cuarentena manual")})
            elif accion == "eliminar":
                r = _eliminar_via_monitor(iid, body.get("motivo", "eliminado por humano"))
            else:
                return self._send(400, {"ok": False, "error": "acción desconocida"})
            return self._send(200 if r.get("ok") else 400, r)
        except Exception as e:
            return self._send(400, {"ok": False, "error": str(e)})

    def _memory_delete(self, accion):
        # HUMAN governance ACTION over MEMORY. "delete" PROPOSES the multi-signature deletion (does
        # not execute until quorum); "restore" removes the denylist. Goes over the MONITOR (single
        # writer) via socket. The author is the authenticated user; the MOTIVO is mandatory to
        # propose deletion (it remains immutable in the hash chain).
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            key = body.get("key")
            if not isinstance(key, str) or not key:
                return self._send(400, {"ok": False, "error": "key requerida"})
            motivo = body.get("motivo", "")
            categoria = body.get("categoria", "otro")
            autor = getattr(self, "_user", {}).get("username", "desconocido")
            if accion == "delete":
                if not isinstance(motivo, str) or not motivo.strip():
                    return self._send(400, {"ok": False, "error": "motivo requerido (por qué se borra)"})
                r = _proponer_revocacion_via_monitor(key, autor, motivo, categoria)
            else:
                r = _restaurar_hecho_via_monitor(key, autor, motivo)
            return self._send(200 if r.get("ok") else 400, r)
        except Exception as e:
            return self._send(400, {"ok": False, "error": str(e)})

    def _memory_vote(self):
        # STEP 2 of multi-signature deletion: a validator DIFFERENT from the proposer approves. Goes
        # over the MONITOR via socket. The author is the authenticated user (never self-approves).
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            key = body.get("key")
            if not isinstance(key, str) or not key:
                return self._send(400, {"ok": False, "error": "key requerida"})
            autor = getattr(self, "_user", {}).get("username", "desconocido")
            r = _votar_revocacion_via_monitor(key, autor)
            return self._send(200 if r.get("ok") else 400, r)
        except Exception as e:
            return self._send(400, {"ok": False, "error": str(e)})

    def _memory_quorum(self):
        # ADMIN: configures how many distinct validators are required to execute a deletion. Goes
        # over the MONITOR via socket (single writer). Audited in the hash chain.
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            q = body.get("quorum")
            autor = getattr(self, "_user", {}).get("username", "desconocido")
            r = _set_quorum_revocacion_via_monitor(q, autor)
            return self._send(200 if r.get("ok") else 400, r)
        except Exception as e:
            return self._send(400, {"ok": False, "error": str(e)})

    def _herramienta_accion(self):
        # HUMAN tool-governance ACTION (approve installation/cabling, mark installed, reject). Goes
        # over the MONITOR (single writer) via socket. The agent does not trigger it: only the human
        # operates the dashboard.
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            tool_id = body.get("tool_id")
            accion = body.get("accion")
            if not isinstance(tool_id, str) or not tool_id:
                return self._send(400, {"ok": False, "error": "tool_id requerido"})
            mapa = {
                "aprobar_instalacion": ("aprobar_instalacion_herramienta", {}),
                "escanear_instalar": ("escanear_instalar_herramienta",
                                      {"path": body.get("path", ""),
                                       "origen": body.get("origen", "repo_privado")}),
                "marcar_instalada": ("marcar_instalada_herramienta",
                                     {"origen": body.get("origen", "repo_privado"),
                                      "hash_scan": body.get("hash_scan", "")}),
                "aprobar_cableado": ("aprobar_cableado_herramienta", {}),
                "rechazar": ("rechazar_herramienta", {"motivo": body.get("motivo", "")}),
            }
            if accion not in mapa:
                return self._send(400, {"ok": False, "error": f"acción desconocida: {accion}"})
            tipo_op, extra = mapa[accion]
            r = _herramienta_accion_via_monitor(tool_id, tipo_op, extra)
            return self._send(200 if r.get("ok") else 400, r)
        except Exception as e:
            return self._send(400, {"ok": False, "error": str(e)})

    def _accion_proponer(self):
        # SECURITY ACTION with quorum (de-quarantine/delete/patch incident, approve tool
        # installation/cabling). If there is ONE single user who can act, it executes DIRECTLY
        # (audited) because there is no co-validator; otherwise it PROPOSES and waits for N distinct
        # validators (Ed25519 multi-signature). The dashboard never executes: it goes to the MONITOR.
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            tipo_accion = body.get("tipo_accion")
            objetivo = body.get("objetivo")
            motivo = body.get("motivo", "")
            payload = body.get("payload") or {}
            if not isinstance(tipo_accion, str) or not tipo_accion:
                return self._send(400, {"ok": False, "error": "tipo_accion requerido"})
            if not isinstance(objetivo, str) or not objetivo:
                return self._send(400, {"ok": False, "error": "objetivo requerido"})
            autor = getattr(self, "_user", {}).get("username", "desconocido")
            if _n_usuarios_activos() <= 1:
                r = _accion_directa_via_monitor(tipo_accion, objetivo, autor, payload)
                r["directa"] = True
                return self._send(200 if r.get("ok") else 400, r)
            if not isinstance(motivo, str) or not motivo.strip():
                return self._send(400, {"ok": False, "error": "motivo requerido (por qué se ejecuta)"})
            r = _proponer_accion_gobernada_via_monitor(tipo_accion, objetivo, autor, motivo, payload)
            return self._send(200 if r.get("ok") else 400, r)
        except Exception as e:
            return self._send(400, {"ok": False, "error": str(e)})

    def _accion_votar(self):
        # STEP 2: SIGNED vote from a validator DIFFERENT from the proposer. At quorum, the MONITOR
        # executes the domain (rehabilitate/delete/cable...) and marks it executed.
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            hash_propuesta = body.get("hash_propuesta")
            if not isinstance(hash_propuesta, str) or not hash_propuesta:
                return self._send(400, {"ok": False, "error": "hash_propuesta requerido"})
            autor = getattr(self, "_user", {}).get("username", "desconocido")
            r = _votar_accion_gobernada_via_monitor(hash_propuesta, autor)
            return self._send(200 if r.get("ok") else 400, r)
        except Exception as e:
            return self._send(400, {"ok": False, "error": str(e)})


def main():
    global TLS
    admin, init_pw = auth.init()
    if admin:
        if init_pw:
            print(f"🔐 admin inicial creado: '{admin}' · contraseña: {init_pw}")
            print("   (cámbiala tras el primer login; es la única vez que se muestra)")
        else:
            print(f"🔐 admin inicial creado: '{admin}' (contraseña por HCMA_DASH_ADMIN_PASSWORD)")
    ctx = _ssl_context()
    TLS = ctx is not None
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    if ctx:
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    scheme = "https" if TLS else "http"
    print(f"📊 Dashboard HCMA → {scheme}://localhost:{PORT}")
    print(f"   telemetry: {TELEMETRY_DB}")
    print(f"   memory:    {MEMORY_DB or '(no configurado — set HCMA_DASH_MEMORY_DB)'}")
    print(f"   auth db:   {auth.AUTH_DB}")
    # Durable write-back queue: the pool drains (and retries) the facts users tell in the chat, even
    # if the dashboard restarts mid-write.
    try:
        import writeback_queue as WB
        WB.start_worker()
    except Exception as e:
        print(f"⚠️ writeback worker no arrancó: {e}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n detenido")


if __name__ == "__main__":
    main()
