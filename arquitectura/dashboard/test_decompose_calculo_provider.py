#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de `_decompose_calculo` AGNÓSTICO al proveedor.

El LLM SOLO descompone (es_calculo + inputs + fórmulas) a JSON; la EJECUCIÓN es la calculadora
determinista (`calculadora.computar`, evaluador AST seguro) y NO depende del proveedor. Acá se
valida que la DESCOMPOSICIÓN rutee al forward correcto según el proveedor:

  - google    -> _forward_google    (generateContent)
  - anthropic -> _forward_anthropic (messages)
  - else      -> _forward_openai    (chat/completions, OpenAI-compatible)

También se valida el fail-closed: si el proveedor no devuelve JSON, `_decompose_calculo` devuelve
None (y `resolver_calculo` responde "no_es_calculo").
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

import llm_gateway as LG  # noqa: E402

_SPEC = ('{"es_calculo": true, "inputs": {"a": 2, "b": 3}, '
         '"formulas": [{"name": "suma", "expr": "a+b", "unit": ""}]}')


def main():
    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    via = []

    def mock_google(base, key, body):
        via.append("google")
        return {"candidates": [{"content": {"parts": [{"text": _SPEC}]}}], "usageMetadata": {}}

    def mock_anthropic(base, key, body):
        via.append("anthropic")
        return {"content": [{"type": "text", "text": _SPEC}], "usage": {}}

    def mock_openai(base, key, body):
        via.append("openai")
        return {"choices": [{"message": {"role": "assistant", "content": _SPEC}}], "usage": {}}

    LG._forward_google = mock_google
    LG._forward_anthropic = mock_anthropic
    LG._forward_openai = mock_openai

    # 1) Google
    LG._conn = lambda: ("https://generativelanguage.googleapis.com", "k", "google")
    d = LG._decompose_calculo("cuanto es 2+3", "test-model")
    check("google -> _forward_google + parsea JSON",
          via == ["google"] and isinstance(d, dict) and d.get("es_calculo") is True,
          f"via={via} d={d}")

    # 2) Anthropic
    via.clear()
    LG._conn = lambda: ("https://api.anthropic.com", "k", "anthropic")
    d = LG._decompose_calculo("cuanto es 2+3", "test-model")
    check("anthropic -> _forward_anthropic + parsea JSON",
          via == ["anthropic"] and isinstance(d, dict) and d.get("es_calculo") is True,
          f"via={via} d={d}")

    # 3) OpenAI-compatible (OpenRouter cae acá)
    via.clear()
    LG._conn = lambda: ("https://openrouter.ai/api/v1", "k", "openrouter")
    d = LG._decompose_calculo("cuanto es 2+3", "test-model")
    check("openrouter -> _forward_openai + parsea JSON",
          via == ["openai"] and isinstance(d, dict) and d.get("es_calculo") is True,
          f"via={via} d={d}")

    # 4) no-JSON -> None (fail-closed, no alucina un spec)
    LG._conn = lambda: ("https://openrouter.ai/api/v1", "k", "openrouter")
    LG._forward_openai = lambda *a, **k: {"choices": [{"message": {"content": "no soy json"}}], "usage": {}}
    d = LG._decompose_calculo("que es la entropia", "test-model")
    check("no-JSON -> None", d is None, f"d={d}")

    print("\nRESULTADO:", "✔ decompose_calculo agnóstico OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
