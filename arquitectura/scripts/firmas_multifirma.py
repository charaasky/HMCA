#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""firmas_multifirma.py — Ed25519 PER VALIDATOR for real multi-signature (P0 A5).

Converts the "multi-row" deletion/governance (votes by username, without crypto) into
real "multi-signature": each validator has their own Ed25519 pair and their vote goes SIGNED and bound to the
hash of the proposal, with EXPIRATION. The monitor verifies the signature against the REGISTERED PUBLIC KEY
of THAT validator (not against a single human key).

Unlike `firma_asimetrica.py` (a single human key with a PIN anchor), here there are N keys,
one per validator, and verification is by explicit pubkey (hex) WITHOUT a global anchor: the pubkey
is registered as a governed fact in the memory and its integrity is covered by the hash chain (external
anchor + verify()). The pubkey registry is the identity of the validators.

Signature domains (domain separation, avoids cross-use of signatures):
  voto_revocacion      — deletion vote of a fact
  voto_cambio_quorum   — vote to change the current quorum
"""
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)

DOMINIO_VOTO_REVOCACION = "voto_revocacion"
DOMINIO_VOTO_CAMBIO_QUORUM = "voto_cambio_quorum"
DOMINIO_VOTO_ACCION = "voto_accion_gobernada"
DOMINIOS = (DOMINIO_VOTO_REVOCACION, DOMINIO_VOTO_CAMBIO_QUORUM, DOMINIO_VOTO_ACCION)

# Vote validity window (seconds): once the expiration passes, the vote is rejected.
EXPIRACION_VOTO_SEG = 300


class FirmaMultifirmaInvalida(ValueError):
    """The key/signature/domain is not valid for the per-validator multi-signature."""


def generar_par():
    """Generates an Ed25519 key pair. Returns (privada_pem_bytes, publica_hex)."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())
    return priv_pem, pub.hex()


def pubkey_valida(pub_hex):
    """Is `pub_hex` a well-formed Ed25519 public key (64 hex chars -> 32 bytes)?"""
    if not isinstance(pub_hex, str) or len(pub_hex) != 64:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        return True
    except (ValueError, TypeError):
        return False


def _cargar_privada(priv_pem):
    k = serialization.load_pem_private_key(priv_pem, password=None)
    if not isinstance(k, Ed25519PrivateKey):
        raise FirmaMultifirmaInvalida("la clave privada no es Ed25519")
    return k


# ── canonical proposal hash (what gets signed) ──────────────────────────────
def hash_propuesta(tipo, campos):
    """SHA-256 of `{tipo, **campos}` (canonical JSON, sorted, without NaN). Deterministic."""
    payload = {"tipo": tipo, **campos}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
        .encode("utf-8")).hexdigest()


def hash_propuesta_revocacion(key, motivo, categoria, quorum, propuesta_por, ts_propuesta):
    """Binding hash of a REVOCATION proposal (immutable fields of the proposal)."""
    return hash_propuesta(DOMINIO_VOTO_REVOCACION, {
        "key": key, "motivo": motivo, "categoria": categoria,
        "quorum": quorum, "propuesta_por": propuesta_por, "ts_propuesta": ts_propuesta,
    })


def hash_propuesta_cambio_quorum(n, propuesta_por, quorum_actual, ts_propuesta):
    """Binding hash of a QUORUM CHANGE proposal (immutable fields)."""
    return hash_propuesta(DOMINIO_VOTO_CAMBIO_QUORUM, {
        "n": n, "propuesta_por": propuesta_por,
        "quorum_actual": quorum_actual, "ts_propuesta": ts_propuesta,
    })


def hash_propuesta_accion_gobernada(tipo_accion, objetivo, motivo, quorum, propuesta_por,
                                     ts_propuesta):
    """Binding hash of a GOVERNED ACTION (security-degrading: rehabilitate incident,
    remove agent, install/wire tool). Reuses the vote domain separation."""
    return hash_propuesta(DOMINIO_VOTO_ACCION, {
        "tipo_accion": tipo_accion, "objetivo": objetivo, "motivo": motivo,
        "quorum": quorum, "propuesta_por": propuesta_por, "ts_propuesta": ts_propuesta,
    })


def _mensaje_voto(dominio, hash_propuesta, autor, ts_exp):
    """Canonical message that gets signed: separate domain + {hash_propuesta, autor, ts_exp}."""
    if dominio not in DOMINIOS:
        raise FirmaMultifirmaInvalida(f"dominio desconocido: {dominio}")
    cuerpo = json.dumps({"hash_propuesta": hash_propuesta, "autor": autor,
                         "ts_exp": int(ts_exp)}, sort_keys=True, ensure_ascii=False,
                        allow_nan=False)
    return f"{dominio}::{cuerpo}".encode("utf-8")


def firmar_voto(dominio, hash_propuesta, autor, ts_exp, priv_pem):
    """Signs a vote with the validator's private key. Returns hex signature (128 chars)."""
    priv = _cargar_privada(priv_pem)
    return priv.sign(_mensaje_voto(dominio, hash_propuesta, autor, ts_exp)).hex()


def verificar_voto(dominio, hash_propuesta, autor, ts_exp, firma_hex, pub_hex):
    """Verifies a signature against the validator's EXPLICIT pubkey. False on any change."""
    if not pubkey_valida(pub_hex):
        return False
    if not isinstance(firma_hex, str) or len(firma_hex) != 128:
        return False
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        pub.verify(bytes.fromhex(firma_hex),
                   _mensaje_voto(dominio, hash_propuesta, autor, ts_exp))
        return True
    except (ValueError, TypeError, InvalidSignature):
        return False


if __name__ == "__main__":
    priv, pub = generar_par()
    hp = hash_propuesta_revocacion("k", "m", "error", 1, "admin", "2026-01-01T00:00:00Z")
    ts_exp = 9999999999
    firma = firmar_voto(DOMINIO_VOTO_REVOCACION, hp, "usuario1", ts_exp, priv)
    print("pública:", pub)
    print("firma ok:", verificar_voto(DOMINIO_VOTO_REVOCACION, hp, "usuario1", ts_exp, firma, pub))
    print("firma forjada:", verificar_voto(DOMINIO_VOTO_REVOCACION, hp, "usuario1", ts_exp,
                                           "0" * 128, pub))
