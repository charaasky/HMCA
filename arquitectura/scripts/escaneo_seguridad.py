#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""escaneo_seguridad.py — artifact scanning (integrity + antivirus) for wiring.

Closes the "the hash is declared by hand" gap: the hash of the installed artifact is COMPUTED
deterministically (SHA-256), and the malware scan runs with ClamAV IF available. If there is no
antivirus, `av_disponible=False` is declared (honesty: the hash gives INTEGRITY, not malware
detection; malware detection requires the operator's AV or installing clamav).

The scan is a deterministic, READ-ONLY operation (it does not execute the artifact): it reads
bytes, computes a hash and, optionally, passes the path to clamscan. It does not run in the
agent's sandbox; it is a TCB (monitor/operator) operation over the installed artifact path.
"""
import hashlib
import os
import shutil
import subprocess
import sys


def hash_sha256_archivo(path):
    """SHA-256 of a file (block reading, without loading it entirely into RAM)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def hash_sha256_directorio(path):
    """Manifest-hash of a directory: hash of the sorted listing (relpath + file hash).

    Deterministic: does not depend on filesystem order nor timestamps."""
    entradas = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, path)
            entradas.append((rel, hash_sha256_archivo(fp)))
    payload = "\n".join(f"{rel} {h}" for rel, h in sorted(entradas)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def hash_sha256(path):
    """SHA-256 of a file or a directory (manifest). None if missing/invalid."""
    if os.path.isfile(path):
        return hash_sha256_archivo(path)
    if os.path.isdir(path):
        return hash_sha256_directorio(path)
    return None


def _clamscan(path):
    """ClamAV if available. Returns (av_disponible, malware_encontrado, detalle)."""
    if shutil.which("clamscan") is None:
        return False, False, "clamav no disponible (solo integridad por hash)"
    try:
        r = subprocess.run(["clamscan", "--no-summary", "-r", path],
                           capture_output=True, text=True, timeout=180)
        if r.returncode == 0:
            return True, False, "limpio"
        if r.returncode == 1:
            return True, True, "MALWARE detectado: " + (r.stdout or r.stderr)[:200]
        return True, False, f"clamscan error rc={r.returncode}"
    except Exception as e:  # noqa: BLE001
        return False, False, f"clamscan falló: {e}"


def escanear(path):
    """Scans an artifact. Returns {ok, hash_sha256, malware_encontrado, av_disponible,
    detalle_av}. If the path is missing or invalid, {ok: False, detalle}."""
    if not os.path.exists(path):
        return {"ok": False, "detalle": f"path no existe: {path}"}
    h = hash_sha256(path)
    if h is None:
        return {"ok": False, "detalle": f"path no es archivo ni directorio: {path}"}
    av, malware, detalle_av = _clamscan(path)
    return {"ok": True, "hash_sha256": h, "malware_encontrado": malware,
            "av_disponible": av, "detalle_av": detalle_av}


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp(prefix="hcma_scan_")
    with open(os.path.join(tmp, "a.txt"), "w") as f:
        f.write("hola")
    print(escanear(tmp))
