#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""ancla_publica.py — external anchor of the HCMA hash chain (append-only, hash-chained).

Fable ruling (P0 A1): the hash chain is tamper-evident, not immutable, as long as whoever writes
the store can truncate the tail or recompute the whole chain and leave it internally valid. For it
to be IMMUTABLE, `(seq, head_hash)` must be published OUTSIDE the writer's trust domain.

This module implements the LOCAL mode (chosen option): an external file with:
  - append-only: only lines are added; there is no deletion or rewriting.
  - hash-chained: each line = sha256(prev_hash | payload), with the last hash in an anti-truncation
    `.head` (same pattern as ledger_ejecucion.py / nonces_monitor.py).
  - in production it must live in a directory of ANOTHER UID (or with permissions that the store
    writer cannot alter); the REMOTE mode (syslog/Object Lock bucket/another host) remains a future
    option.

The anchor records the `seq` and the `head_hash` of the hash chain at an instant. If the store is
later truncated (the tail is removed), `verify()` detects that the store's `seq`/`head` ended up
BELOW the anchor, and reports it as truncated.
"""
import fcntl
import hashlib
import json
import os
import time


def _hash_linea(prev_hash, payload):
    return hashlib.sha256((prev_hash + "|" + payload).encode("utf-8")).hexdigest()


class AnclaPublica:
    def __init__(self, path):
        self.path = path
        self.head_path = path + ".head"

    def _ultimo_hash(self):
        if os.path.exists(self.head_path):
            with open(self.head_path, "r", encoding="utf-8") as f:
                h = f.read().strip()
            return h if h else ""
        return ""

    def _escribir_head(self, h):
        with open(self.head_path, "w", encoding="utf-8") as f:
            f.write(h + "\n")

    def anclar(self, seq, head_hash):
        """Adds an entry `(seq, head_hash)`. Returns the line hash. Serializes with a lock."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            prev = self._ultimo_hash()
            cuerpo = {"ts": time.time(), "seq": int(seq), "head_hash": head_hash}
            payload = json.dumps(cuerpo, sort_keys=True, ensure_ascii=False)
            h = _hash_linea(prev, payload)
            cuerpo["prev"] = prev
            cuerpo["hash"] = h
            f.write(json.dumps(cuerpo, sort_keys=True, ensure_ascii=False) + "\n")
            f.flush()
        self._escribir_head(h)
        return h

    def leer(self):
        """Returns (entries, intact). Detects truncation/recompute by comparing against .head."""
        if not os.path.exists(self.path):
            return [], True
        entradas = []
        prev = ""
        integra = True
        with open(self.path, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    e = json.loads(linea)
                except json.JSONDecodeError:
                    integra = False
                    continue
                cuerpo = {k: v for k, v in e.items() if k not in ("hash", "prev")}
                payload = json.dumps(cuerpo, sort_keys=True, ensure_ascii=False)
                esperado = _hash_linea(prev, payload)
                if e.get("hash") != esperado or e.get("prev") != prev:
                    integra = False
                entradas.append(e)
                prev = e.get("hash", "")
        head = self._ultimo_hash()
        if entradas and head != entradas[-1].get("hash", ""):
            integra = False
        return entradas, integra

    def ultimo(self):
        """Last entry (dict with seq/head_hash) or None if there is no anchor."""
        entradas, _ = self.leer()
        return entradas[-1] if entradas else None


def get_ancla_path():
    """Path of the anchor configured via the environment, or '' if not set."""
    return os.environ.get("HCMA_ANCLA_PATH", "")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        a = AnclaPublica(os.path.join(d, "ancla.log"))
        a.anclar(1, "hash1")
        a.anclar(2, "hash2")
        entradas, integra = a.leer()
        print("entradas:", len(entradas), "integra:", integra, "ultimo:", a.ultimo())
