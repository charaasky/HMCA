#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Escala (pendiente 3): memoria HCMA con miles de hechos + invalidación fan-out.

Mide: carga, state()/FTS a escala, y la invalidación transitiva cuando un input del que
dependen miles de derivados se corrige (fan-out).
"""
import os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memoria_sqlite import HCMAMemoriaSQLite, _enc


def _bulk_fanout(db, n):
    """Crea 1 input + n derivados (todos con linaje al input) vía SQL directo (rápido).
    Los triggers de FTS sincronizan facts_fts automáticamente."""
    c = db.conn
    c.execute("BEGIN IMMEDIATE")
    c.execute("INSERT INTO facts(version,key,value,unit,verified,authority,stale,fuente,meta,derived_from) "
              "VALUES(1,'in_x',?, '',1,1,0,'usuario','{}','{}')", (_enc(100.0),))
    for i in range(n):
        v = i + 2
        c.execute("INSERT INTO facts(version,key,value,unit,verified,authority,stale,fuente,meta,derived_from) "
                  "VALUES(?,?,?, '',1,1,0,'calculadora','{}',?)",
                  (v, f"d_{i}", _enc(float(i)), _enc({"in_x": 1})))
        c.execute("INSERT INTO lineage(child_version,dep_key,dep_version) VALUES(?,?,?)",
                  (v, "in_x", 1))
    c.execute("COMMIT")


def main():
    n = 10000
    db = HCMAMemoriaSQLite(os.path.join(tempfile.mkdtemp(), "escala.db"))

    t0 = time.time()
    _bulk_fanout(db, n)
    print(f"1) carga fan-out: {n} derivados + 1 input en {time.time()-t0:.2f}s")

    t0 = time.time()
    st = db.state()
    print(f"2) state(): {len(st)} hechos vigentes en {(time.time()-t0)*1000:.1f}ms")

    t0 = time.time()
    r = db.buscar("d_5000")
    print(f"3) FTS buscar 'd_5000': {r[0]['key'] if r else 'sin match'} en {(time.time()-t0)*1000:.1f}ms")

    t0 = time.time()
    db.write("in_x", 200.0, fuente="usuario", verified=True)
    t_inv = time.time() - t0
    vigentes = len(db.state())
    stale = db.conn.execute("SELECT COUNT(*) FROM facts WHERE stale=1").fetchone()[0]
    print(f"4) corrección in_x: {t_inv*1000:.1f}ms → {stale} derivados stale, vigentes={vigentes}")
    assert vigentes == 1 and stale == n, (vigentes, stale)

    # estado del DB en disco
    size = os.path.getsize(db.path)
    db.close()
    print(f"5) tamaño del archivo SQLite: {size/1024:.0f} KB")
    print("\n✅ ESCALA OK: 10k hechos, invalidación fan-out correcta y rápida.")


if __name__ == "__main__":
    main()
