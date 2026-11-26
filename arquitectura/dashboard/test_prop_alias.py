#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Golden test del salto 2 de alias = BLOQUE DE MUNDO CERRADO (fable_propiedad_llm_fable.md).

La ENTIDAD es conocimiento PRIVADO y se resuelve determinista por aristas alias; la PROPIEDAD es
SEMÁNTICA (público, multi-idioma) y la selecciona el LLM consumidor. NO se materializa `prop:*`.

Cubre los 7 casos de Fable + 1 caso de generalidad:
  1) sinónimo ('llamaba'≈'nombre') -> bloque con perra:nombre, sin abstener.
  2) multi-idioma ('what was my guiya called') -> mismo bloque.
  3) determinismo byte-idéntico.
  4) propiedad ausente -> completo=true; validador rechaza used_key fuera del bloque.
  5) entidad no resuelta -> None (no_memory_block en el caller).
  6) falso positivo literal ('correa de mi guiya') -> bloque completo, no recall directo.
  7) truncado K+1 -> completo=false.
  8) generalidad: entidades ajenas a 'perra/guiya' (auto/coche, persona/jefa) — el mecanismo
     es el mismo porque la ENTIDAD es determinista y la PROPIEDAD es semántica, sin hardcode.
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


def _sembrar_basico(m):
    m.write("perra:nombre", "Luna", fuente="usuario", verified=True)
    m.write("perra:color", "negra", fuente="usuario", verified=True)
    m.crear_alias("guiya", "perra", fuente="usuario", verified=True)


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    MC.MEMORY_DB = db
    _sembrar_basico(m)

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    def alias(q, k=8):
        dec = R.resolver(q, m, top_k=5)
        return MC.resolve_alias(q, dec, k=k)

    # 1) sinónimo: 'llamaba'≈'nombre' -> bloque con perra:nombre, sin abstener.
    r1 = alias("como se llamaba mi guiya")
    check("1) sinónimo: bloque con perra:nombre (sin abstener)",
          r1 is not None and r1.get("tipo") == "entity_block"
          and r1.get("entidad") == "perra"
          and any(f["key"] == "perra:nombre" for f in r1["facts"]),
          f"r1={r1}")

    # 2) multi-idioma: mismo bloque.
    r2 = alias("what was my guiya called")
    check("2) multi-idioma: mismo bloque",
          r2 is not None and {f["key"] for f in r2["facts"]} == {f["key"] for f in r1["facts"]},
          f"r2={r2}")

    # 3) determinismo byte-idéntico.
    r3a = alias("como se llamaba mi guiya")
    r3b = alias("como se llamaba mi guiya")
    check("3) determinismo byte-idéntico",
          MC.entity_block(r3a, r3a["facts"]) == MC.entity_block(r3b, r3b["facts"]))

    # 4) propiedad ausente: completo=true; validador rechaza used_key fuera del bloque.
    r4 = alias("cuántos años tiene mi guiya")
    check("4) propiedad ausente -> completo=true",
          r4 is not None and r4.get("complete") is True
          and not any(f["key"] == "perra:edad" for f in r4["facts"]),
          f"r4={r4}")
    check("4) validador rechaza used_key fuera del bloque",
          MC.validate_used_key("perra:edad", r4) is False
          and MC.validate_used_key("perra:color", r4) is True
          and MC.validate_used_key(None, r4) is False
          and MC.validate_used_key("", r4) is False)

    # 5) entidad no resuelta -> None.
    r5 = alias("de que color es mi auto")
    check("5) entidad no resuelta -> None", r5 is None, f"r5={r5}")

    # 6) falso positivo literal: 'correa de mi guiya' -> bloque completo (no recall directo).
    r6 = alias("de que color es la correa de mi guiya")
    check("6) falso positivo literal: bloque completo (no recall directo)",
          r6 is not None and r6.get("tipo") == "entity_block"
          and r6.get("key") is None and len(r6["facts"]) >= 2
          and r6["facts"][0]["key"] == "perra:color",  # el literal ORDENA, no decide
          f"r6={r6}")

    m.close()
    os.remove(db)

    # 7) truncado K+1 -> completo=false (entidad con más hechos que K).
    db2 = _tmp()
    m2 = HCMAMemoriaSQLite(db2, crear_esquema=True)
    MC.MEMORY_DB = db2
    m2.write("perra:nombre", "Luna", fuente="usuario", verified=True)
    m2.write("perra:color", "negra", fuente="usuario", verified=True)
    m2.write("perra:edad", "7", fuente="usuario", verified=True)
    m2.crear_alias("guiya", "perra", fuente="usuario", verified=True)

    dec7 = R.resolver("de que color era mi guiya", m2, top_k=5)
    r7 = MC.resolve_alias("de que color era mi guiya", dec7, k=2)
    check("7) truncado K+1 -> completo=false",
          r7 is not None and r7.get("complete") is False
          and len(r7["facts"]) == 2 and r7.get("total") == 3,
          f"r7={r7}")

    m2.close()
    os.remove(db2)

    # 8) generalidad: entidades ajenas a 'perra/guiya' (auto/coche, persona/jefa). Mismo
    #    mecanismo: ENTIDAD determinista por alias, PROPIEDAD semántica por LLM, sin hardcode.
    db3 = _tmp()
    m3 = HCMAMemoriaSQLite(db3, crear_esquema=True)
    MC.MEMORY_DB = db3
    m3.write("auto:color", "rojo", fuente="usuario", verified=True)
    m3.write("auto:puertas", "5", fuente="usuario", verified=True)
    m3.crear_alias("coche", "auto", fuente="usuario", verified=True)
    m3.write("persona:nombre", "Ana", fuente="usuario", verified=True)
    m3.write("persona:altura", "170", fuente="usuario", verified=True)
    m3.crear_alias("jefa", "persona", fuente="usuario", verified=True)

    def alias2(q):
        return MC.resolve_alias(q, R.resolver(q, m3, top_k=5))

    def keys2(r):
        return {f["key"] for f in (r or {}).get("facts", [])}

    ra = alias2("como se llamaba mi jefa")
    check("8) generalidad: sinónimo semántico (llamaba -> persona:nombre)",
          ra is not None and ra.get("entidad") == "persona"
          and "persona:nombre" in keys2(ra) and "persona:altura" in keys2(ra),
          f"ra={ra}")

    rb = alias2("de que color era mi coche")
    check("8) generalidad: propiedad directa (auto:color, literal primero)",
          rb is not None and rb.get("entidad") == "auto"
          and "auto:color" in keys2(rb) and rb["facts"][0]["key"] == "auto:color",
          f"rb={rb}")

    rc = alias2("qué motor tiene mi coche")
    check("8) generalidad: propiedad ausente (completo=true, validador rechaza auto:motor)",
          rc is not None and rc.get("entidad") == "auto" and rc.get("complete") is True
          and "auto:motor" not in keys2(rc)
          and MC.validate_used_key("auto:motor", rc) is False
          and MC.validate_used_key("auto:color", rc) is True,
          f"rc={rc}")

    m3.close()
    os.remove(db3)

    print("\nRESULTADO:", "✔ prop-alias (bloque de entidad) OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
