#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test P0.1 (gate de escritura de alias) + P1.1 (miss de alias ruidoso).

Dictamen Fable (fable_olvido_alias_fable.md):
  - P0.1: un write con forma de alias se enruta a crear_alias (arista), y se rechaza escribir a
    X / X:* si ya existe alias:X (no re-fragmentar).
  - P1.1: surface legacy sin arista se marca en la respuesta (hcma_alias_unresolved).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import monitor_fachada as MF  # noqa: E402
import monitor_multiplex as MM  # noqa: E402
import memory_context as MC  # noqa: E402


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    fachada = MF.MonitorFachada(m)
    MC.MEMORY_DB = db

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    # 0) crear_alias y retirar son ops HUMANAS (en GOBERNANZA_OPS, no del agente)
    check("crear_alias en GOBERNANZA_OPS", "crear_alias" in MM.GOBERNANZA_OPS)
    check("retirar en GOBERNANZA_OPS", "retirar" in MM.GOBERNANZA_OPS)

    # 1) P0.1: write con forma de alias se enruta a crear_alias (no a hecho bare)
    r = fachada.pedir({"tipo": "escribir_hecho", "key": "guiya", "value": "apodo de mi perra"})
    fact_alias = m.current_raw("alias:guiya")
    check("P0.1: escribe arista alias:guiya (no bare guiya)",
          r.get("ok") and fact_alias is not None and fact_alias["value"] == "perra",
          f"r={r} alias={fact_alias and fact_alias['value']!r}")
    check("P0.1: no escribe bare guiya", m.current("guiya") is None and m.current_raw("guiya") is None,
          f"bare={m.current_raw('guiya')}")

    # 2) P0.1: rechaza write a X:* si existe alias:X
    m.crear_alias("guiya", "perra", fuente="usuario", verified=True)
    r2 = fachada.pedir({"tipo": "escribir_hecho", "key": "guiya:color", "value": "negro"})
    check("P0.1: rechaza write a guiya:color (alias existente)",
          r2.get("ok") is False, f"r2={r2}")

    # 3) P1.1: helper detecta surface legacy (bare key con value 'apodo de E')
    sup = MC.superficie_alias_legacy([{"key": "guiya", "value": "apodo de mi perra"},
                                      {"key": "perra:color", "value": "negra"}])
    check("P1.1: superficie_alias_legacy detecta guiya", sup == "guiya", f"sup={sup!r}")
    sup2 = MC.superficie_alias_legacy([{"key": "perra:color", "value": "negra"}])
    check("P1.1: sin surface legacy -> None", sup2 is None, f"sup2={sup2!r}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ P0.1/P1.1 OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
