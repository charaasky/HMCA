#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_checkpoint_logico.py — P1: el control fail-closed valida estado LÓGICO, no solo digest.

Dictamen Fable (fable_wal_fantasma_fable.md, P1): el monitor atestiguaba bytes del .db pero
operaba sobre .db + WAL. Un -wal ajeno pisaba los facts sin alterar el digest del .db. El fix:
verificar `n_facts`, `n_facts_log`, `head_facts_log` y la integridad de la cadena facts_log.

Invariantes:
  [1] `checkpoint_cutover.head_facts_log` se calcula por `seq` (AUTOINCREMENT), no por `version`
      (que es -1 para ops de nonce/consumir/log_op). Un log_op posterior no debe romper el head.
  [2] `hcma_monitor_daemon._estado_logico` devuelve n_facts/n_facts_log/head coherentes con el
      checkpoint y con last_log_hash (cadena íntegra).
  [3] `verificar_checkpoint` rechaza (ok=False) si n_facts difiere del checkpoint (simula el
      WAL fantasma sin tocar el digest del archivo).
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


tmpdir = tempfile.mkdtemp(prefix="hcma_p1_")
STORE = os.path.join(tmpdir, "memoria.db")


def _poblar():
    from memoria_sqlite import HCMAMemoriaSQLite
    m = HCMAMemoriaSQLite(STORE)
    m.write("skill:a:spec", {"x": 1}, unit="", fuente="ingeniero")
    m.write("skill:b:spec", {"y": 2}, unit="", fuente="ingeniero")
    # log_op escribe en facts_log con version=-1 (NO un fact): debe ser el head real de la cadena
    m.log_op("incidente.registrar", key="incidente:i", payload={"modo": "wal_fantasma"})
    m.close()
    return m


print("[1] checkpoint_cutover calcula head_facts_log por seq (no por version)")
import checkpoint_cutover as CC  # noqa: E402
_poblar()
cp = CC.calcular_checkpoint(STORE)
check("1a. n_facts == 2", cp.get("n_facts") == 2, cp.get("n_facts"))
# 2 writes + 1 marcador payload_ver (emite crear_esquema) + 1 log_op
check("1b. n_facts_log == 4 (2 writes + 1 payload_ver + 1 log_op)",
      cp.get("n_facts_log") == 4, cp.get("n_facts_log"))

# el head REAL es el hash del log_op (último seq), no el del último write (último version)
import sqlite3  # noqa: E402
conn = sqlite3.connect(f"file:{STORE}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
head_by_seq = conn.execute("SELECT hash FROM facts_log ORDER BY seq DESC LIMIT 1").fetchone()["hash"]
head_by_ver = conn.execute("SELECT hash FROM facts_log ORDER BY version DESC LIMIT 1").fetchone()["hash"]
last = conn.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()["v"]
conn.close()
check("1c. head_facts_log == head_by_seq", cp.get("head_facts_log") == head_by_seq,
      f"{cp.get('head_facts_log')[:12]} != {head_by_seq[:12]}")
check("1d. head_by_seq == last_log_hash (cadena íntegra)", head_by_seq == last)
check("1e. head_by_seq != head_by_version (el fix importa)", head_by_seq != head_by_ver,
      "sin log_op version=-1 posterior, no se distingue")

print("\n[2] hcma_monitor_daemon._estado_logico coincide con el checkpoint")
import hcma_monitor_daemon as MON  # noqa: E402
MON.STORE_PATH = STORE
s = MON._estado_logico()
check("2a. n_facts coincide", s["n_facts"] == cp["n_facts"], s["n_facts"])
check("2b. n_facts_log coincide", s["n_facts_log"] == cp["n_facts_log"], s["n_facts_log"])
check("2c. head_facts_log coincide", s["head_facts_log"] == cp["head_facts_log"],
      s["head_facts_log"][:12])
check("2d. cadena íntegra (head==last_log_hash)", s["head_facts_log"] == s["last_log_hash"])

print("\n[3] verificar_checkpoint rechaza si el estado LÓGICO difiere del checkpoint")
# simular WAL fantasma: el .db tiene 2 facts, pero el checkpoint dice 130 (o viceversa). Acá
# forzamos una divergencia de n_facts sin tocar el digest: añadimos un fact y dejamos el
# checkpoint viejo (n_facts=2). El digest del .db cambió, pero la PRUEBA apunta a que la
# verificación LÓGICA (n_facts) es la que debe fallar primero al haber divergencia.
import json  # noqa: E402
import hashlib  # noqa: E402


def _digest(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# checkpoint correcto contra el estado actual
CKP_DIR = tempfile.mkdtemp(prefix="hcma_p1_ckp_")
CKP_JSON = os.path.join(CKP_DIR, "checkpoint.json")
CKP_SIG = os.path.join(CKP_DIR, "checkpoint.sig")
ok_ckp = dict(cp)
ok_ckp["store_digest"] = _digest(STORE)
json.dump(ok_ckp, open(CKP_JSON, "w"))
open(CKP_SIG, "w").write("firma")
MON.CKP_JSON = CKP_JSON  # parche: verificar_checkpoint usa constantes locales; se sobreescriben
MON.CKP_SIG = CKP_SIG
r_ok = MON.verificar_checkpoint()
check("3a. verifica OK con checkpoint coherente", r_ok["ok"] is True, r_ok["detalle"])

# divergencia de n_facts: re-poblamos con UN fact más y firmamos checkpoint con n_facts=999
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
m = HCMAMemoriaSQLite(STORE)
m.write("skill:c:spec", {"z": 3}, unit="", fuente="ingeniero")
m.close()
ok_ckp_bad = dict(CC.calcular_checkpoint(STORE))
ok_ckp_bad["n_facts"] = 999  # forzar divergencia lógica
ok_ckp_bad["store_digest"] = _digest(STORE)
json.dump(ok_ckp_bad, open(CKP_JSON, "w"))
open(CKP_SIG, "w").write("firma")
r_bad = MON.verificar_checkpoint()
check("3b. rechaza con n_facts divergente (ok=False)", r_bad["ok"] is False, r_bad["detalle"])
check("3c. el motivo cita n_facts", "n_facts" in r_bad["detalle"], r_bad["detalle"])

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
