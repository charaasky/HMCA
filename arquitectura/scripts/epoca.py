#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""epoca.py — governed EPOCH (outside the store) for the anti-replay binding of authorizations.

Fable P0 (fable_nonce_fable.md): 'epoch that increments on every restore or rollback, stored
outside the store. This invalidates at once all authorizations issued before.'

The nonce alone is NOT enough against a full host restore: the nonces log goes back together with
the store. The EPOCH closes that gap: any restore/rollback increments the epoch, and any
authorization signed with an old epoch stops verifying (it stays bound to the current epoch).

The epoch lives OUTSIDE the versioned store (the monitor's own file), so a store restore does not
revert it. It is a monotonic counter that only the operator (or the monitor, on an explicit
restore) increments.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

# Epoch file, outside the store. Overridden via env for tests.
EPOCA_PATH = os.environ.get("HCMA_EPOCA_FILE", "/var/lib/hcma-monitor/epoca")


class EpocaError(RuntimeError):
    """The epoch is missing or is not a monotonic integer (fail-closed)."""


def _leer_epoca(path=None):
    path = path or EPOCA_PATH
    if not os.path.exists(path):
        raise EpocaError("archivo de época ausente (fail-closed)")
    try:
        with open(path, "r", encoding="utf-8") as f:
            v = f.read().strip()
        return int(v)
    except (OSError, ValueError) as e:
        raise EpocaError(f"época inválida: {e}")


def epoca_actual(path=None):
    """Returns the current epoch. Fail-closed: missing/invalid => EpocaError."""
    return _leer_epoca(path)


def incrementar_epoca(path=None):
    """Increments the epoch (called by the operator/monitor on restore/rollback).

    Fail-closed: if the file is missing, it does NOT create a new one by magic (that would reset
the counter and re-enable old authorizations). It requires the epoch to already exist.
    Returns the new epoch.
    """
    path = path or EPOCA_PATH
    actual = _leer_epoca(path)
    nueva = actual + 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(nueva) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return nueva


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp(prefix="epoca_")
    p = os.path.join(tmp, "epoca")
    with open(p, "w") as f:
        f.write("0\n")
    print("época actual:", epoca_actual(p))
    print("incrementar:", incrementar_epoca(p))
    print("época tras incrementar:", epoca_actual(p))
