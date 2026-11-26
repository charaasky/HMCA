#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de borrado por herencia aislado de un ORIGEN (derecho al olvido / PDF ingerido).

Cubre:
  1) arbol_documento muestra el árbol completo (raíz + descendientes transitivos).
  2) delete(raíz) invalida TODA la clausura transitiva (verified Y claimed), incluyendo
     hechos de OTRO usuario que consumió el origen bajo otra clave.
  3) NO toca información ajena (un hecho sin linaje al origen queda intacto).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    # raíz: documento ingerido (claim)
    v_doc = m.write("doc:pdf:abc123", {"filename": "test.pdf", "sha256": "abc123"},
                    fuente="chat:usuario", verified=False, meta={"usuario": "admin"})
    # hecho derivado del documento (usuario 1)
    v_a = m.write("color:perro", "negro", fuente="chat:usuario", verified=False,
                  meta={"usuario": "user1"}, derived_from={"doc:pdf:abc123": v_doc})
    # OTRO usuario consume A y lo guarda bajo otra clave (derivado de A)
    v_b = m.write("clave:otra", "negro", fuente="chat:usuario", verified=False,
                  meta={"usuario": "user2"}, derived_from={"color:perro": v_a})
    # hecho AJENO (sin linaje al documento)
    m.write("fisica:constante", 9.81, unit="m/s2", fuente="ingeniero", verified=True)

    # 1) árbol completo
    arbol = m.arbol_documento("doc:pdf:abc123")
    keys_arbol = {n["key"] for n in arbol["nodes"]}
    check("árbol completo (raíz + 2 derivados)",
          arbol["n_descendientes"] == 3 and "doc:pdf:abc123" in keys_arbol
          and "color:perro" in keys_arbol and "clave:otra" in keys_arbol,
          f"n={arbol['n_descendientes']} keys={keys_arbol}")

    # 2) borrado por herencia aislado
    m.delete("doc:pdf:abc123", autor="admin", motivo="orden judicial", categoria="legal")

    # los tres del árbol quedan stale (no vigentes)
    desc = m._descendientes_transitivos("doc:pdf:abc123")
    todos_stale = all(d["stale"] for d in desc) and len(desc) == 3
    check("borrado invalida toda la clausura transitiva", todos_stale,
          f"desc={[(d['key'], d['stale']) for d in desc]}")

    # el hecho ajeno sigue vigente
    ajena = m.current("fisica:constante")
    check("NO toca información ajena", ajena is not None and ajena["verified"] and not ajena["stale"],
          f"ajena={ajena}")

    # 3) denylist: re-escribir el hecho borrado (o similar) se rechaza citando el hash
    try:
        m.write("color:perro", "negro", fuente="chat:usuario", verified=False,
                meta={"usuario": "user3"})
        check("re-escritura de hecho borrado se rechaza", False)
    except ValueError as e:
        check("re-escritura de hecho borrado se rechaza", "revocado" in str(e), f"e={e}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ borrado por herencia aislado (documento) OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
