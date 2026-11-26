#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de slug canónico multi-idioma (P0-1 Fable: no colapsar, normalizar, deduplicar).

Cubre:
  - latino puro: slug legacy ASCII sin cambio.
  - no-latino puro: `sk_<sha256(canon)>` determinista, sin colisión entre idiomas.
  - MIXTO (CJK + ASCII): va a hash, NO colapsa a 'pdf' (era la colisión residual).
  - normalización: canon() hace NFC == NFD (fullwidth/espacios convergen).
  - vacío/emoji: None (abstenerse, no acuñar id).
  - consistencia monitor._slug_skill <-> router._slug.
  - integración: el monitor persiste bajo id hash y rechaza contenido no identificable.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

import slug_canon as SC  # noqa: E402
import router_skills as RS  # noqa: E402
import monitor_fachada as MF  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402


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

    # 1) latino puro: slug legacy sin cambio
    check("latino: slug legacy ASCII",
          RS._slug("diseñame un control de tv") == "dise_ame_un_control_de_tv",
          repr(RS._slug("diseñame un control de tv")))

    # 2) no-latino puro: hash determinista, no 'skill', sin colisión
    zh = "帮我设计一个电视遥控器"
    ar = "تصميم جهاز تحكم عن بعد للتلفاز"
    hi = "दूरदर्शन रिमोट डिज़ाइन करें"
    s_zh = RS._slug(zh)
    check("no-latino: hash no colapsa a 'skill'", s_zh != "skill" and s_zh.startswith("sk_"), repr(s_zh))
    check("no-latino: determinista", RS._slug(zh) == s_zh, repr(s_zh))
    check("no-latino: sin colisión entre idiomas",
          len({RS._slug(zh), RS._slug(ar), RS._slug(hi)}) == 3,
          f"zh={RS._slug(zh)} ar={RS._slug(ar)} hi={RS._slug(hi)}")

    # 3) MIXTO: CJK + ASCII -> hash, no colapsa al token ASCII (P0-1)
    m1 = SC.slug("设计 PDF 解析器")
    m2 = SC.slug("合并 PDF 文件")
    check("mixto: no colapsa a 'pdf'", m1 != "pdf" and m2 != "pdf" and m1 != m2,
          f"m1={m1!r} m2={m2!r}")
    check("mixto: va a hash", m1.startswith("sk_") and m2.startswith("sk_"), f"m1={m1!r}")

    # 4) normalización: canon() hace NFC == NFD (y fullwidth/espacios convergen)
    check("canon: NFC == NFD", SC.canon("café") == SC.canon("cafe\u0301"),
          f"{SC.canon('café')!r} vs {SC.canon('cafe\u0301')!r}")
    check("canon: fullwidth/espacios convergen",
          SC.canon("ｃｏｎｔｒｏｌ　ｔｖ") == SC.canon("control tv"),
          f"{SC.canon('ｃｏｎｔｒｏｌ　ｔｖ')!r}")

    # 5) vacío/emoji: None (abstenerse)
    check("slug vacío -> None", SC.slug("") is None)
    check("slug emoji -> None", SC.slug("😀") is None)
    check("slug puntuación -> None", SC.slug("...") is None)

    # 6) consistencia monitor <-> router
    for q in ("diseñame un control de tv", zh, ar, hi, "设计 PDF 解析器", "Control de TV"):
        check(f"monitor._slug_skill == router._slug para {q!r}",
              MF._slug_skill(q) == RS._slug(q),
              f"{MF._slug_skill(q)!r} vs {RS._slug(q)!r}")

    # 7) integración: persistencia bajo id hash + rechazo de contenido no identificable
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    fachada = MF.MonitorFachada(m)
    r = fachada.pedir({"tipo": "crear_skill", "contexto": zh})
    sid = r.get("skill_id")
    check("monitor crea skill no-latino con id hash",
          r.get("ok") and sid and sid.startswith("sk_") and sid != "skill", f"r={r}")
    fact = m.current_raw("skill:" + sid)
    check("skill no-latino persistido bajo id hash",
          fact is not None and fact["key"] == "skill:" + sid and fact["verified"] is False,
          f"key={fact and fact['key']}")
    r2 = fachada.pedir({"tipo": "crear_skill", "contexto": "😀"})
    check("monitor rechaza contexto no identificable", r2.get("ok") is False, f"r2={r2}")
    m.close()
    os.remove(db)

    print("\nRESULTADO:", "✔ slug multi-idioma (P0-1) OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
