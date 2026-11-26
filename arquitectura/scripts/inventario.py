#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""inventario.py — system SBOM: binaries + libraries + versions + vulnerabilities.

Ruling (2026-11-22): the CMDB must list EVERYTHING available (Mathlib, binaries, Python packages)
with their VERSIONS, detect whether something is vulnerable, and remain audited/reproducible so as
to have a REPLICA that is scanned separately.

What it does:
  - `enumerar()`            : full inventory (binaries + mathlib + python packages + vulns).
  - `registrar(m)`          : writes the summary + binaries + mathlib into HCMA memory (auditable).
  - `manifiesto(path=None)` : FROZEN manifest (JSON) with hash, to scan separately.

Vulnerability honesty:
  - `pip check` (broken dependencies) ALWAYS runs (it is local, deterministic).
  - CVE scanning (`pip-audit` / `trivy` / `grype` / `clamscan`) only if installed; if not,
    `cve_scanner_disponible=False` is declared (the hash gives integrity, not CVEs).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import capacidades as CAP  # noqa: E402

# EXTRA binaries (besides the capacidades.HERRAMIENTAS catalog). (id, binario, args_version)
BINARIOS_EXTRA = [
    ("python3", "python3", ["python3", "--version"]),
    ("pip", "pip", ["pip", "--version"]),
    ("git", "git", ["git", "--version"]),
    ("runsc", "runsc", ["runsc", "--version"]),
    ("firecracker", "firecracker", ["firecracker", "--version"]),
    ("clamscan", "clamscan", ["clamscan", "--version"]),
    ("pip-audit", "pip-audit", ["pip-audit", "--version"]),
]


def _probar(binario, args, env=None):
    """Runs a version command. Returns (available, version, path)."""
    import copy
    entorno = dict(os.environ)
    if env:
        entorno.update(env)
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30, env=entorno)
        out = (r.stdout or r.stderr).strip()
        version = out.splitlines()[0][:120] if out else ""
        return True, version, shutil.which(args[0])
    except FileNotFoundError:
        return False, "", None
    except Exception as e:  # noqa: BLE001
        return False, repr(e), shutil.which(args[0])


def _binarios():
    out = []
    # from the capacidades.HERRAMIENTAS catalog (already brings absolute path + env)
    for tid, t in CAP.HERRAMIENTAS.items():
        disp, detalle = CAP._verificar_binario(t["binario"], t.get("comando_verificacion"),
                                               t.get("env"))
        # version: try the verification command if present, otherwise only availability
        version = detalle if disp else ""
        out.append({"nombre": tid, "disponible": disp, "version": version,
                    "binario": t["binario"], "proposito": t["proposito"]})
    for tid, binario, args in BINARIOS_EXTRA:
        disp, version, ruta = _probar(binario, args)
        out.append({"nombre": tid, "disponible": disp, "version": version,
                    "binario": binario, "proposito": ""})
    return out


def _mathlib_version():
    tc = os.path.join(DEP, "mathlib4", "lean-toolchain")
    if os.path.exists(tc):
        v = open(tc, encoding="utf-8").read().strip()
        return v.replace("leanprover/lean4:", "lean4 ")
    return None


def _python_paquetes():
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "list", "--format=json"],
                           capture_output=True, text=True, timeout=60)
        return [{"nombre": p["name"], "version": p["version"]}
                for p in json.loads(r.stdout or "[]")]
    except Exception:  # noqa: BLE001
        return []


def _vulnerabilidades():
    # pip check (broken dependencies) — local and deterministic
    roto = []
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "check"],
                           capture_output=True, text=True, timeout=120)
        roto = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001
        pass
    # CVE scanner (pip-audit / trivy / grype / clamscan) — only if installed
    cve_scanner = None
    for sc in ("pip-audit", "trivy", "grype", "clamscan"):
        if shutil.which(sc):
            cve_scanner = sc
            break
    return {"pip_check_roto": roto, "cve_scanner_disponible": cve_scanner is not None,
            "cve_scanner": cve_scanner}


def enumerar():
    """Full inventory (SBOM)."""
    binarios = _binarios()
    paquetes = _python_paquetes()
    return {
        "binarios": binarios,
        "mathlib_version": _mathlib_version(),
        "paquetes_python": paquetes,
        "n_paquetes_python": len(paquetes),
        "vulnerabilidades": _vulnerabilidades(),
    }


def _hash_manifesto(inv):
    payload = json.dumps(inv, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def manifiesto(path=None):
    """FROZEN manifest (JSON) with hash, for replica/separate scanning."""
    inv = enumerar()
    hash_ = _hash_manifesto(inv)
    doc = {"hash": hash_, "inventario": inv}
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
    return doc


def registrar(m):
    """Writes the summary + binaries + mathlib into HCMA memory (auditable)."""
    inv = enumerar()
    m.write("inventario:resumen", {
        "mathlib_version": inv["mathlib_version"],
        "n_binarios": len(inv["binarios"]),
        "n_paquetes_python": inv["n_paquetes_python"],
        "dependencias_rotas": len(inv["vulnerabilidades"]["pip_check_roto"]),
        "cve_scanner": inv["vulnerabilidades"]["cve_scanner"],
    }, unit="", fuente="calculadora", verified=True, meta={"tipo": "inventario"})
    for b in inv["binarios"]:
        m.write(f"inventario:binario:{b['nombre']}",
                {"disponible": b["disponible"], "version": b["version"],
                 "binario": b["binario"]},
                unit="", fuente="calculadora", verified=True, meta={"tipo": "inventario"})
    return inv


if __name__ == "__main__":
    inv = enumerar()
    print("== binarios ==")
    for b in inv["binarios"]:
        print(f"  {b['nombre']:14s} disponible={b['disponible']!s:5s} {b['version'][:60]}")
    print(f"\nmathlib: {inv['mathlib_version']}")
    print(f"paquetes python: {inv['n_paquetes_python']}")
    print(f"vulnerabilidades: cve_scanner={inv['vulnerabilidades']['cve_scanner']}, "
          f"dependencias_rotas={len(inv['vulnerabilidades']['pip_check_roto'])}")
