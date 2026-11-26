#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de los P1 multi-idioma (Fable): alias Unicode + fallback, cuarentena skill:skill,
metadatos de slug.

Cubre:
  1) alias `_norm_slug` preserva Unicode (no colapsa 'PDF 合并' a 'pdf').
  2) alias lectura con fallback LEGACY (alias viejo sin acentos/ASCII sigue resolviendo).
  3) `_raw_words` (query) extrae palabras Unicode (scripts no latinos).
  4) `_skill_ids` pone en cuarentena el id degenerado `skill`.
  5) metadatos de slug (slug_algo / slug_origen) en crear_skill y _op_crear_skill.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite, _norm_slug, _norm_slug_legacy  # noqa: E402
import router_skills as RS  # noqa: E402
import memory_context as MC  # noqa: E402
import monitor_fachada as MF  # noqa: E402


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def main():
    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    # 1) alias surface Unicode: no colapsa mixto a 'pdf'
    check("alias: PDF 合并 != PDF 拆分", _norm_slug("PDF 合并") != _norm_slug("PDF 拆分"),
          f"{_norm_slug('PDF 合并')!r} vs {_norm_slug('PDF 拆分')!r}")
    check("alias: preserva CJK", _norm_slug("PDF 合并") == "pdf_合并", repr(_norm_slug("PDF 合并")))
    check("alias: preserva tilde latina", _norm_slug("qué") == "qué", repr(_norm_slug("qué")))

    # 2) fallback legacy en lectura
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    m.write("alias:que", "perra", fuente="usuario", verified=True, meta={"tipo": "alias", "target": "perra"})
    check("alias: lectura legacy (qué -> que)", m.alias_target("qué") == ("perra", "verified"),
          f"{m.alias_target('qué')!r}")
    check("alias: surface nueva directa", m.alias_target("que") == ("perra", "verified"),
          f"{m.alias_target('que')!r}")

    # 3) _raw_words Unicode (query no-latina)
    MC.MEMORY_DB = db
    words = MC._raw_words("que es la 电视")
    check("raw_words: extrae CJK", "电视" in words and "que" in words, f"words={words}")

    # 4) cuarentena skill:skill
    m.write("skill:skill", "degenerado", fuente="ingeniero", verified=True)
    m.write("skill:control_tv", "spec", fuente="ingeniero", verified=True)
    ids = RS._skill_ids(m)
    check("skill:skill en cuarentena", "skill" not in ids and "control_tv" in ids, f"ids={ids}")

    # 5) metadatos de slug
    fachada = MF.MonitorFachada(m)
    r = fachada.pedir({"tipo": "crear_skill", "contexto": "control de tv"})
    fact = m.current_raw("skill:control_de_tv")
    check("slug_algo persistido", fact is not None and fact["meta"].get("slug_algo") == 2,
          f"meta={fact and fact['meta']}")
    check("slug_origen legacy", fact is not None and fact["meta"].get("slug_origen") == "legacy",
          f"meta={fact and fact['meta']}")
    r2 = fachada.pedir({"tipo": "crear_skill", "contexto": "帮我设计电视遥控器"})
    fact2 = m.current_raw("skill:" + r2["skill_id"])
    check("slug_origen hash", fact2 is not None and fact2["meta"].get("slug_origen") == "hash",
          f"meta={fact2 and fact2['meta']}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ P1 multi-idioma OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
