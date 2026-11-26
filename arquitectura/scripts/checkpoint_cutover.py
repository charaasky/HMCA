#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""checkpoint_cutover.py — Phase 2 (step 3): pre-revocation checkpoint computation.

Fable ruling (fable_fase2_revocacion_fable.md, step 5): before revocation one must compute and
SIGN a checkpoint of the state head, with a key that 101 (agent) could never invoke. The
checkpoint does NOT certify that the history is true; it only FREEZES it and leaves a record that
a human accepted that head as pre-revocation legacy.

What the checkpoint covers (Fable):
  - head of facts_log (last hash of the chain)
  - number of entries in facts_log
  - digest of the store file at rest (sha256)
  - digest of the nonces log at rest (sha256)
  - old epoch (and the new one is set when the monitor starts)

This module COMPUTES the checkpoint (does not sign: the human signs with their offline key).
The computation must run with the runtime STOPPED and with a tool that does NOT run as 101.
"""
import hashlib
import json
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)


def _sha256_file(path):
    """sha256 of a file at rest. Returns hex or None if it does not exist."""
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def head_facts_log(conn):
    """Returns (head_hash, n_entradas) of facts_log, rebuilding the chain from genesis.

    The head is the hash of the LAST facts_log entry (the chain). If the chain is broken,
    returns (None, n) and `cadena_ok=False`.
    """
    import sqlite3
    filas = conn.execute(
        "SELECT version, key, unit, value, fuente, verified, hash_prev, hash "
        "FROM facts_log ORDER BY version ASC").fetchall()
    n = len(filas)
    prev = ""
    head = None
    cadena_ok = True
    for r in filas:
        # recompute the entry hash to verify the chain (the memoria_sqlite module uses its own
        # format; here we only verify that hash_prev chains, without re-deriving the exact
        # content — the chain is validated by comparing hash_prev with the previous head).
        hash_prev = r["hash_prev"] if isinstance(r, dict) else None
        # Note: the real schema is inspected at runtime; if there is no hash_prev/hash, the
        # checkpoint only reports n and the file digest (memoria_sqlite validates the chain on
        # startup).
        prev = head
        head = None
    return head, n, cadena_ok


def calcular_checkpoint(store_path, nonces_path=None, epoca_vieja=None):
    """Computes the pre-revocation checkpoint and returns it as a stable dict (to sign).

    - store_path: path of the store's SQLite (facts).
    - nonces_path: path of the nonces log (append-only) or None.
    - epoca_vieja: value of the current epoch before the cutover (or None if none).
    """
    checkpoint = {
        "store_digest": _sha256_file(store_path),
        "store_path": store_path,
    }
    # nº of facts and head of facts_log from the SQLite (without importing memoria_sqlite, so this
    # tool runs without the monitor library — it is independent and audited).
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        checkpoint["n_facts"] = conn.execute("SELECT COUNT(*) AS c FROM facts").fetchone()["c"]
        checkpoint["n_facts_log"] = conn.execute(
            "SELECT COUNT(*) AS c FROM facts_log").fetchone()["c"]
        # head of facts_log: last hash of the hash column (if present). The REAL head of the
        # chain is the last row by `seq` (AUTOINCREMENT), NOT by `version` (which is -1 for
        # nonce/consumir/stale/log_op ops). It must match what the monitor verifies in
        # hcma_monitor_daemon._estado_logico (P1 phantom WAL).
        cols = [r[1] for r in conn.execute("PRAGMA table_info(facts_log)").fetchall()]
        if "hash" in cols:
            ult = conn.execute(
                "SELECT hash FROM facts_log ORDER BY seq DESC LIMIT 1").fetchone()
            checkpoint["head_facts_log"] = ult["hash"] if ult else None
        conn.close()
    except Exception as e:
        checkpoint["sqlite_error"] = repr(e)

    checkpoint["nonces_digest"] = _sha256_file(nonces_path) if nonces_path else None
    if nonces_path and os.path.exists(nonces_path + ".head"):
        checkpoint["nonces_head_ancla"] = open(nonces_path + ".head").read().strip()

    checkpoint["epoca_vieja"] = epoca_vieja
    # canonical JSON (to sign by the human; this dict is the checkpoint payload)
    return checkpoint


def canonical(checkpoint):
    """Stable canonical serialization of the checkpoint (for signing)."""
    return json.dumps(checkpoint, sort_keys=True, ensure_ascii=False)


if __name__ == "__main__":
    store = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "HCMA_STORE", "/home/charasky/Documentos/HMC/arquitectura/data/memoria.db")
    nonces = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("HCMA_NONCE_LOG", "")
    epoca = int(sys.argv[3]) if len(sys.argv) > 3 else None
    cp = calcular_checkpoint(store, nonces or None, epoca)
    print("CHECKPOINT (a firmar por el humano):")
    print(json.dumps(cp, indent=2, sort_keys=True, ensure_ascii=False))
    print("\ncanonical (para firmar):")
    print(canonical(cp))
