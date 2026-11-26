#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de contrato del gate de recall (hcma_recall) — golden set determinista.

Cubre el bug que Fable marcó: el sistema debe responder desde el hipocampo (sin LLM) cuando la
consulta es un recall factual, y NO cortar con overlap débil (opinión, entidad ajena, solo value).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import hcma_recall as R  # noqa: E402


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    m.write("color_perro", "gris", fuente="usuario", verified=True)
    m.write("motor.potencia", 1500, unit="W", fuente="ingeniero", verified=True)
    m.write("peso_motor", 42.5, unit="kg", fuente="ingeniero", verified=True)
    m.write("perra:apodo", "guiya", fuente="usuario", verified=True)
    m.write("fuente:teoria_complejidad", "2-SAT decidible en tiempo polinomial",
            fuente="ingeniero", verified=True)

    casos = [
        # (query, esperado)  — RECALL_PURO es lo que corta sin LLM
        ("de qué color es mi perro", "RECALL_PURO"),
        ("de qué color es mi perra", "RECALL_PURO"),          # género: perra/perro
        ("cuánto pesa el motor", "RECALL_PURO"),               # peso_motor
        ("¿Qué opinás del gris para un perro?", "NO_RECALL"),  # opinión (residuo)
        ("el perro de Juan", "NO_RECALL"),                     # entidad ajena (residuo)
        ("color del auto", "NO_RECALL"),                       # sin key de auto
        ("hola", "NO_RECALL"),                                 # solo stopwords
    ]

    ok = True
    for q, esperado in casos:
        d = R.resolver(q, m, top_k=5)
        got = d["tipo"] if d["tipo"] == "RECALL_PURO" else "NO_RECALL"
        pass_ = (got == esperado)
        ok &= pass_
        print(f"  [{'OK' if pass_ else 'FALLA'}] {q!r} -> {d['tipo']} (esperado {esperado})")
        if not pass_:
            print("       evidencia:", d.get("evidencia"))

    # Overlap por VALUE (no por KEY): preguntar por el CONTENIDO de un hecho debe devolverlo como
    # contexto (no RECALL_PURO, pero sí en hechos), para que el LLM pueda responder
    # "guiya era el apodo de tu perra". Antes esto se descartaba (m<1 en la KEY).
    d_val = R.resolver("quién era mi guiya", m, top_k=5)
    keys_val = [f["key"] for f in d_val.get("hechos", [])]
    val_ok = (d_val["tipo"] != "RECALL_PURO") and ("perra:apodo" in keys_val)
    ok &= val_ok
    print(f"  [{'OK' if val_ok else 'FALLA'}] value-overlap 'quién era mi guiya' -> {d_val['tipo']} hechos={keys_val}")
    if not val_ok:
        print("       evidencia:", d_val.get("evidencia"))

    # Solapamiento DÉBIL (1 token de una key larga): NO debe inyectar ruido. Antes 'teoria' hacía
    # que el LLM contestara 'no sé' a 'relatividad general' por arrastrar 'fuente:teoria_complejidad'.
    d_deb = R.resolver("cual es la formula de la teoria de la relatividad general", m, top_k=5)
    deb_ok = (d_deb["tipo"] == "NO_HIT") and (len(d_deb.get("hechos", [])) == 0)
    ok &= deb_ok
    print(f"  [{'OK' if deb_ok else 'FALLA'}] weak-overlap 'relatividad general' -> {d_deb['tipo']} hechos={[f['key'] for f in d_deb.get('hechos', [])]}")
    if not deb_ok:
        print("       evidencia:", d_deb.get("evidencia"))

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ todos los casos pasan" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
