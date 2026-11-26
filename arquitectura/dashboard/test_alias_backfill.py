#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Golden test del backfill de alias (dictamen Fable: fable_olvido_alias_fable.md).

Fixture legacy -> detección -> backfill -> retiro del namespace -> invariantes -> recall:
  'de que color era mi guiya' debe resolver a perra:color = negra (RECALL_PURO por alias).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import alias_backfill as AB  # noqa: E402
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

    m.write("perra:nombre", "Luna", fuente="usuario", verified=True)
    m.write("perra:color", "negra", fuente="usuario", verified=True)
    m.write("guiya", "apodo de mi perra", fuente="usuario", verified=True)
    m.write("guiya:apodo", "de mi perra", fuente="usuario", verified=True)
    m.write("guiya:es_perra", "sí", fuente="usuario", verified=True)
    m.write("perro:apodo", "guiya", fuente="usuario", verified=True)

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    # 1) detección
    cands = AB.detectar_aliases_legacy(m)
    guiya = next((c for c in cands if c["surface"] == "guiya"), None)
    check("detecta surface guiya", guiya is not None)
    check("conflicto perra vs perro", guiya is not None and guiya["conflicto"]
          and guiya["target_mayoritario"] == "perra", f"votos={guiya and guiya['votos']}")

    # 2) P2: alias por nombre (luna -> perra)
    por_nombre = AB.detectar_alias_por_nombre(m)
    check("P2 detecta luna -> perra",
          any(c["surface"] == "luna" and c["target"] == "perra" for c in por_nombre),
          f"por_nombre={por_nombre}")

    # 3) backfill + retiro del namespace legacy
    AB.backfill(m, {"guiya": "perra", "luna": "perra"})
    check("backfill crea arista guiya -> perra", m.alias_target("guiya") == ("perra", "verified"))
    check("backfill crea arista luna -> perra", m.alias_target("luna") == ("perra", "verified"))
    retirados = AB.retirar_legacy(m, ["guiya"])
    check("retiro del namespace guiya", len(retirados) >= 3, f"retirados={retirados}")
    check("bare guiya retirado (stale)", m.current("guiya") is None)

    # 4) invariantes I1-I4 sin violaciones (tras backfill + retiro)
    violaciones = AB.validar_invariantes_alias(m)
    check("invariantes sin violaciones", violaciones == [], f"violaciones={violaciones}")

    # 5) recall por alias -> bloque de entidad (advisory, no recall directo)
    dec = R.resolver("de que color era mi guiya", m, top_k=5)
    res = MC.resolve_alias("de que color era mi guiya", dec)
    keys = {f["key"] for f in (res or {}).get("facts", [])}
    check("alias resuelve a bloque de entidad con perra:color",
          res is not None and res.get("tipo") == "entity_block"
          and res.get("entidad") == "perra" and "perra:color" in keys,
          f"res={res}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ backfill alias OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
