#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_inventario.py — SBOM del sistema (binarios + mathlib + paquetes python + vulnerabilidades).

Verifica que el inventario enumera lo disponible con versiones, detecta dependencias rotas, se
puede registrar en la memoria HCMA (auditable) y genera un manifiesto congelado (réplica).

Uso:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/test_inventario.py
Exit 0 = OK, 1 = FAIL.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import inventario as INV                     # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        FAIL.append(name)


print("[1] Enumerar (SBOM)")
inv = INV.enumerar()
check("1a. hay binarios", len(inv["binarios"]) > 0, str(len(inv["binarios"])))
check("1b. mathlib version presente", bool(inv["mathlib_version"]), str(inv["mathlib_version"]))
check("1c. paquetes python > 0", inv["n_paquetes_python"] > 0, str(inv["n_paquetes_python"]))
check("1d. vulnerabilidades con pip_check_roto", "pip_check_roto" in inv["vulnerabilidades"])
nombres = {b["nombre"] for b in inv["binarios"]}
check("1e. python3 en el inventario", "python3" in nombres)
check("1f. lean en el inventario", "lean" in nombres)

print("\n[2] Registrar en HCMA (auditable)")
m = HCMAMemoriaSQLite(":memory:")
INV.registrar(m)
# las observaciones del inventario son 'calculadora' (crudas, sin linaje) → unverified. Se leen
# con current_raw (auditoría), no con current (que solo devuelve hechos canónicos).
r = m.current_raw("inventario:resumen")
check("2a. resumen registrado (raw)", r is not None)
check("2b. resumen con mathlib_version", r is not None and "mathlib_version" in (r.get("value") or {}))
b = m.current_raw("inventario:binario:lean")
check("2c. binario lean registrado (raw)", b is not None)
check("2d. hechos quedaron en facts_log (hash chain)",
      m.conn.execute("SELECT COUNT(*) FROM facts_log").fetchone()[0] > 0)

print("\n[3] Manifiesto congelado (réplica/escaneo aparte)")
doc = INV.manifiesto()
check("3a. manifiesto tiene hash", bool(doc.get("hash")), str(doc.get("hash", ""))[:20])
check("3b. hash determinista (2 corridas iguales)",
      doc.get("hash") == INV.manifiesto().get("hash"))

print(f"\nRESULTADO: {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
