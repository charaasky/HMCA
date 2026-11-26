#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""auth.py — authentication and RBAC of the HCMA dashboard (stdlib only).

Roles:
  - `admin`   : superuser. Everything, including user ABM.
  - `usuario` : can run operational actions (rehabilitate, cable, manifesto).
  - `visor`   : read-only. Audits; does NOT run actions (unlock/authorization/manifesto).

Security:
  - Passwords with PBKDF2-HMAC-SHA256 (200k iterations) + per-user salt.
  - 2FA TOTP (RFC 6238, HMAC-SHA1, base32), forced on first login.
  - In-memory sessions (random HttpOnly token) with expiration.
  - Per-user IP allowlist (specific machines, with CIDR support).

Store: SQLite `auth.db` (not the HCMA memory; it is the dashboard's user base).
"""
import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import sqlite3
import struct
import time

AUTH_DB = os.environ.get(
    "HCMA_DASH_AUTH_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.db"))
SESSION_TTL = int(os.environ.get("HCMA_DASH_SESSION_TTL", "43200"))  # 12 h

# Anti-brute-force: after MAX_LOGIN_ATTEMPTS failed (password) within LOGIN_LOCKOUT_SECONDS,
# the account is locked. Every attempt is recorded in `login_attempts` for analysis.
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 86400  # 1 day

ROLES = ("admin", "usuario", "visor")
_ISS = "HCMA"

# token -> {"username": str, "expires": float}
_SESSIONS = {}


def _db():
    c = sqlite3.connect(AUTH_DB)
    c.row_factory = sqlite3.Row
    return c


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── crypto (passwords) ───────────────────────────────────────────────────────
def _hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                            bytes.fromhex(salt), 200_000)
    return h.hex(), salt


def _verify_password(password, salt, stored_hash):
    h, _ = _hash_password(password, salt)
    return hmac.compare_digest(h, stored_hash)


# ── TOTP (RFC 6238) ───────────────────────────────────────────────────────────
def _totp(secret_b32, counter):
    pad = "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    o = digest[-1] & 0x0f
    code = (struct.unpack(">I", digest[o:o + 4])[0] & 0x7fffffff) % 1_000_000
    return f"{code:06d}"


def new_totp_secret():
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def totp_uri(secret, username):
    return (f"otpauth://totp/{_ISS}:{username}?secret={secret}&issuer={_ISS}"
            f"&algorithm=SHA1&digits=6&period=30")


def verify_totp(secret, code, window=1):
    code = str(code or "").strip()
    if not secret or len(code) != 6 or not code.isdigit():
        return False
    now = int(time.time()) // 30
    for i in range(-window, window + 1):
        if hmac.compare_digest(_totp(secret, now + i), code):
            return True
    return False


# ── store / users ──────────────────────────────────────────────────────────
def init():
    """Creates the schema and, if there are no users, the initial admin.

    Returns (username, initial_password) or (None, None). The initial password is
    `HCMA_DASH_ADMIN_PASSWORD` or a random one (must be shown only ONCE)."""
    c = _db()
    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username      TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                salt          TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'visor',
                totp_secret   TEXT,
                totp_enabled  INTEGER NOT NULL DEFAULT 0,
                enabled       INTEGER NOT NULL DEFAULT 1,
                allowed_ips   TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL,
                last_login    TEXT,
                session_ttl   INTEGER,
                must_change_password INTEGER NOT NULL DEFAULT 1
            )
        """)
        c.commit()
        # migration: if the table already existed without session_ttl, add the column (per-user TTL)
        cols = [r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        if "session_ttl" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN session_ttl INTEGER")
            c.commit()
        # mandatory password change on first login (default 1 = pending)
        if "must_change_password" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1")
            c.commit()
        # migration (P0 A5): PER-USER Ed25519 key for real multi-signature votes.
        # The private key lives here (server-side, next to the TOTP secret) and is only used by the
        # dashboard to SIGN the vote of THAT authenticated user. The monitor verifies against the
        # public key registered in memory (governed fact).
        if "ed25519_priv_pem" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN ed25519_priv_pem TEXT")
        if "ed25519_pub_hex" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN ed25519_pub_hex TEXT")
        c.commit()
        # login attempt log (for analysis + brute-force lockout)
        c.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      REAL NOT NULL,
                username TEXT NOT NULL,
                ip      TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL CHECK(success IN (0,1)),
                event   TEXT NOT NULL DEFAULT ''
            )
        """)
        c.commit()
        if not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            env_pw = os.environ.get("HCMA_DASH_ADMIN_PASSWORD")
            pw = env_pw or secrets.token_urlsafe(12)
            _create_user(c, "admin", pw, "admin")
            c.commit()
            return "admin", pw if not env_pw else None
        return None, None
    finally:
        c.close()


def _create_user(c, username, password, role="visor", allowed_ips="", session_ttl=None):
    if role not in ROLES:
        raise ValueError(f"rol inválido: {role}")
    ph, salt = _hash_password(password)
    c.execute(
        "INSERT INTO users(username,password_hash,salt,role,totp_secret,totp_enabled,"
        "enabled,allowed_ips,created_at,session_ttl) VALUES(?,?,?,?,?,0,1,?,?,?)",
        (username, ph, salt, role, None, allowed_ips, _now(), session_ttl))


def user(username):
    c = _db()
    try:
        r = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def authenticate(username, password):
    u = user(username)
    if not u or not u["enabled"]:
        return None
    if not _verify_password(password, u["salt"], u["password_hash"]):
        return None
    return u


def record_attempt(username, ip="", success=False, event=""):
    """Records a login attempt (success/failure) for analysis and brute-force lockout."""
    c = _db()
    try:
        c.execute("INSERT INTO login_attempts(ts,username,ip,success,event) VALUES(?,?,?,?,?)",
                  (time.time(), username or "", ip or "", 1 if success else 0, event or ""))
        c.commit()
    finally:
        c.close()


def failed_count(username, window=LOGIN_LOCKOUT_SECONDS):
    """Failed attempts (password) within the lockout window."""
    c = _db()
    try:
        cutoff = time.time() - window
        row = c.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE username=? AND success=0 AND ts>=?",
            (username, cutoff)).fetchone()
        return row[0]
    finally:
        c.close()


def is_locked(username):
    """True if the account is locked due to excessive failed attempts (1-day window)."""
    return failed_count(username) >= MAX_LOGIN_ATTEMPTS


def login_attempts(username=None, limit=200):
    """Login attempt log (for later analysis; optionally filter by user)."""
    c = _db()
    try:
        if username:
            rows = c.execute(
                "SELECT * FROM login_attempts WHERE username=? ORDER BY id DESC LIMIT ?",
                (username, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM login_attempts ORDER BY id DESC LIMIT ?",
                             (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def list_users():
    c = _db()
    try:
        rows = c.execute(
            "SELECT username,role,enabled,totp_enabled,allowed_ips,created_at,last_login,session_ttl "
            "FROM users ORDER BY username").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def create_user(username, password, role="visor", allowed_ips="", session_ttl=None):
    username = (username or "").strip()
    if not username or len(password or "") < 8:
        raise ValueError("usuario no vacío y contraseña >= 8 caracteres")
    c = _db()
    try:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("el usuario ya existe")
        _create_user(c, username, password, role, allowed_ips, session_ttl)
        c.commit()
    finally:
        c.close()


def update_user(username, role=None, enabled=None, allowed_ips=None, session_ttl=None):
    c = _db()
    try:
        cur = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not cur:
            raise ValueError("usuario no existe")
        role = cur["role"] if role is None else role
        if role not in ROLES:
            raise ValueError(f"rol inválido: {role}")
        enabled = cur["enabled"] if enabled is None else (1 if enabled else 0)
        allowed_ips = cur["allowed_ips"] if allowed_ips is None else (allowed_ips or "")
        if session_ttl is None:
            session_ttl = cur["session_ttl"]
        else:
            session_ttl = int(session_ttl)
            if session_ttl <= 0:
                session_ttl = None  # 0/negative => NULL => use global default
        c.execute("UPDATE users SET role=?, enabled=?, allowed_ips=?, session_ttl=? WHERE username=?",
                  (role, enabled, allowed_ips, session_ttl, username))
        c.commit()
    finally:
        c.close()


def set_password(username, password):
    if len(password or "") < 8:
        raise ValueError("contraseña >= 8 caracteres")
    ph, salt = _hash_password(password)
    c = _db()
    try:
        cur = c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
        if not cur:
            raise ValueError("usuario no existe")
        c.execute("UPDATE users SET password_hash=?, salt=? WHERE username=?",
                  (ph, salt, username))
        c.commit()
    finally:
        c.close()


def change_own_password(username, old_password, new_password):
    """Changes the user's own password (verifies the current one) and clears the
    must_change_password flag. Returns True or raises ValueError."""
    if len(new_password or "") < 8:
        raise ValueError("contraseña nueva >= 8 caracteres")
    u = user(username)
    if not u:
        raise ValueError("usuario no existe")
    if not _verify_password(old_password, u["salt"], u["password_hash"]):
        raise ValueError("contraseña actual incorrecta")
    ph, salt = _hash_password(new_password)
    c = _db()
    try:
        c.execute("UPDATE users SET password_hash=?, salt=?, must_change_password=0 WHERE username=?",
                  (ph, salt, username))
        c.commit()
    finally:
        c.close()
    return True


def reset_admin_password(password=None):
    """(Re)generates the admin password and leaves it in FIRST-login state: password to rotate
    (must_change_password=1) and 2FA NOT enabled (totp_secret/totp_enabled cleared). Thus the next
    login starts the 2FA setup with QR, instead of asking for a code from an old secret. Returns
    (username, password)."""
    username = "admin"
    pw = password or secrets.token_urlsafe(12)
    if user(username):
        ph, salt = _hash_password(pw)
        c = _db()
        try:
            c.execute(
                "UPDATE users SET password_hash=?, salt=?, must_change_password=1, "
                "totp_secret=NULL, totp_enabled=0 WHERE username=?",
                (ph, salt, username))
            c.commit()
        finally:
            c.close()
    else:
        c = _db()
        try:
            _create_user(c, username, pw, "admin")
            c.commit()
        finally:
            c.close()
    return username, pw


def delete_user(username, actor):
    if username == actor:
        raise ValueError("no podés eliminar tu propio usuario")
    c = _db()
    try:
        n_admin = c.execute("SELECT COUNT(*) n FROM users WHERE role='admin' AND enabled=1").fetchone()["n"]
        target = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not target:
            raise ValueError("usuario no existe")
        if target["role"] == "admin" and n_admin <= 1:
            raise ValueError("no se puede eliminar el último admin activo")
        c.execute("DELETE FROM users WHERE username=?", (username,))
        c.commit()
    finally:
        c.close()


def ensure_totp_secret(username):
    """Returns the user's TOTP secret, generating it if it does not exist (left pending
    activation: totp_enabled=0)."""
    c = _db()
    try:
        cur = c.execute("SELECT totp_secret FROM users WHERE username=?", (username,)).fetchone()
        if not cur:
            raise ValueError("usuario no existe")
        secret = cur["totp_secret"]
        if not secret:
            secret = new_totp_secret()
            c.execute("UPDATE users SET totp_secret=? WHERE username=?", (secret, username))
            c.commit()
        return secret
    finally:
        c.close()


def enable_totp(username, secret):
    c = _db()
    try:
        c.execute("UPDATE users SET totp_secret=?, totp_enabled=1 WHERE username=?",
                  (secret, username))
        c.commit()
    finally:
        c.close()


def reset_totp(username):
    """Regenerates the TOTP secret (single-view QR access). Returns the secret and leaves
    totp_enabled=0 so the user reactivates it on their next login."""
    secret = new_totp_secret()
    c = _db()
    try:
        if not c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("usuario no existe")
        c.execute("UPDATE users SET totp_secret=?, totp_enabled=0 WHERE username=?",
                  (secret, username))
        c.commit()
    finally:
        c.close()
    return secret


# ── per-user Ed25519 keys (P0 A5, real multi-signature) ─────────────────────
# Each dashboard user has an Ed25519 pair. The private key is stored here (next to the TOTP
# secret) and is only used by the dashboard to SIGN the vote of THAT authenticated user. The
# public key is registered in HCMA MEMORY (governed fact `pubkey:<usuario>`) so the monitor
# verifies the signature. Thus the deletion vote is "real multi-signature", not "multi-row".
def ensure_ed25519(username):
    """Ensures `username` has an Ed25519 pair (generates it the first time). Returns the public key
    in hex. The private key remains persisted in auth.db (server-side, like the TOTP secret)."""
    c = _db()
    try:
        cur = c.execute("SELECT ed25519_priv_pem, ed25519_pub_hex FROM users WHERE username=?",
                        (username,)).fetchone()
        if not cur:
            raise ValueError("usuario no existe")
        if cur["ed25519_priv_pem"] and cur["ed25519_pub_hex"]:
            return cur["ed25519_pub_hex"]
        import firmas_multifirma as FM
        priv_pem, pub_hex = FM.generar_par()
        c.execute("UPDATE users SET ed25519_priv_pem=?, ed25519_pub_hex=? WHERE username=?",
                  (priv_pem.decode("ascii"), pub_hex, username))
        c.commit()
        return pub_hex
    finally:
        c.close()


def pubkey_hex_for(username):
    """Ed25519 public key of `username` (generating it if missing). None if the user does not exist."""
    try:
        return ensure_ed25519(username)
    except ValueError:
        return None


def sign_voto(username, dominio, hash_propuesta, ts_exp):
    """Signs a vote (domain + hash_propuesta + author + expiration) with the user's private key.
    Returns the hex signature. Raises if the user has no key (should not happen after ensure)."""
    c = _db()
    try:
        cur = c.execute("SELECT ed25519_priv_pem FROM users WHERE username=?",
                        (username,)).fetchone()
        if not cur or not cur["ed25519_priv_pem"]:
            raise ValueError(f"usuario '{username}' sin clave Ed25519")
        priv_pem = cur["ed25519_priv_pem"].encode("ascii")
    finally:
        c.close()
    import firmas_multifirma as FM
    return FM.firmar_voto(dominio, hash_propuesta, username, ts_exp, priv_pem)


def touch_login(username):
    c = _db()
    try:
        c.execute("UPDATE users SET last_login=? WHERE username=?", (_now(), username))
        c.commit()
    finally:
        c.close()


# ── sessions (in memory) ─────────────────────────────────────────────────────
def session_ttl_for(username):
    """Effective session TTL for a user (their `session_ttl` or the global default)."""
    u = user(username)
    if u and u.get("session_ttl"):
        try:
            return max(60, int(u["session_ttl"]))
        except (ValueError, TypeError):
            pass
    return SESSION_TTL


def create_session(username):
    ttl = session_ttl_for(username)
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = {"username": username, "expires": time.time() + ttl, "ttl": ttl}
    _gc_sessions()
    return token


def get_session(token):
    s = _SESSIONS.get(token)
    if not s:
        return None
    if s["expires"] < time.time():
        _SESSIONS.pop(token, None)
        return None
    return s


def destroy_session(token):
    _SESSIONS.pop(token, None)


def _gc_sessions():
    now = time.time()
    for t in [t for t, s in _SESSIONS.items() if s["expires"] < now]:
        _SESSIONS.pop(t, None)


# ── IP allowlist ──────────────────────────────────────────────────────────
def check_ip(u, ip):
    """True if `ip` is allowed for `u`. Empty `allowed_ips` = allow all."""
    al = (u.get("allowed_ips") or "").strip()
    if not al:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in al.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            net = ipaddress.ip_network(entry, strict=False)
            if net.version == addr.version and addr in net:
                return True
        except ValueError:
            if entry == ip:
                return True
    return False


def can_act(role):
    return role in ("admin", "usuario")


def can_admin(role):
    return role == "admin"
