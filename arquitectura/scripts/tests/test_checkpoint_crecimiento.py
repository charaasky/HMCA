#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_checkpoint_crecimiento.py — el verificador tolera crecimiento append-only, detecta tampering.

El store es append-only: crece legítimamente entre cortes de estado. El verificador fail-closed
no debe morir por crecimiento, pero SÍ debe detectar:
  - borrado (n_facts / n_facts_log decrecen),
  - reescritura de historia (el head del checkpoint ya no es prefijo de la cadena),
  - cadena rota (head por seq != last_log_hash).
"""
import os
import sys
import tempfile

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")
    return cond


import json  # noqa: E402
import sqlite3  # noqa: E402
import hcma_monitor_daemon as MON  # noqa: E402

tmp = tempfile.mkdtemp(prefix="hcma_crec_")
STORE = os.path.join(tmp, "memoria.db")
MON.STORE_PATH = STORE
MON.CKP_JSON = os.path.join(tmp, "checkpoint.json")
MON.CKP_SIG = os.path.join(tmp, "checkpoint.sig")


def _poblar(n_log, hash_seq1, last_log_hash, n_facts):
    if os.path.exists(STORE):
        os.remove(STORE)
    c = sqlite3.connect(STORE)
    c.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY)")
    c.execute("CREATE TABLE facts_log (seq INTEGER PRIMARY KEY, hash TEXT)")
    c.execute("CREATE TABLE hcma_meta (k TEXT PRIMARY KEY, v TEXT)")
    c.executemany("INSERT INTO facts(id) VALUES (?)", [(i + 1,) for i in range(n_facts)])
    for s in range(1, n_log + 1):
        h = hash_seq1 if s == 1 else f"h{s}"
        c.execute("INSERT INTO facts_log(seq, hash) VALUES (?, ?)", (s, h))
    c.execute("INSERT INTO hcma_meta(k, v) VALUES ('last_log_hash', ?)", (last_log_hash,))
    c.commit()
    c.close()


def _checkpoint(n_facts, n_facts_log, head):
    json.dump({"n_facts": n_facts, "n_facts_log": n_facts_log, "head_facts_log": head},
              open(MON.CKP_JSON, "w"))
    open(MON.CKP_SIG, "w").write("firma\n")


print("[1] tolera crecimiento append-only (checkpoint n=1, store n=3)")
_poblar(3, "H1", "h3", 3)
_checkpoint(1, 1, "H1")
r = MON.verificar_checkpoint()
check("1a. extensión válida verifica OK", r["ok"] is True, r["detalle"])

print("\n[2] detecta borrado (n_facts decrece)")
_poblar(0, "H1", None, 0)
_checkpoint(1, 1, "H1")
r = MON.verificar_checkpoint()
check("2a. rechaza n_facts decrecido", r["ok"] is False and "decreció" in r["detalle"], r["detalle"])

print("\n[3] detecta reescritura de historia (prefijo roto)")
_poblar(3, "ROTO", "h3", 3)
_checkpoint(1, 1, "H1")
r = MON.verificar_checkpoint()
check("3a. rechaza prefijo roto", r["ok"] is False and "prefijo" in r["detalle"], r["detalle"])

print("\n[4] detecta cadena rota (head != last_log_hash)")
_poblar(3, "H1", "h3-distinto", 3)
_checkpoint(1, 1, "H1")
r = MON.verificar_checkpoint()
check("4a. rechaza cadena rota", r["ok"] is False and "cadena" in r["detalle"], r["detalle"])

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
