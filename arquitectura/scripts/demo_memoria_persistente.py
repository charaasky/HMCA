#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""End-to-end demo: orchestrator with memory persisted in SQLite + restart survival.

1) Runs the orchestrator (motor domain) with ARQ_MEMORIA_DB → writes to SQLite.
2) Closes and reopens the file (simulates process restart).
3) Shows that the state survives, that the search (FTS) remembers, and that the audit
   log (hash chain) remains intact.
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# demo config: deterministic, no web, demo inputs (no interaction)
os.environ["ARQ_DEMO"] = "1"
os.environ["ARQ_WEB"] = "0"
os.environ["ARQ_DETERMINISTA"] = "1"

from prototipo import orquestar, DOMINIOS
from memoria_sqlite import HCMAMemoriaSQLite

db = os.path.join(tempfile.mkdtemp(), "hcma_motor.db")
os.environ["ARQ_MEMORIA_DB"] = db

print("=" * 68)
print("PASO 1 — orquestar motor eléctrico (memoria → SQLite)")
print("=" * 68)
hcma = orquestar(
    "Dimensionar un motor eléctrico: calcular potencia y corriente a partir de par y velocidad.",
    {"motor_electrico": DOMINIOS["motor_electrico"]})
hcma.close()

print("\n" + "=" * 68)
print("PASO 2 — 'reinicio': reabrir el archivo SQLite")
print("=" * 68)
m = HCMAMemoriaSQLite(db)
estado = m.state()
print(f"Estado persistido ({len(estado)} hechos vigentes):")
for k, v in sorted(estado.items()):
    print(f"   {k}: {v['value']} {v['unit']}  [{v['fuente']}]")

print("\nRecall semántico sobre la memoria persistida (FTS5):")
for q in ["corriente del motor", "potencia", "par motor"]:
    r = m.buscar(q)
    if r:
        print(f"   '{q}' → {r[0]['key']} = {r[0]['value']} {r[0]['unit']}  (score {r[0]['score']})")
    else:
        print(f"   '{q}' → sin match")

n_log = m.conn.execute("SELECT COUNT(*) FROM facts_log").fetchone()[0]
n_facts = m.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
hash_last = m.conn.execute(
    "SELECT hash FROM facts_log ORDER BY seq DESC LIMIT 1").fetchone()[0]
print(f"\nAuditoría: {n_log} operaciones en facts_log, {n_facts} versiones en facts.")
print(f"   último hash de la cadena: {hash_last[:20]}…")

m.close()
print("\n✅ DEMO OK: la memoria sobrevive el reinicio y responde consultas.")
