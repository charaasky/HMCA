#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test P0 del router: NO MATCH = ABSTENER (nunca CREAR) + señal explícita (skill / crear_skill).

Valida el dictamen Fable 5.2 (fable_router_diseno_fable.md):
  1) Sin skill y sin índice, elegir_skill devuelve ABSTENER, no CREAR.
  2) Una consulta general (sin señal explícita) cae a LLM advisory (no fuerza HCMA).
  3) `body.skill` (señal explícita de uso) ejecuta/inyecta ese skill.
  4) `body.crear_skill` (señal explícita de creación) crea candidata autor=modelo.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import router_skills as RS  # noqa: E402
import memory_context as MC  # noqa: E402
import llm_gateway as LG  # noqa: E402


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def _silenciar_llm():
    LG._forward_openai = lambda *a, **k: {"choices": [{"message": {"role": "assistant", "content": "RESPUESTA_LLM"}}], "usage": {}}
    LG._forward_anthropic = lambda *a, **k: {"content": [{"type": "text", "text": "RESPUESTA_LLM"}], "usage": {}}
    LG._forward_google = lambda *a, **k: {"candidates": [{"content": {"parts": [{"text": "RESPUESTA_LLM"}]}}], "usageMetadata": {}}
    LG._conn = lambda: ("https://openrouter.ai/api/v1", "test-key", "openrouter")


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    MC.MEMORY_DB = db
    _silenciar_llm()

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    # 1) no match -> abstener (nunca crear)
    sk = RS.elegir_skill(m, "diseñame una lampara led", indice=None)
    check("no match -> abstener (no crear)", sk[0] == "abstener", f"sk={sk}")

    # 2) consulta general sin señal explícita -> LLM advisory (no fuerza HCMA)
    r = LG.chat_completions({"model": "test-model",
                             "messages": [{"role": "user", "content": "diseñame una lampara led"}],
                             "hcma_memory": True})
    check("general sin señal -> LLM advisory", r.get("procedencia") == "llm_unverified",
          f"proc={r.get('procedencia')}")

    # 3) señal explícita de uso: body.skill -> ejecuta skill declarativo
    m.write("motor.potencia", 1500, unit="W", fuente="ingeniero", verified=True)
    m.write("motor.tension", 12, unit="V", fuente="ingeniero", verified=True)
    m.write("skill:corriente:spec",
            {"inputs": {"P": {"key": "motor.potencia"}, "V": {"key": "motor.tension"}},
             "formulas": [{"name": "I", "expr": "P / V", "unit": "A"}]},
            fuente="ingeniero", verified=True)
    r3 = LG.chat_completions({"model": "test-model",
                              "messages": [{"role": "user", "content": "calcula la corriente"}],
                              "hcma_memory": True, "skill": "corriente"})
    check("body.skill ejecuta skill declarativo", r3.get("procedencia") == "skill"
          and r3.get("hcma_skill", {}).get("nivel") == "explicito",
          f"proc={r3.get('procedencia')} skill={r3.get('hcma_skill')}")

    # 4) señal explícita de creación: body.crear_skill -> candidata (monitor mockeado)
    LG._write_monitor = lambda op: {"ok": True, "skill_id": "disena_lampara_led", "version": 9}
    r4 = LG.chat_completions({"model": "test-model",
                              "messages": [{"role": "user", "content": "diseñame una lampara led"}],
                              "hcma_memory": True, "crear_skill": True})
    sk4 = r4.get("hcma_skill") or {}
    check("body.crear_skill crea candidata autor=modelo",
          r4.get("procedencia") == "skill_creada" and sk4.get("es_canonico") is False,
          f"proc={r4.get('procedencia')} skill={sk4}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ router P0 (abstener + señal explícita) OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
