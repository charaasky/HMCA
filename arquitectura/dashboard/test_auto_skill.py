#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de auto-creación / reuso de skill cuando el router se ABSTIENE (P0-2 + P0-3).

El router NUNCA auto-aprueba. Ante ("abstener", ...), un JUEZ LLM decide:
  - 'reuse'  -> reutiliza un skill existente (NO acuña id nuevo; evita fragmentación).
  - 'new'    -> crea una CANDIDATA (autor=modelo, unverified, no ejecutable).
  - 'ninguna'-> conocimiento general, cae a advisory.
Además, P0-3: una consulta NO latina con modelo de embeddings solo-inglés NO auto-crea
(abstenerse para no fragmentar; se marca sin_multilingue).

Se valida:
  1) juez_auto_skill parsea JSON decision=new; fail-closed ante no-JSON.
  2) decision=new -> skill_creada_auto + persistencia unverified (spec dict intacta).
  3) decision=reuse -> skill_reuse, NO crea skill.
  4) decision=ninguna -> advisory normal, NO crea skill.
  5) P0-3: consulta no-latina + modelo no-multilingüe -> sin_multilingue, NO crea skill.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import monitor_fachada as MF  # noqa: E402
import memory_context as MC  # noqa: E402
import llm_gateway as LG  # noqa: E402


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def _mock_openai_json(payload):
    content = json.dumps(payload, ensure_ascii=False)
    return lambda *a, **k: {"choices": [{"message": {"role": "assistant", "content": content}}],
                            "usage": {}}


def _mock_openai_text(text):
    return lambda *a, **k: {"choices": [{"message": {"role": "assistant", "content": text}}],
                            "usage": {}}


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    fachada = MF.MonitorFachada(m)
    MC.MEMORY_DB = db
    LG._conn = lambda: ("https://openrouter.ai/api/v1", "test-key", "openrouter")
    LG._forward_anthropic = lambda *a, **k: {"content": [{"type": "text", "text": "RESPUESTA_LLM"}], "usage": {}}
    LG._forward_google = lambda *a, **k: {"candidates": [{"content": {"parts": [{"text": "RESPUESTA_LLM"}]}}], "usageMetadata": {}}
    LG._write_monitor = lambda op: fachada.pedir(op)

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    def n_skills():
        return m.conn.execute("SELECT COUNT(*) FROM facts WHERE key LIKE 'skill:%'").fetchone()[0]

    # 1) juez: JSON decision=new parseado
    LG._forward_openai = _mock_openai_json({
        "decision": "new", "nombre": "control_tv", "descripcion": "Diseñar un control remoto de TV",
        "inputs": [], "formulas": []})
    d = LG.juez_auto_skill("diseñame un control de tv", "test-model",
                           "https://openrouter.ai/api/v1", "k", "openrouter", candidatos=["luminaria_led"])
    check("juez parsea decision=new", isinstance(d, dict) and d.get("decision") == "new"
          and d.get("nombre") == "control_tv", f"d={d}")

    # 2) juez: fail-closed ante no-JSON
    LG._forward_openai = _mock_openai_text("RESPUESTA_LLM")
    d2 = LG.juez_auto_skill("diseñame un control de tv", "test-model",
                            "https://openrouter.ai/api/v1", "k", "openrouter")
    check("juez fail-closed ante no-JSON", d2 == {}, f"d2={d2}")

    # 3) decision=new -> skill_creada_auto + persistencia
    LG.juez_auto_skill = lambda *a, **k: {
        "decision": "new", "nombre": "control_tv", "descripcion": "Diseñar un control remoto de TV",
        "inputs": [], "formulas": []}
    LG._forward_openai = _mock_openai_text("RESPUESTA_LLM")
    r = LG.chat_completions({"model": "test-model",
                             "messages": [{"role": "user", "content": "diseñame un control de tv"}],
                             "hcma_memory": True})
    sk = r.get("hcma_skill") or {}
    check("new -> skill_creada_auto", r.get("procedencia") == "skill_creada_auto"
          and sk.get("decision") == "crear_auto" and sk.get("es_canonico") is False,
          f"proc={r.get('procedencia')} skill={sk}")
    fact = m.current_raw("skill:control_tv")
    check("skill persistido unverified", fact is not None and fact["verified"] is False
          and fact["authority"] is False and fact["fuente"] == "modelo"
          and (fact.get("meta") or {}).get("tipo") == "skill",
          f"fact={fact and {k: fact[k] for k in ('key','verified','authority','fuente')}}")
    check("spec guardada como JSON", fact is not None and isinstance(fact["value"], dict)
          and fact["value"].get("descripcion") == "Diseñar un control remoto de TV",
          f"value={fact and fact['value']!r}")
    total = n_skills()

    # 4) decision=reuse -> skill_reuse, NO crea skill
    LG.juez_auto_skill = lambda *a, **k: {"decision": "reuse", "id_reuse": "control_tv"}
    LG._forward_openai = _mock_openai_text("RESPUESTA_LLM")
    r4 = LG.chat_completions({"model": "test-model",
                              "messages": [{"role": "user", "content": "diseñame una lapicera"}],
                              "hcma_memory": True})
    check("reuse -> skill_reuse (no crea)", r4.get("procedencia") == "skill_reuse"
          and (r4.get("hcma_skill") or {}).get("decision") == "reuse"
          and n_skills() == total,
          f"proc={r4.get('procedencia')} skills={n_skills()}")

    # 5) decision=ninguna -> advisory, NO crea skill
    LG.juez_auto_skill = lambda *a, **k: {"decision": "ninguna"}
    LG._forward_openai = _mock_openai_text("RESPUESTA_LLM")
    r5 = LG.chat_completions({"model": "test-model",
                              "messages": [{"role": "user", "content": "que es la entropia"}],
                              "hcma_memory": True})
    check("ninguna -> advisory (no crea)", r5.get("procedencia") == "llm_unverified"
          and n_skills() == total,
          f"proc={r5.get('procedencia')} skills={n_skills()}")

    # 6) P0-3: no-latino + modelo solo-inglés -> sin_multilingue, NO auto-crea
    LG.juez_auto_skill = lambda *a, **k: {"decision": "new", "nombre": "x", "descripcion": "x",
                                          "inputs": [], "formulas": []}
    LG._forward_openai = _mock_openai_text("RESPUESTA_LLM")
    r6 = LG.chat_completions({"model": "test-model",
                              "messages": [{"role": "user", "content": "帮我设计一个电视遥控器"}],
                              "hcma_memory": True})
    sk6 = r6.get("hcma_skill") or {}
    check("P0-3: no-latino sin multilingüe no auto-crea",
          sk6.get("sin_multilingue") is True and r6.get("procedencia") == "llm_unverified"
          and n_skills() == total,
          f"skill={sk6} proc={r6.get('procedencia')} skills={n_skills()}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ auto-skill (P0-2/P0-3) OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
