#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""firma_asimetrica.py — Ed25519 signature to close the 'ONLY human authorizes' channel (Fable P0-a).

PROBLEM it solves (Fable 5.2 ruling): symmetric HMAC does not guarantee 'human only', because the
same key that VERIFIES can also SIGN. Any code inside the trust boundary (the dispatcher, the
LLM-generated impl, a tool) that reaches `_clave_humana()` can self-consent. The realistic attacker
does not guess the signature: they CALCULATE it.

SOLUTION: asymmetry. The agent process (runtime/verifier) only has the PUBLIC KEY; with it it can
VERIFY but can NOT SIGN. The PRIVATE KEY lives OUTSIDE the repo and outside the agent process (a
human file), only touched by a separate CLI that the human runs.

  human (CLI `firmar`)    -> private key ~/.config/hcma/humano_ed25519.key
  runtime (verifier)      -> public key ~/.config/hcma/humano_ed25519.pub

File names (outside the repo, same folder as the HMAC key):
  ~/.config/hcma/humano_ed25519.key   (private, mode 600, only the human)
  ~/.config/hcma/humano_ed25519.pub   (public, readable by the runtime)

Domain separation: each signature includes a canonical context LABEL (why it is signed), so that a
'manifiesto' signature does not serve as an 'autorizacion' and vice versa. The signed payload is a
stable JSON serialization (sort_keys, allow_nan=False, no silent default=str).

