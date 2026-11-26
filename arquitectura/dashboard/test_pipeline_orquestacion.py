#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de pipeline HCMA: la estructura de los papers.

Verifica que el chat respete la arquitectura:
    usuario -> orquestador -> { memoria | alias | calculadora | skill | LLM } -> retorna al usuario

Cada respuesta debe llevar `procedencia` ESTRUCTURADA puesta por CÓDIGO (no por el LLM):
    recall_puro | calculadora | llm_unverified

Reglas que cubre (según diseño):
  1) Memoria-primero: si el hecho está en el hipocampo, responde DETERMINISTA (sin LLM).
  2) Alias: 'color de mi guiya' -> entidad 'perra' -> bloque de mundo cerrado (ADVISORY, con LLM).
  3) Sin hit determinista -> LLM advisory con procedencia 'llm_unverified'.
  4) El router de skills decide y lo expone en `hcma_skill` (metacognición).
  5) `procedencia` SIEMPRE presente y en el conjunto conocido.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import memory_context as MC  # noqa: E402
import llm_gateway as LG  # noqa: E402

PROCEDENCIAS = {"recall_puro", "calculadora", "llm_unverified"}


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def _sembrar(m):
    # hechos personales (fuente usuario, verified)
    m.write("perra:color", "negra", fuente="usuario", verified=True)
    m.write("perra:nombre", "Luna", fuente="usuario", verified=True)
    m.write("guiya", "apodo de mi perra", fuente="usuario", verified=True)
    # arista alias TIPADA (P0-B): guiya -> perra
    m.crear_alias("guiya", "perra", fuente="usuario", verified=True)
    # hechos de ingeniería
    m.write("motor.potencia", 1500, unit="W", fuente="ingeniero", verified=True)


def _silenciar_llm():
    """Monkeypatch de los forwards para no tocar la red: el advisory devuelve texto fijo."""
    LG._forward_openai = lambda *a, **k: {"choices": [{"message": {"role": "assistant", "content": "RESPUESTA_LLM"}}], "usage": {}}
    LG._forward_anthropic = lambda *a, **k: {"content": [{"type": "text", "text": "RESPUESTA_LLM"}], "usage": {}}
    LG._forward_google = lambda *a, **k: {"candidates": [{"content": {"parts": [{"text": "RESPUESTA_LLM"}]}}], "usageMetadata": {}}
    LG._conn = lambda: ("https://openrouter.ai/api/v1", "test-key", "openrouter")


def _preguntar(q):
    return LG.chat_completions({"model": "test-model", "messages": [{"role": "user", "content": q}],
                                "hcma_memory": True})


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    _sembrar(m)
    MC.MEMORY_DB = db
    _silenciar_llm()

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    # 1) memoria-primero determinista (sin LLM)
    r = _preguntar("de que color era mi perra")
    check("memoria-primero -> recall_puro", r.get("procedencia") == "recall_puro" and r.get("llm_llamado") is False,
          f"proc={r.get('procedencia')}")

    # 2) alias -> entidad -> bloque de mundo cerrado (advisory, con LLM; la propiedad es semántica)
    r = _preguntar("de que color era mi guiya")
    alias = r.get("hcma_alias") or {}
    check("alias 2 saltos -> advisory con bloque de entidad",
          r.get("procedencia") == "llm_unverified" and alias.get("entidad") == "perra"
          and alias.get("complete") is True,
          f"proc={r.get('procedencia')} alias={alias}")

    # 3) sin hit -> LLM advisory con llm_unverified
    r = _preguntar("quien fue Einstein")
    check("advisory -> llm_unverified", r.get("procedencia") == "llm_unverified",
          f"proc={r.get('procedencia')}")

    # 4) router de skills expone decisión (metacognición) para un contexto sin skill
    r = _preguntar("diseñame una lapicera de tinta")
    sk = r.get("hcma_skill") or {}
    check("router skills -> hcma_skill", sk.get("decision") in ("crear", "abstener", "usar"),
          f"skill={sk}")

    # 5) procedencia siempre en el conjunto conocido
    todas = True
    for q in ["de que color era mi perra", "de que color era mi guiya", "quien fue Einstein",
              "diseñame una lapicera de tinta", "quien es mi vecino"]:
        p = _preguntar(q).get("procedencia")
        todas &= p in PROCEDENCIAS
    check("procedencia siempre estructurada", todas)

    # 6) anáfora multi-turno: follow-up sin entidad recupera por el turno previo (contexto, no determinista)
    r = LG.chat_completions({"model": "test-model",
                             "messages": [
                                 {"role": "user", "content": "mi guiya es mi perra"},
                                 {"role": "assistant", "content": "ok"},
                                 {"role": "user", "content": "y de que color es?"},
                             ], "hcma_memory": True})
    check("anáfora multi-turno -> contexto (no recall_puro)",
          r.get("procedencia") == "llm_unverified" and bool(r.get("hcma_facts")),
          f"proc={r.get('procedencia')} facts={[f['key'] for f in r.get('hcma_facts', [])]}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ pipeline fiel a la arquitectura" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
