#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""nonces_monitor.py — MONOTONIC, append-only nonce registry, OUTSIDE the versioned store.

Fable P0 (6th revision + fable_nonce_fable.md query): an anti-replay registry must be MONOTONIC,
and a versioned store is REVERTIBLE by design. 'Putting monotonic state inside revertible state is
a category error.'

That is why the consumed nonce does NOT live in the SQLite `nonces_consumidos` table (which a
restore can revert), but in a separate APPEND-ONLY LOG owned by the MONITOR (outside the store).

Design (Fable's 'A formalized as C' option):
  1. Authoritative on-disk LOG, append + fsync BEFORE applying (crash => authorization burned
     without effect, the human re-signs). Never the reverse.
  2. Process memory = only a CACHÉ of the log; rebuilt on startup.
  3. FAIL-CLOSED startup: if the log is missing / corrupt / the chain does not verify, the monitor
     does NOT accept authorizations. An empty log never means 'no nonce consumed'.
  4. STATE BINDING: the authorization is bound to (operation hash, parent version of the store CAS,
     expiration, epoch). The nonce is the SECOND line of defense, not the only one.
  5. EPOCH is incremented on every restore/rollback, stored outside the store.

ARCHITECTURE NOTE: in the FULL MEDIATION model (see MEDIACION_COMPLETA.md), this module runs
INSIDE the `hcma-monitor` process (UID 103, host), NOT inside the agent runtime. This module is the
primitive; the next step is to extract the monitor into a separate process.
"""
import json
import os
import sys
import time
import hashlib

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

# Path of the authoritative log (outside the versioned store; owned by the monitor).
# Overridden via env for tests.
LOG_PATH = os.environ.get("HCMA_NONCE_LOG", "/var/lib/hcma-monitor/nonces.log")


class NonceLogError(RuntimeError):
    """The nonces log is missing, corrupt, or its chain does not verify (fail-closed)."""


def _hash_linea(prev_hash, nonce, tool_id, hash_accion, ts, epoca):
    canon = json.dumps({"prev": prev_hash, "nonce": nonce, "tool_id": tool_id,
                        "hash_accion": hash_accion, "ts": ts, "epoca": epoca},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class RegistroNonces:
    """Append-only log of consumed nonces, with hash chain and fail-closed startup."""

    def __init__(self, path=None, epoca_actual=0):
        self.path = path or LOG_PATH
        self.head_path = self.path + ".head"  # anchored head (detects tail truncation)
        self._consumidos = set()   # in-memory cache
        self._cabeza = ""          # hash of the last record (head of chain)
        self._epoca = epoca_actual
        self._abierto = False

    def abrir(self, fail_closed=True):
        """Loads the log and verifies the chain. If corrupt/missing and fail_closed, ABORTS.

        Fable P0: fail-closed startup — an empty log NEVER means 'no nonce consumed'. In
        production, the absence of the log is a reason to block (not to open 'empty').
        """
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    lineas = [l for l in f.read().splitlines() if l.strip()]
            except OSError as e:
                raise NonceLogError(f"log de nonces inaccesible: {e}")
            prev = ""
            for i, linea in enumerate(lineas):
                try:
                    rec = json.loads(linea)
                except (ValueError, TypeError):
                    if fail_closed:
                        raise NonceLogError(f"linea {i} corrupta (no JSON)")
                    continue
                esperado = _hash_linea(prev, rec.get("nonce", ""), rec.get("tool_id", ""),
                                       rec.get("hash_accion", ""), rec.get("ts", ""),
                                       rec.get("epoca", 0))
                if rec.get("hash") != esperado:
                    if fail_closed:
                        raise NonceLogError(f"cadena rota en linea {i}")
                self._consumidos.add(rec.get("nonce", ""))
                prev = rec.get("hash", "")
            self._cabeza = prev
            # TAIL TRUNCATION DETECTION (Fable 7th): the head must match an anchor stored apart.
            # If the log was truncated (the last N lines were deleted), the internal chain remains
            # 'valid' but the head does not match the anchor => fail-closed.
            self._verificar_cabeza_anclada(fail_closed)
            self._abierto = True
            return self
        # log missing
        if fail_closed:
            raise NonceLogError("log de nonces ausente: el monitor no acepta autorizaciones "
                                "(arranque fail-closed). Crear el log vacío explícitamente.")
        # dev/test mode: missing log => opens empty (but explicit, not silent)
        self._abierto = True
        return self

    def _asegurar_abierto(self):
        if not self._abierto:
            raise NonceLogError("registro de nonces no abierto")

    def _verificar_cabeza_anclada(self, fail_closed=True):
        """Compares the log head with the anchor stored apart (detects tail truncation).

        If the .head file does not exist (first startup) and the log is not empty, it creates it
        with the current head. If it exists and does not match the current head, the log was
        TRUNCATED => fail-closed (even if the internal chain looks intact).
        """
        if os.path.exists(self.head_path):
            try:
                with open(self.head_path, "r", encoding="utf-8") as f:
                    ancla = f.read().strip()
            except OSError as e:
                raise NonceLogError(f"ancla de cabeza inaccesible: {e}")
            if ancla and ancla != self._cabeza:
                if fail_closed:
                    raise NonceLogError(
                        f"log truncado: cabeza actual '{self._cabeza[:12]}...' != ancla "
                        f"'{ancla[:12]}...' (borraron la cola)")
        else:
            # first startup: sets the anchor (only if the log is not empty)
            if self._cabeza:
                try:
                    os.makedirs(os.path.dirname(self.head_path), exist_ok=True)
                    with open(self.head_path, "w", encoding="utf-8") as f:
                        f.write(self._cabeza + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                except OSError as e:
                    raise NonceLogError(f"no se pudo fijar el ancla: {e}")

    def consumido(self, nonce):
        """Was the nonce already consumed? (in-memory cache, synchronized with the log)."""
        self._asegurar_abierto()
        return nonce in self._consumidos

    def consumir(self, nonce, tool_id, hash_accion):
        """Consumes a nonce: append + fsync BEFORE returning True (monotonic content).

        Fable P0 (consume before apply + exclusive lock): first the nonce is written to the log and
        fsynced; ONLY then does the caller apply the effect. A crash between the two burns the
        authorization without effect (safe). `fcntl.flock` prevents TWO processes from spending the
        same nonce (Fable 7th: while the monitor is a library, the append needs an exclusive lock).
        Returns False if the nonce was already present (replay).
        """
        import fcntl
        self._asegurar_abierto()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # exclusive lock: a single consumer
            # re-read the tail under lock before deciding (another process may have consumed)
            f.seek(0)
            lineas = [l for l in f.read().splitlines() if l.strip()]
            if any(json.loads(l).get("nonce") == nonce for l in lineas if l.startswith("{")):
                return False
            if nonce in self._consumidos:
                return False
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            nuevo_hash = _hash_linea(self._cabeza, nonce, tool_id, hash_accion, ts, self._epoca)
            linea = json.dumps({"nonce": nonce, "tool_id": tool_id, "hash_accion": hash_accion,
                                "ts": ts, "epoca": self._epoca, "hash": nuevo_hash},
                               sort_keys=True, ensure_ascii=False)
            f.write(linea + "\n")
            f.flush()
            os.fsync(f.fileno())
            self._consumidos.add(nonce)
            self._cabeza = nuevo_hash
            # update the head anchor (same dir)
            with open(self.head_path, "w", encoding="utf-8") as hf:
                hf.write(nuevo_hash + "\n")
                hf.flush()
                os.fsync(hf.fileno())
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return True


# per-process singleton (the monitor keeps ONE live instance)
_instancia = None


def registro(epoca_actual=0):
    global _instancia
    if _instancia is None:
        _instancia = RegistroNonces(epoca_actual=epoca_actual)
    return _instancia


if __name__ == "__main__":
    # self-contained demo
    import tempfile
    tmp = tempfile.mkdtemp(prefix="nonces_")
    p = os.path.join(tmp, "nonces.log")
    r = RegistroNonces(path=p)
    r.abrir(fail_closed=False)
    print("consumir abc:", r.consumir("abc", "openfoam", "h1"))
    print("consumir abc de nuevo (replay):", r.consumir("abc", "openfoam", "h1"))
    print("consumir def:", r.consumir("def", "openfoam", "h2"))
    # reopen (persistence + chain)
    r2 = RegistroNonces(path=p)
    r2.abrir(fail_closed=True)
    print("tras reabrir, consumido abc:", r2.consumido("abc"))
    print("tras reabrir, consumido def:", r2.consumido("def"))