Does NOT touch `procedencia.py` (HMAC of the manifesto/objective). This layer is ONLY for the
authorization of ACTIONS (deontic), where the self-consent risk is higher.
"""
import json
import os
import sys
import hashlib

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey, Ed25519PublicKey)
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.exceptions import InvalidSignature  # noqa: E402


class FirmaInvalida(ValueError):
    """The signature does not verify against the public key, or the domain/payload changed."""


# The linter does not resolve the type returned by load_pem_private_key (union of all keys).
# We narrow explicitly to the Ed25519 private key that DOES have .sign().
def _cargar_privada_ed25519(privada_bytes: bytes) -> Ed25519PrivateKey:
    k = serialization.load_pem_private_key(privada_bytes, password=None)
    if not isinstance(k, Ed25519PrivateKey):
        raise FirmaInvalida("la clave privada no es Ed25519")
    return k

DIR_CFG = os.path.expanduser("~/.config/hcma")
PATH_PRIV = os.path.join(DIR_CFG, "humano_ed25519.key")
PATH_PUB = os.path.join(DIR_CFG, "humano_ed25519.pub")

# UID BOUNDARY (Fable P0): the PRIVATE key lives in the home of a SEPARATE signer user
# (`hcma-signer`), not this user. If the runtime/agent tries to read the private key on its own,
# the filesystem denies access (EACCES), not the code. `firmante_uid` resolves the paths by role.
# It is imported lazily to avoid coupling at import.
def _rutas_frontera():
    """Returns (private_path, public_path) according to firma_asimetrica or the UID boundary.

    In production it ALWAYS uses `firmante_uid.ruta_privada()`/`ruta_publica()`. Keeps
    compatibility with the env override (tests)."""
    if os.environ.get("HCMA_SIGNER_TEST_DIR") or os.environ.get("HCMA_SIGNER_USER"):
        import firmante_uid as FU
        return FU.ruta_privada(), FU.ruta_publica()
    # no UID boundary configured (dev/historical tests mode): local paths
    return PATH_PRIV, PATH_PUB

# Valid domain labels (what is being signed). Prevents cross-use of signatures.
DOMINIOS = ("autorizacion", "manifiesto", "objetivo")

# The runtime NEVER has any reason to import the private key. If someone tries to sign from the
# runtime, it fails with FileNotFoundError (the private key is not in its scope). In testing, path
# override is allowed ONLY via test environment variables, never in production.


def _leer_clave(path):
    if not os.path.exists(path):
        raise FirmaInvalida(f"no existe {path}")
    with open(path, "rb") as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# TRUST ANCHOR (Fable P0 5th revision, item 4): 'verify the signature when reading,
# with the anchor outside the agent's write scope'.
# ─────────────────────────────────────────────────────────────────────────────
# The agent could replace the PUBLIC key (readable .pub file or the env var) to make its own
# signatures verify. Closure: PIN of the public key. The real public key has a known sha256 hash
# (fixed at deployment, NOT editable by the agent). If the public key read does not match the pin,
# it is REJECTED (the anchor was replaced).
#
# The pin is set by (in priority order):
#   1. root-owned anchor file `/etc/hcma/publica.pin` (OUTSIDE the agent's scope),
#   2. deployment variable HCMA_PUBLICA_PIN,
#   3. empty => dev mode (no anchor; accepts any public key).
# In production the operator sets the pin so the agent can NOT write over it.
ANCLA_ARCHIVO = "/etc/hcma/publica.pin"


def _pin_desde_archivo():
    """Returns (pin, reason) reading the root-owned file. Fail-closed, anti-TOCTOU.

    Fable (6th + 7th revision): the pin is only trustworthy if the file AND its parent directory
    are root:root, regular file, without group/other write. And (7th) it must NOT follow symlinks,
    and must validate permissions on the already-open DESCRIPTOR (not on the path, to avoid TOCTOU).
    """
    import stat as _stat
    # verify parent directory
    dirpath = os.path.dirname(ANCLA_ARCHIVO)
    try:
        st_dir = os.stat(dirpath)
    except OSError as e:
        return None, f"directorio de ancla inaccesible: {e}"
    if st_dir.st_uid != 0 or st_dir.st_gid != 0:
        return None, "directorio de ancla no es root:root"
    if st_dir.st_mode & 0o022:
        return None, "directorio de ancla tiene escritura de grupo/otros"
    # open WITHOUT following symlinks (O_NOFOLLOW) and validate on the descriptor (anti-TOCTOU)
    import stat as _stat
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(ANCLA_ARCHIVO, flags)
    except OSError as e:
        return None, f"archivo de ancla inaccesible: {e}"
    try:
        st = os.fstat(fd)  # validates on the open descriptor, not the path
    except OSError as e:
        os.close(fd)
        return None, f"no se pudo stat del descriptor: {e}"
    if not _stat.S_ISREG(st.st_mode):
        os.close(fd)
        return None, "ancla no es archivo regular"
    if st.st_uid != 0 or st.st_gid != 0:
        os.close(fd)
        return None, "ancla no es root:root"
    if st.st_mode & 0o022:
        os.close(fd)
        return None, "ancla tiene escritura de grupo/otros"
    try:
        with os.fdopen(fd, "r") as f:
            v = f.read().strip().lower()
    except OSError as e:
        return None, f"ancla ilegible: {e}"
    if not v or len(v) != 64:
        return None, f"pin inválido ({len(v) if v else 0} chars, esperado 64)"
    return v, ""


def _pin_actual():
    """Trust pin: root-owned file (production) or env (test mode only).

    Fail-closed (6th revision): in production ONLY the root-owned file is valid. The env var
    HCMA_PUBLICA_PIN is accepted only in test mode (see ablacion.modo_prueba).
    """
    import ablacion as AB
    if AB.modo_prueba():
        env_pin = os.environ.get("HCMA_PUBLICA_PIN", "").strip().lower()
        if env_pin:
            return env_pin
    # production (or test mode without env): the root-owned file IS the authority
    pin, _motivo = _pin_desde_archivo()
    return pin or ""


def _hash_publica(pub_bytes):
    import hashlib
    return hashlib.sha256(pub_bytes).hexdigest()


def ancla_publica_valida(pub_bytes):
    """Does the public key read match the trust PIN? Fail-closed.

    Fable (6th revision): in production, if there is no valid pin (root-owned file missing or
    corrupt), no public key is accepted (False). Only in test mode without a pin is it allowed
    (dev). True only if the pin exists AND matches.
    """
    import ablacion as AB
    pin = _pin_actual()
    if not pin:
        # no pin: only allowed in test mode (dev); in production it is fail-closed (rejects)
        return AB.modo_prueba()
    return hashlib.sha256(pub_bytes).hexdigest() == pin


def clave_publica_bytes():
    """Public key (the only thing the runtime needs to verify), VALIDATED against the pin.

    Fable P0: if there is a pin and the public key read does not match, it is REJECTED (the anchor
    was replaced).

    FIXED BUG (D2): the FILE path returned the RAW ASCII hex (65 bytes) instead of the 32 binary
    bytes of the Ed25519 key. That broke (a) pin validation (it compared the hash of the hex
    string, not of the bytes) and (b) Ed25519PublicKey.from_public_bytes (expects 32 bytes). Now
    the .pub file (hex string + newline) is DECODED to binary bytes, same as the env var path.
    """
    pub = os.environ.get("HCMA_HUMANO_ED25519_PUB", "")
    if pub:
        pub_bytes = bytes.fromhex(pub)
    else:
        _priv, pub_path = _rutas_frontera()
        raw = _leer_clave(pub_path)
        # the .pub file stores the HEX of the key (ASCII string), not the binary bytes.
        # Decode: strip() removes the newline; bytes.fromhex converts hex -> bytes.
        try:
            pub_bytes = bytes.fromhex(raw.strip().decode("ascii"))
        except (ValueError, UnicodeDecodeError) as e:
            raise FirmaInvalida(f"pública en {pub_path} no es hex válido: {e}")
    if not ancla_publica_valida(pub_bytes):
        raise FirmaInvalida(
            "pública NO coincide con el pin de confianza (el ancla fue reemplazada): "
            "negándose a verificar (P0 Fable).")
    return pub_bytes


def cargar_publica():
    """Returns an Ed25519PublicKey object. Only verifies, does not sign."""
    return Ed25519PublicKey.from_public_bytes(clave_publica_bytes())


# ─────────────────────────────────────────────────────────────────────────────
# Generation (run by a human, ONCE: generates a pair and saves outside the repo)
# ─────────────────────────────────────────────────────────────────────────────
def generar_par(dirmeta=None):
    """Generates an Ed25519 pair. Returns (private_pem_bytes, public_hex). Does NOT write by
    itself: to be clear, writing outside the repo is done by the `generar_claves` CLI, not this
    function (prevents the agent process from creating keys and knowing them)."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())
    return priv_pem, pub.hex()


