#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Tests del ancla externa (P0 A1): publica (seq, head_hash) fuera del store.

Invariantes:
  1. El ancla es append-only + hash-chained + .head anti-truncado (leer() detecta manipulación).
  2. write()/close() publican (seq, head_hash) en el ancla.
  3. verify() detecta truncado: si el store pierde la cola, su seq queda POR DEBAJO del ancla.
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memoria_sqlite import HCMAMemoriaSQLite
from ancla_publica import AnclaPublica


def _tmp():
    return tempfile.mkdtemp()


def test_ancla_append_only_e_integra():
    d = _tmp()
    a = AnclaPublica(os.path.join(d, "ancla.log"))
    a.anclar(1, "h1")
    a.anclar(2, "h2")
    entradas, integra = a.leer()
    assert integra and len(entradas) == 2
    assert a.ultimo()["seq"] == 2 and a.ultimo()["head_hash"] == "h2"
    # tamper: truncar el .head => integra False
    with open(os.path.join(d, "ancla.log.head"), "w") as f:
        f.write("deadbeef\n")
    _, integra2 = a.leer()
    assert integra2 is False
    print("  ✔ ancla append-only + hash-chained + .head anti-truncado")


def test_verify_detecta_truncado():
    d = _tmp()
    db = os.path.join(d, "hcma.db")
    ancla = os.path.join(d, "ancla.log")
    m = HCMAMemoriaSQLite(db, ancla_path=ancla)
    m.write("a", 1, fuente="usuario", verified=True)
    m.write("b", 2, fuente="usuario", verified=True)
    m.close()
    # reabrir: store intacto => no truncado
    m2 = HCMAMemoriaSQLite(db, ancla_path=ancla)
    r = m2.verify()
    assert r["ok"] is True and r["ancla"]["truncada"] is False, r
    # truncar la cola del store (borrar el último evento)
    m2.conn.execute("DELETE FROM facts_log WHERE seq=(SELECT MAX(seq) FROM facts_log)")
    m2.conn.commit()
    r2 = m2.verify()
    assert r2["ancla"]["truncada"] is True, r2
    assert any(p["tipo"] == "ancla_truncada" for p in r2["problemas"]), r2
    m2.close()
    print("  ✔ verify() detecta truncado del store vía ancla externa")


def test_sin_ancla_no_interfiere():
    d = _tmp()
    m = HCMAMemoriaSQLite(os.path.join(d, "hcma.db"))
    m.write("x", 1, fuente="usuario", verified=True)
    r = m.verify()
    assert r["ok"] is True and r["ancla"] is None, r
    m.close()
    print("  ✔ sin ancla configurada, verify() no interfiere")


if __name__ == "__main__":
    test_ancla_append_only_e_integra()
    test_verify_detecta_truncado()
    test_sin_ancla_no_interfiere()
    print("\nTODOS LOS TESTS DE ANCLA EXTERNA (A1) PASARON")
