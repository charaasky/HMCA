#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Tests de integridad de la cadena (verify + marcador de payload v1→v2).

Invariantes:
  1. Una cadena sana pasa verify() (ok=True).
  2. Editar un hash de facts_log => cadena_rota.
  3. Escribir un payload pv=1 después del marcador => downgrade_payload.
  4. Divergencia fact.value ↔ log.val => fact_payload_divergente.
  5. El marcador payload_ver se emite una sola vez (idempotente).
"""
import os, sys, tempfile, json, hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memoria_sqlite import HCMAMemoriaSQLite


def _tmp():
    return os.path.join(tempfile.mkdtemp(), "hcma.db")


def test_verify_sano():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("x", 1, fuente="usuario", verified=True)
    m.write("y", 2, fuente="calculadora", verified=True, derived_from={"x": 1})
    r = m.verify()
    assert r["ok"] is True, r
    assert r["seq_total"] >= 3  # marcador + 2 writes
    m.close()
    print("  ✔ verify() sano")


def test_cadena_rota_por_tamper():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("x", 1, fuente="usuario", verified=True)
    m.conn.execute("UPDATE facts_log SET hash='deadbeef' WHERE seq=1")
    m.conn.commit()
    r = m.verify()
    assert r["ok"] is False
    assert any(p["tipo"] == "cadena_rota" for p in r["problemas"]), r
    m.close()
    print("  ✔ tamper de hash detectado (cadena_rota)")


def test_downgrade_payload_detectado():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("a", 1, fuente="usuario", verified=True)
    # inyectar un write pv=1 (sin procedencia) tras el marcador
    prev = m.conn.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()[0]
    canon = json.dumps({"pv": 1, "v": 99, "k": "mal", "val": 1}, sort_keys=True)
    h = hashlib.sha256((prev + "|" + canon).encode()).hexdigest()
    m.conn.execute("INSERT INTO facts_log(ts,op,key,version,payload,hash) VALUES(?,?,?,?,?,?)",
                   ("2026-11-22T00:00:00Z", "write", "mal", 99, canon, h))
    m.conn.execute("INSERT INTO hcma_meta(k,v) VALUES('last_log_hash',?) "
                   "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (h,))
    m.conn.execute("INSERT INTO facts(version,key,value,unit,verified,authority,stale,fuente,meta,derived_from) "
                   "VALUES(99,'mal','1','',0,0,0,'base','{}','{}')")
    m.conn.commit()
    r = m.verify()
    assert r["ok"] is False
    assert any(p["tipo"] == "downgrade_payload" for p in r["problemas"]), r
    m.close()
    print("  ✔ downgrade payload pv=1 detectado")


def test_divergencia_fact_log():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("x", 1, fuente="usuario", verified=True)
    # editar el valor del fact in-place (sin tocar el log)
    m.conn.execute("UPDATE facts SET value='999' WHERE key='x'")
    m.conn.commit()
    r = m.verify()
    assert r["ok"] is False
    assert any(p["tipo"] == "fact_payload_divergente" for p in r["problemas"]), r
    m.close()
    print("  ✔ divergencia fact↔log detectada")


def test_marcador_idempotente():
    m = HCMAMemoriaSQLite(_tmp())
    n1 = m.conn.execute("SELECT COUNT(*) FROM facts_log WHERE op='payload_ver'").fetchone()[0]
    m.marcar_payload_ver()
    n2 = m.conn.execute("SELECT COUNT(*) FROM facts_log WHERE op='payload_ver'").fetchone()[0]
    assert n1 == 1 and n2 == 1, (n1, n2)
    m.close()
    print("  ✔ marcador payload_ver idempotente")


if __name__ == "__main__":
    test_verify_sano()
    test_cadena_rota_por_tamper()
    test_downgrade_payload_detectado()
    test_divergencia_fact_log()
    test_marcador_idempotente()
    print("\nTODOS LOS TESTS DE INTEGRIDAD (verify) PASARON")
