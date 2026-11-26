#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""M4 — concurrencia: escritores concurrentes sobre la misma memoria SQLite.

SQLite es single-writer (los escritores se serializan con BEGIN IMMEDIATE + busy_timeout).
Este test verifica que bajo CONTENCIÓN concurrente: (1) no hay corrupción, (2) todas las
escrituras aterrizan, (3) los invariantes se mantienen (autoridad única garantizada por el
índice único parcial a nivel DB, no por el código).
"""
import os, sys, tempfile, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memoria_sqlite import HCMAMemoriaSQLite

N_THREADS = 8
WRITES_POR_HILO = 40


def _worker(path, tid, resultados):
    h = HCMAMemoriaSQLite(path, crear_esquema=False)
    try:
        for i in range(WRITES_POR_HILO):
            # pares: claves compartidas (contienden); impares: claves propias
            key = f"k_{i % 5}" if i % 2 == 0 else f"t{tid}_{i}"
            h.write(key, float(tid * 1000 + i), fuente="usuario", verified=True)
        resultados[tid] = True
    except Exception as e:
        resultados[tid] = f"error: {e}"
    finally:
        h.close()


def main():
    path = os.path.join(tempfile.mkdtemp(), "conc.db")
    base = HCMAMemoriaSQLite(path)  # crea el esquema una sola vez
    base.close()

    t0 = time.time()
    resultados = {}
    hilos = [threading.Thread(target=_worker, args=(path, t, resultados))
             for t in range(N_THREADS)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    t_total = time.time() - t0

    fallos = [r for r in resultados.values() if r is not True]
    if fallos:
        print(f"✗ fallos en hilos: {fallos[:3]}")
        return 1

    chk = HCMAMemoriaSQLite(path, crear_esquema=False)
    n = chk.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    integridad = chk.conn.execute("PRAGMA integrity_check").fetchone()[0]
    dup = chk.conn.execute(
        "SELECT key FROM facts WHERE authority=1 GROUP BY key HAVING COUNT(*)>1").fetchall()
    n_authority = chk.conn.execute(
        "SELECT COUNT(*) FROM facts WHERE authority=1").fetchone()[0]
    chk.close()

    esperado = N_THREADS * WRITES_POR_HILO
    print(f"Escritores concurrentes: {N_THREADS} hilos × {WRITES_POR_HILO} escrituras")
    print(f"  total escrituras en facts: {n} (esperado {esperado})")
    print(f"  hechos vigentes (authority=1): {n_authority}")
    print(f"  integridad (PRAGMA integrity_check): {integridad}")
    print(f"  claves con >1 authority: {len(dup)}")
    print(f"  tiempo total (serializado por SQLite): {t_total:.2f}s")
    assert n == esperado and integridad == "ok" and not dup
    print("\n✅ M4 CONCURRENCIA OK: sin corrupción, todas las escrituras, invariantes intactas.")


if __name__ == "__main__":
    main()
