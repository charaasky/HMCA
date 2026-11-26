#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""llm_config.py — LLM connection config (a human sets it from the dashboard).

The API key is stored ENCRYPTED (AES-256-GCM, authenticated) with a separate MASTER KEY.
The config file is 0600 and the master key lives in `~/.keys/llm_config.key` (0600, outside the
repo), so leaking the `.json` without the master key does not expose the token.

Honest threat model: the master key lives on the same machine (dashboard user). This protects
against accidental exposure (backups, logs, copies, casual reads, leaking the config alone) and
gives strong encryption at rest; it does NOT protect against an attacker who already controls the
account that owns the master key (they could read it and decrypt).

`masked()` NEVER returns the key in the clear: only `api_key_set` and `api_key_masked` (last 4).
The clear key is only returned by `api_key()` to whoever runs the LLM (prototype), never to the UI.
"""
import base64
import json
import os
import secrets
import urllib.parse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("HCMA_LLM_CONFIG", os.path.join(HERE, "llm_config.json"))
KEY_PATH = os.environ.get(
    "HCMA_LLM_KEY",
    os.path.join(os.path.expanduser("~"), ".keys", "llm_config.key"))

DEFAULTS = {
    "provider": "openrouter",
    "base_url": "https://openrouter.ai/api/v1",
    "model": "openai/gpt-4o-mini",
    "model_type": "text",
}

# Fields editable from the panel (whitelist; ignores any other key).
PLAIN_FIELDS = ("provider", "base_url", "model", "model_type")
FIELDS = PLAIN_FIELDS + ("api_key",)

# Fixed AAD: binds the ciphertext to this context (not reusable elsewhere).
_AAD = b"hcma-llm-config:v1"


def _master_key():
    """Returns the master key (32 bytes), creating it if missing (0600)."""
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            k = f.read()
        if len(k) == 32:
            return k
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    k = secrets.token_bytes(32)
    fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(k)
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass
    return k


def _encrypt(plaintext):
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_master_key()).encrypt(nonce, plaintext.encode("utf-8"), _AAD)
    return base64.b64encode(nonce + ct).decode("ascii")


def _decrypt(enc):
    raw = base64.b64decode(enc)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(_master_key()).decrypt(nonce, ct, _AAD).decode("utf-8")


def _read_raw():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def _secret(raw, enc_field, plain_field):
    """Returns a secret in the clear from the file (encrypted, or legacy plaintext)."""
    enc = raw.get(enc_field) or ""
    if enc:
        try:
            return _decrypt(enc)
        except Exception:
            return ""  # fail-closed: a corrupted secret is never exposed
    return raw.get(plain_field) or ""


def _api_keys_enc_map(raw):
    """{provider: enc} dict of per-provider API keys, migrating the legacy single key.

    The legacy key (`api_key_enc`/`api_key`) is associated with the provider that was active in
    the file at that time (best available signal; if the user changed provider with the old key,
    that association is corrected by re-entering the key once per provider)."""
    s = raw.get("api_keys_enc") or ""
    if s:
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                return {str(k): v for k, v in d.items() if isinstance(v, str) and v}
        except Exception:
            pass
    enc = raw.get("api_key_enc") or ""
    if not enc and raw.get("api_key"):
        enc = _encrypt(str(raw["api_key"]))
    if enc:
        prov = raw.get("provider") or DEFAULTS["provider"]
        return {prov: enc}
    return {}


def _api_keys_clear(raw):
    """{provider: clear_key} dict of per-provider API keys (corrupt ones are dropped fail-closed)."""
    out = {}
    for prov, enc in _api_keys_enc_map(raw).items():
        try:
            out[prov] = _decrypt(enc)
        except Exception:
            continue
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


def _api_keys_origin_map(raw):
    """{provider: origin} dict stored alongside each key (for the per-provider anti-leak rule)."""
    s = raw.get("api_keys_origin") or ""
    if s:
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                return {str(k): str(v) for k, v in d.items() if v}
        except Exception:
            pass
    return {}


def api_key_origin_for(provider):
    """Origin stored for ONE provider's key, or '' (old keys without origin)."""
    return _api_keys_origin_map(_read_raw()).get(str(provider or "")) or ""


def load():
    """Effective config with secrets IN THE CLEAR (internal use only, never for the UI)."""
    d = dict(DEFAULTS)
    raw = _read_raw()
    for k in PLAIN_FIELDS:
        if k in raw:
            d[k] = raw[k]
    keys = _api_keys_clear(raw)
    # active provider's key; legacy fallback if the active provider has no key in the map.
    d["api_key"] = keys.get(d["provider"]) or _secret(raw, "api_key_enc", "api_key")
    d["api_keys"] = keys
    d["gateway_key"] = _secret(raw, "gateway_key_enc", "gateway_key")
    return d


