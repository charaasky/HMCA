#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""firmar_checkpoint.py — Phase 2 (closure 4): generates/keeps the offline human key for the
checkpoint and signs with it (NOT ephemeral).

Fable ruling: the checkpoint key must be PERSISTED and kept, not ephemeral per
run. An ephemeral key makes the signature "prove nothing between runs". This tool:

  - `generar-clave`: generates ONE Ed25519 pair, stores the PRIVATE key in /var/lib/hcma-monitor/humano
    of the machine? NO — Fable: "private outside the machine". Stores the private key in a FILE
    kept outside the repo and out of the agent's reach (by default
    ~/.config/hcma/checkpoint_privada.key, 600, not versioned), and the PUBLIC one in
    /etc/hcma/checkpoint.pub (root-owned).
  - `firmar`: reads the kept private key (does not regenerate), signs the canonical checkpoint under
    the 'manifiesto' domain, writes the signature to /etc/hcma/checkpoint.sig.

The checkpoint key is DISTINCT from the signer key (102) and from the authorization key. It is used
only to freeze the pre-revocation head of Phase 2.

Usage:
  sudo python3 firmar_checkpoint.py generar-clave
  sudo python3 firmar_checkpoint.py firmar <store.db> [nonces.log] [epoca]
"""
import json
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
import firma_asimetrica as FA  # noqa: E402

ANCLA_DIR = "/etc/hcma"
CHECKPOINT_PUB = os.path.join(ANCLA_DIR, "checkpoint.pub")
CHECKPOINT_SIG = os.path.join(ANCLA_DIR, "checkpoint.sig")
CHECKPOINT_JSON = os.path.join(ANCLA_DIR, "checkpoint.json")

# The checkpoint private key lives OUTSIDE the repo and out of the agent's reach. By default in
# the human's home (charasky), mode 600, NOT versioned. Fable: "private outside the machine"
# is the ideal; here it is kept locally but separate from the agent and from the signer.
PRIVADA_PATH = os.environ.get(
    "HCMA_CHECKPOINT_PRIVADA",
    os.path.expanduser("~/.config/hcma/checkpoint_privada.key"))


def _generar_clave():
    """Generates the checkpoint key pair ONCE. Writes ONLY the private key (600) in the human's
    home. The public key is derived and written to /etc in the 'firmar' step (with sudo)."""
    priv_pem, pub_hex = FA.generar_par()
    os.makedirs(os.path.dirname(PRIVADA_PATH), exist_ok=True)
    if os.path.exists(PRIVADA_PATH):
        raise FileExistsError(f"la privada ya existe en {PRIVADA_PATH}. No la regeneres (es UNA).")
    with open(PRIVADA_PATH, "wb") as f:
        f.write(priv_pem)
    os.chmod(PRIVADA_PATH, 0o600)
    return priv_pem, pub_hex


def _cargar_privada():
    """Reads the kept private key. If it does not exist, raises an error (does not regenerate: the key is ONE)."""
    if not os.path.exists(PRIVADA_PATH):
        raise FileNotFoundError(
            f"no existe la privada del checkpoint en {PRIVADA_PATH}. "
            f"Ejecutá 'generar-clave' UNA vez primero.")
    with open(PRIVADA_PATH, "rb") as f:
        return f.read()


def firmar_checkpoint(checkpoint):
    """Signs the checkpoint with the KEPT private key (not ephemeral).

    Returns (firma_hex, publica_hex, checkpoint_canon).
    """
    priv_pem = _cargar_privada()
    check_canon = json.dumps(checkpoint, sort_keys=True, ensure_ascii=False)
    firma = FA.firmar("manifiesto", {"checkpoint": checkpoint}, privada_bytes=priv_pem)
    # derive the public key from the private one (do not trust a file that might be missing)
    desde_priv = FA._cargar_privada_ed25519(priv_pem)
    pub_bytes = desde_priv.public_key().public_bytes(
        FA.serialization.Encoding.Raw, FA.serialization.PublicFormat.Raw)
    pub_hex = pub_bytes.hex()
    return firma, pub_hex, check_canon


def escribir_ancla(firma_hex, pub_hex, check_canon):
    """Writes the public key, the signature and the checkpoint to /etc/hcma (root-owned)."""
    os.makedirs(ANCLA_DIR, exist_ok=True)
    with open(CHECKPOINT_PUB, "w") as f:
        f.write(pub_hex + "\n")
    os.chmod(CHECKPOINT_PUB, 0o644)
    with open(CHECKPOINT_SIG, "w") as f:
        f.write(firma_hex + "\n")
    os.chmod(CHECKPOINT_SIG, 0o644)
    with open(CHECKPOINT_JSON, "w") as f:
        f.write(check_canon + "\n")
    os.chmod(CHECKPOINT_JSON, 0o644)
    return CHECKPOINT_PUB, CHECKPOINT_SIG, CHECKPOINT_JSON


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("uso: firmar_checkpoint.py generar-clave | firmar <store> [nonces] [epoca]",
              file=sys.stderr)
        sys.exit(2)

    if args[0] == "generar-clave":
        priv_pem, pub_hex = _generar_clave()
        print(f"clave del checkpoint GENERADA y custodiada:")
        print(f"  privada -> {PRIVADA_PATH} (600, FUERA del repo y del agente)")
        print(f"  pública -> {CHECKPOINT_PUB}")
        print(f"  pública (hex): {pub_hex}")
        print("CUSTODIÁ la privada fuera de la máquina (Fable: 'privada fuera de la máquina').")
        print("No la regeneres: es UNA clave de una sola vez para congelar el head.")
        sys.exit(0)

    if args[0] == "firmar":
        from checkpoint_cutover import calcular_checkpoint, canonical
        store = args[1] if len(args) > 1 else os.environ.get(
            "HCMA_STORE", os.path.join(SCRIPTS, "..", "data", "memoria.db"))
        nonces = args[2] if len(args) > 2 else ""
        epoca = int(args[3]) if len(args) > 3 else None
        cp = calcular_checkpoint(store, nonces or None, epoca)
        check_canon = canonical(cp)
        print("Checkpoint a firmar:")
        print(check_canon)
        print()
        firma, pub_hex, _ = firmar_checkpoint(cp)
        print(f"pública (checkpoint): {pub_hex}")
        print(f"firma (checkpoint) : {firma}")
        print()
        escribir_ancla(firma, pub_hex, check_canon)
        print(f"ancla fijada: {CHECKPOINT_SIG} + {CHECKPOINT_JSON}")
        sys.exit(0)

    print(f"comando desconocido: {args[0]}", file=sys.stderr)
    sys.exit(2)