# ─────────────────────────────────────────────────────────────────────────────
# Canonical signed message (with domain separation)
# ─────────────────────────────────────────────────────────────────────────────
def mensaje_accion(dominio, payload):
    """Stable serialization of the domain + payload. EVERYTHING enters the signature.

    ABLATION: if SEPARACION_DOMINIO is off, it signs WITHOUT the domain label (only the payload),
    which ENABLES the 'signature from another domain' attack (manifiesto==autorizacion).
    """
    import ablacion as AB
    if dominio not in DOMINIOS:
        raise FirmaInvalida(f"dominio desconocido: {dominio}")
    # payload must be strict JSON-serializable (no NaN/inf nor default=str)
    cuerpo = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if AB.activo(AB.SEPARACION_DOMINIO):
        return f"{dominio}::{cuerpo}".encode("utf-8")
    return cuerpo.encode("utf-8")  # without domain: signatures from any domain are interchangeable


# ─────────────────────────────────────────────────────────────────────────────
# SIGN: only the human (separate CLI, has the private key)
# ─────────────────────────────────────────────────────────────────────────────
def firmar(dominio, payload, privada_bytes=None):
    """Signs `payload` under `dominio` with the PRIVATE key. Returns signature hex (128 Ed25519 chars).

    `privada_bytes`: private pem. If None, it is read from PATH_PRIV (outside the repo). In the
    agent runtime this function should NEVER be invoked: it does not have the private key.
    """
    if privada_bytes is None:
        priv_path, _pub = _rutas_frontera()
        privada_bytes = _leer_clave(priv_path)
    priv = _cargar_privada_ed25519(privada_bytes)
    return priv.sign(mensaje_accion(dominio, payload)).hex()


# ─────────────────────────────────────────────────────────────────────────────
# VERIFY: public key only (the runtime can do this, and NOTHING more)
# ─────────────────────────────────────────────────────────────────────────────
def verificar(dominio, payload, firma_hex):
    """Verifies the signature with the public key. False on any domain/payload change, or on a
    forged signature. Does not require the private key."""
    if not isinstance(firma_hex, str) or len(firma_hex) != 128:
        return False
    try:
        pub = cargar_publica()
        pub.verify(bytes.fromhex(firma_hex), mensaje_accion(dominio, payload))
        return True
    except (ValueError, InvalidSignature, FirmaInvalida):
        return False


if __name__ == "__main__":
    # Self-contained demo (testing only): generates an ephemeral pair, signs and verifies.
    priv_pem, pub_hex = generar_par()
    print("pública (hex):", pub_hex)

    def firmar_efimero(dominio, payload):
        priv = _cargar_privada_ed25519(priv_pem)
        return priv.sign(mensaje_accion(dominio, payload)).hex()

    payload = {"necesidad": "necesidad:eva:openfoam", "hash_accion": "abc123", "ttl_abs": 1234567890}
    firma = firmar_efimero("autorizacion", payload)
    print("firma (hex):", firma[:32], "...")

    # verify (with the correct public key)
    print("verifica payload original:", Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(pub_hex)).verify(bytes.fromhex(firma), mensaje_accion("autorizacion", payload)))
    # falsification: changed domain
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex)).verify(
            bytes.fromhex(firma), mensaje_accion("manifiesto", payload))
        print("ERROR: dominio cruzado verificó (bug)")
    except InvalidSignature:
        print("falsación OK: dominio cruzado NO verifica")