def _store_secret(out, data, cur, raw, field, enc_field):
    new = data.get(field)
    if new and not str(new).startswith("••••"):
        out[enc_field] = _encrypt(new)
    elif raw.get(enc_field):
        out[enc_field] = raw[enc_field]
    elif cur.get(field):
        out[enc_field] = _encrypt(cur[field])  # migrate legacy plaintext


def _atomic_write(out):
    """Writes the config dict atomically (tmp + fsync + os.replace) and 0600."""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def save(data):
    """Saves the config with encrypted secrets (atomic + 0600).

    The API key is stored PER PROVIDER (`api_keys_enc` map): saving with a new key associates it
    with the active provider; other providers keep theirs. `api_key: null` DELETES the active
    provider's key (distinct from absent/empty/placeholder, which preserves it)."""
    cur = load()
    raw = _read_raw()
    out = {k: (data.get(k) if k in data else cur.get(k, DEFAULTS[k])) for k in PLAIN_FIELDS}
    prov = out.get("provider") or DEFAULTS["provider"]
    keys_enc = _api_keys_enc_map(raw)
    keys_origin = _api_keys_origin_map(raw)
    if "api_key" in data:
        new = data["api_key"]
        if new is None:
            keys_enc.pop(prov, None)  # explicit deletion
            keys_origin.pop(prov, None)
        elif not str(new).startswith("••••"):
            keys_enc[prov] = _encrypt(str(new))
            keys_origin[prov] = _origin(out.get("base_url") or "")
    out["api_keys_enc"] = json.dumps(keys_enc, sort_keys=True)
    out["api_keys_origin"] = json.dumps(keys_origin, sort_keys=True)
    # keep the active provider's key in the legacy field for compatibility with old readers
    if prov in keys_enc:
        out["api_key_enc"] = keys_enc[prov]
    _store_secret(out, data, cur, raw, "gateway_key", "gateway_key_enc")
    _atomic_write(out)
    return masked()


def delete_api_key(provider):
    """Deletes ONE provider's API key WITHOUT changing the active provider. Returns True if present.

    Fable (fable_llm_key_perdida): deletion must not go through `save` because `save` would also
    change the active provider. Here only the `api_keys_enc` map is edited."""
    provider = str(provider or "")
    if not provider:
        return False
    raw = _read_raw()
    keys_enc = _api_keys_enc_map(raw)
    if provider not in keys_enc:
        return False
    keys_enc.pop(provider, None)
    keys_origin = _api_keys_origin_map(raw)
    keys_origin.pop(provider, None)
    out = dict(raw)
    out["api_keys_enc"] = json.dumps(keys_enc, sort_keys=True)
    out["api_keys_origin"] = json.dumps(keys_origin, sort_keys=True)
    activo = out.get("provider") or DEFAULTS["provider"]
    if activo == provider:
        out.pop("api_key_enc", None)
    elif activo in keys_enc:
        out["api_key_enc"] = keys_enc[activo]
    _atomic_write(out)
    return True


def masked():
    """UI view: never exposes secrets in the clear. Includes `api_keys` (per provider) with
    {set, masked} so the panel shows the selected provider's key."""
    c = load()
    keys = c.pop("api_keys", {}) or {}
    key = c.pop("api_key", "") or ""
    gk = c.pop("gateway_key", "") or ""
    c["api_key_set"] = bool(key)
    c["api_key_masked"] = ("••••" + key[-4:]) if key else ""
    c["api_keys"] = {p: {"set": bool(k), "masked": ("••••" + k[-4:]) if k else ""}
                     for p, k in sorted(keys.items())}
    c["gateway_key_set"] = bool(gk)
    c["gateway_key_masked"] = ("••••" + gk[-4:]) if gk else ""
    return c


def api_key():
    """Returns the ACTIVE provider's API key in the clear (for prototype/orchestrator), or ''."""
    return load().get("api_key") or ""


def api_key_for(provider):
    """Returns the API key in the clear for a given provider, or '' (for the connection test)."""
    return (load().get("api_keys") or {}).get(provider) or ""


def gateway_key():
    """Returns the gateway key (auto-generating it if missing). Authenticates the gateway."""
    gk = load().get("gateway_key") or ""
    if gk:
        return gk
    gk = secrets.token_urlsafe(32)
    save({"gateway_key": gk})
    return gk


def regenerate_gateway_key():
    """Regenerates the gateway key and returns the NEW one in the clear (shown ONCE)."""
    gk = secrets.token_urlsafe(32)
    save({"gateway_key": gk})
    return gk


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--masked":
        print(json.dumps(masked(), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(load(), indent=2, ensure_ascii=False))
