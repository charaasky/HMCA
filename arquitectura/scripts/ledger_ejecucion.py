#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""ledger_ejecucion.py — execution ledger (append-only, hash-chained).

Fable ruling (`fable_dashboard_fable.md`): the dashboard needs an EXTERNAL source of TRUTH of the
effects actually executed, not HCMA's own log (which would be judge and party). This ledger
records, at the execution point, each risk>=1 effect: `tool_id`, `hash_accion` (canonical identity
of what was executed), `nonce` and `epoca`.

Properties:
  - append-only: only appended; no deletion or rewriting.
  - hash-chained: each line = sha256(prev_hash | payload), and the last hash is stored in an
    anti-truncation `.head` file (same pattern as nonces_monitor).
  - in production this file lives in a MONITOR directory (UID 999, 0700), outside the agent's
    (101) write scope. The one that records is the execution point (proxy or monitor), NOT the
    agent.

See LEDGER_RECONCILIACION.md for the deployment design.
"""
import fcntl
import hashlib
import json
import os
import time


def _hash_linea(prev_hash, payload):
    return hashlib.sha256((prev_hash + "|" + payload).encode("utf-8")).hexdigest()


class LedgerEjecucion:
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

    def registrar(self, tool_id, hash_accion, nonce, epoca):
        """Adds an execution to the ledger. Returns the line hash. Serializes with a lock."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            prev = self._ultimo_hash()
            entrada = {
                "ts": time.time(),
                "tool_id": tool_id,
                "hash_accion": hash_accion,
                "nonce": nonce,
                "epoca": epoca,
            }
            payload = json.dumps(entrada, sort_keys=True, ensure_ascii=False)
            h = _hash_linea(prev, payload)
            entrada["prev"] = prev
            entrada["hash"] = h
            f.write(json.dumps(entrada, sort_keys=True, ensure_ascii=False) + "\n")
            f.flush()
        self._escribir_head(h)
        return h

    def leer(self):
        """Reads all entries. Returns (entries, cadena_integra: bool)."""
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
                # recompute the line hash (without the hash nor prev field)
                payload = {k: v for k, v in e.items() if k not in ("hash", "prev")}
                payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
                esperado = _hash_linea(prev, payload_json)
                if e.get("hash") != esperado or e.get("prev") != prev:
                    integra = False
                entradas.append(e)
                prev = e.get("hash", "")
        # verify against .head (anti-truncation)
        head = self._ultimo_hash()
        if entradas and (head != entradas[-1].get("hash", "")):
            integra = False
        return entradas, integra


def get_ledger_path():
    """Path of the execution ledger configured via the environment, or '' if not set."""
    return os.environ.get("HCMA_EJECUCION_LEDGER", "")


def registrar_si_configurado(tool_id, hash_accion, nonce, epoca):
    """Records an execution in the external ledger IF configured.

    Returns True if recorded, False if no ledger is configured (not an error). Raises OSError if
    the configured ledger is not writable: the caller decides whether that is a security failure
    (fail-closed) or a log. In production the ledger lives in /var/lib/hcma-monitor/ledger/ or
    /var/lib/hcma-proxy/ledger/ (0700 of the owner UID): the agent (101) cannot write it.
    """
    path = get_ledger_path()
    if not path:
        return False
    LedgerEjecucion(path).registrar(tool_id, hash_accion, nonce or "", epoca or 0)
    return True


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        lg = LedgerEjecucion(os.path.join(d, "ejecucion.log"))
        h1 = lg.registrar("http", "abc123", "n1", 1)
        h2 = lg.registrar("openfoam", "def456", "n2", 1)
        entradas, integra = lg.leer()
        print("entradas:", len(entradas), "integra:", integra, "h1==h2:", h1 == h2)
