#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Tests de HCMAMemoriaSQLite: semántica HCMA (versiones, linaje, stale) + FTS + durabilidad."""
import os, sys, tempfile, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memoria_sqlite import HCMAMemoriaSQLite


def _tmp():
    return os.path.join(tempfile.mkdtemp(), "hcma.db")


def test_basico_y_busqueda():
    m = HCMAMemoriaSQLite(_tmp())
    v = m.write("color_perro", "gris", fuente="usuario", verified=True)
    assert v == 1, v
    assert m.current("color_perro")["value"] == "gris"
    # FTS: la memoria recuerda semánticamente
    res = m.buscar("de qué color es mi can")
    assert res and res[0]["key"] == "color_perro" and res[0]["value"] == "gris", res
    res2 = m.buscar("what color is my dog")
    assert res2 and res2[0]["value"] == "gris", res2
    m.close()
    print("  ✔ básico + búsqueda FTS")


def test_versionado_y_stale():
    m = HCMAMemoriaSQLite(_tmp())
    # input verificado
    m.write("in_masa_kg", 100.0, unit="kg", fuente="usuario", verified=True)
    f = m.current("in_masa_kg")
    # resultado derivado (calculadora) con linaje
    v2 = m.write("peso_N", 981.0, unit="N", fuente="calculadora", verified=True,
                 derived_from={"in_masa_kg": f["version"]})
    assert m.current("peso_N")["value"] == 981.0
    assert m.current("peso_N")["derived_from"] == {"in_masa_kg": f["version"]}
    # corregir el input → el derivado debe quedar stale y perder authority
    m.write("in_masa_kg", 120.0, unit="kg", fuente="usuario", verified=True)
    cur = m.current("peso_N")
    assert cur is None, f"peso_N debería haber perdido autoridad, cur={cur}"
    # el derivado viejo sigue existiendo pero stale
    cands = m.candidates("peso_N")
    assert cands and cands[0]["stale"] is True, cands
    m.close()
    print("  ✔ versionado + invalidación transitiva (stale)")


def test_noop_y_autoridad_unica():
    m = HCMAMemoriaSQLite(_tmp())
    v1 = m.write("x", 5, fuente="usuario", verified=True)
    v2 = m.write("x", 5, fuente="usuario", verified=True)  # no-op
    assert v1 == v2, (v1, v2)
    # solo una authority por key (el índice único parcial lo garantiza)
    n = m.conn.execute("SELECT COUNT(*) FROM facts WHERE key='x' AND authority=1").fetchone()[0]
    assert n == 1, n
    m.close()
    print("  ✔ no-op + autoridad única")


def test_durabilidad():
    path = _tmp()
    m = HCMAMemoriaSQLite(path)
    m.write("clave_durable", "valor", fuente="usuario", verified=True)
    m.close()
    # reabrir: el hecho debe persistir
    m2 = HCMAMemoriaSQLite(path)
    assert m2.current("clave_durable")["value"] == "valor"
    assert m2.buscar("clave durable")[0]["value"] == "valor"
    m2.close()
    print("  ✔ durabilidad (reabrir archivo)")


def test_migracion_desde_ram():
    from prototipo import HCMAMemoria
    ram = HCMAMemoria()
    ram.write("color", 1, fuente="usuario", verified=True)
    f = ram.current("color")
    ram.write("peso", 2, fuente="calculadora", verified=True, derived_from={"color": f["version"]})
    sql = HCMAMemoriaSQLite(_tmp())
    sql.migrar_desde_ram(ram)
    assert sql.current("color")["value"] == 1
    assert sql.current("peso")["value"] == 2
    assert sql.buscar("color")[0]["value"] == 1
    sql.close()
    print("  ✔ migración desde RAM")


if __name__ == "__main__":
    test_basico_y_busqueda()
    test_versionado_y_stale()
    test_noop_y_autoridad_unica()
    test_durabilidad()
    test_migracion_desde_ram()
    print("\nTODOS LOS TESTS PASARON")
