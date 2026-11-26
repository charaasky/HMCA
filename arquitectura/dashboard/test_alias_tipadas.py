#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test P0-B: alias tipadas (arista alias:<surface> -> target) -> bloque de entidad.

Dictamen fable_propiedad_llm_fable.md: la ENTIDAD se resuelve determinista por aristas alias; la
PROPIEDAD es semántica (LLM). `resolve_alias` devuelve un BLOQUE de mundo cerrado (no un recall
directo de un solo hecho).

Cubre:
  1) alias tipada resuelve a bloque de entidad (guiya -> perra).
  2) sin arista alias no resuelve (gato sin alias).
  3) dos targets distintos al mismo tier => entidad no resuelta.
  4) verified le gana a claimed (y claimed sin verified que compita igual resuelve).
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
import memory_context as MC  # noqa: E402


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    MC.MEMORY_DB = db

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    m.write("perra:color", "negra", fuente="usuario", verified=True)
    m.write("perra:nombre", "Luna", fuente="usuario", verified=True)
    m.write("gato:color", "blanco", fuente="usuario", verified=True)
    m.crear_alias("guiya", "perra", fuente="usuario", verified=True)

    def keys(res):
        return {f["key"] for f in (res or {}).get("facts", [])}

    # 1) alias tipada resuelve a bloque de entidad
    dec = R.resolver("de que color era mi guiya", m, top_k=5)
    res = MC.resolve_alias("de que color era mi guiya", dec)
    check("alias guiya -> bloque de entidad perra",
          res is not None and res.get("tipo") == "entity_block"
          and res.get("entidad") == "perra" and "perra:color" in keys(res),
          f"res={res}")

    # 2) sin arista alias no resuelve (token en value no basta)
    m.write("perra:apodo", "guiya", fuente="usuario", verified=True)
    dec2 = R.resolver("de que color era mi gato", m, top_k=5)
    res2 = MC.resolve_alias("de que color era mi gato", dec2)
    check("sin arista alias no resuelve (gato sin alias)", res2 is None, f"res2={res2}")

    # 3) dos targets distintos mismo tier => entidad no resuelta
    m.crear_alias("luna", "gato", fuente="usuario", verified=True)
    dec3 = R.resolver("de que color es guiya y luna", m, top_k=5)
    res3 = MC.resolve_alias("de que color es guiya y luna", dec3)
    check("dos targets distintos -> entidad no resuelta", res3 is None, f"res3={res3}")

    # 4) verified le gana a claimed
    m.crear_alias("michina", "perra", fuente="extractor", verified=False)  # claimed
    dec4 = R.resolver("de que color era mi michina", m, top_k=5)
    res4 = MC.resolve_alias("de que color era mi michina", dec4)
    check("alias claimed resuelve (sin verified que compita)",
          res4 is not None and res4.get("entidad") == "perra" and "perra:color" in keys(res4),
          f"res4={res4}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ P0-B (alias tipadas) OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
