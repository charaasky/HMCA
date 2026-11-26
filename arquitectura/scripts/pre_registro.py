#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""pre_registro.py — run provenance (run_id + commit + model + seed + dataset hash).

Fable ruling (`fable_dashboard_fable.md`, P0): "per-run provenance: run_id, commit, model,
seed, dataset hash and attested effective configuration". Before running D3/D4 this metadata
+ its hash is FROZEN; the result stays bound to that provenance (reproducible + auditable). It is the
mechanism that checks off `procedencia_corrida` on the dashboard.

Does NOT run experiments: it only produces the identity metadata and freezes/verifies it.
"""
import hashlib
import json
import os
import subprocess
import time
import uuid


def _git_commit():
    raiz = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, cwd=raiz).stdout.strip()
    except Exception:
        return ""


def commit_actual():
    """Current HEAD git hash (for the dashboard, without generating a run_id)."""
    return _git_commit()


def _sha256_file(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _dataset_hash(data_dir):
    """Canonical hash of the dataset (the cases to be run), by file name."""
    if not data_dir or not os.path.isdir(data_dir):
        return ""
    import glob
    archivos = sorted(glob.glob(os.path.join(data_dir, "*.json")) +
                      glob.glob(os.path.join(data_dir, "*.jsonl")))
    if not archivos:
        return ""
    h = hashlib.sha256()
    for a in archivos:
        h.update((os.path.basename(a) + ":" + (_sha256_file(a) or "") + "\n").encode("utf-8"))
    return h.hexdigest()


def computar(modelo, seeds, data_dir=None, config_efectiva=None):
    """Returns the provenance dict. `seeds` = list of int; `config_efectiva` = dict of
    attested effective flags/defenses (e.g. the ablation state)."""
    return {
        "run_id": uuid.uuid4().hex,
        "commit": _git_commit(),
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "modelo": modelo,
        "seeds": list(seeds or []),
        "dataset_hash": _dataset_hash(data_dir) if data_dir else "",
        "config_efectiva": config_efectiva or {},
    }


def congelar(procedencia, path):
    """Freezes the provenance to a JSON file + returns its identity hash."""
    canon = json.dumps(procedencia, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"procedencia": procedencia, "hash_procedencia": h}, f,
                  indent=2, ensure_ascii=False, sort_keys=True)
    return h


def verificar(path):
    """Reads a frozen pre-registration and verifies that the hash matches the content.

    Returns (procedencia, ok). `ok=False` if the file was altered after being frozen.
    """
    with open(path, "r", encoding="utf-8") as f:
        datos = json.load(f)
    proc = datos.get("procedencia", {})
    canon = json.dumps(proc, sort_keys=True, ensure_ascii=False)
    esperado = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return proc, (datos.get("hash_procedencia") == esperado)


if __name__ == "__main__":
    import tempfile
    p = computar("openai/gpt-4o-mini", [0, 1, 2],
                 data_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "..", "benchmarks", "injecagent", "data"))
    print(json.dumps(p, indent=2, ensure_ascii=False))
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        h = congelar(p, f.name)
        print("hash:", h)
        proc, ok = verificar(f.name)
        print("verificado:", ok, "run_id coincide:", proc["run_id"] == p["run_id"])
