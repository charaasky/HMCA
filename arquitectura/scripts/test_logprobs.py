#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Prueba qué modelos (vía OpenRouter) soportan `logprobs` y mide su perplejidad.

- Sin poda: `PPL_base` sobre la secuencia devuelta.
- Con poda (demo): `PPL_podado` renormalizando sobre los tokens que pasan un gate de
  verificación (por defecto: solo tokens numéricos del prompt de números).

Modos:
    python3 test_logprobs.py            # llama a OpenRouter (requiere OPENROUTER_API_KEY)
    python3 test_logprobs.py --offline  # auto-test con respuesta sintética (sin red ni key)
"""
import json
import math
import os
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import perplejidad_poda as PP  # noqa: E402


def load_key():
    env = os.environ.get("OPENROUTER_API_KEY", "")
    if env:
        return env.strip().strip('"').strip("'")
    for p in (os.path.join(HERE, "..", "..", ".env"),
              os.path.join(HERE, "..", ".env"),
              os.path.join(HERE, ".env")):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == "OPENROUTER_API_KEY":
                        return v.strip().strip('"').strip("'")
    return ""


MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "anthropic/claude-sonnet-4-5",
    "anthropic/claude-fable-5.2",
    "openai/gpt-6-luna-pro",
]
PROMPT = "Say the numbers one, two, three, four, five, one word each."

# Gate de verificación del demo de poda: solo se aceptan estos tokens.
NUMEROS = {"one", "two", "three", "four", "five"}


def _norm(tok):
    return (tok or "").strip().lower()


def metricas_respuesta(content, whitelist=NUMEROS):
    """PPL_base (real) + PPL_podado (demo: renormaliza sobre el whitelist de verificación)."""
    tokens, logprobs_gt = PP.extraer_secuencia_logprobs(content)
    log_mass = []
    sobrevive = []
    for e in content or []:
        top = e.get("top_logprobs") or []
        validos = [float(t.get("logprob", -math.inf)) for t in top
                   if _norm(t.get("token")) in whitelist]
        log_mass.append(PP.logsumexp(validos) if validos else -math.inf)
        sobrevive.append(_norm(e.get("token")) in whitelist)
    m = PP.metricas(logprobs_gt, log_mass, sobrevive)
    m["tokens"] = tokens
    return m


def _print_metricas(m):
    print(f"    PPL_base   = {m['ppl_base']:.3f}")
    print(f"    PPL_podado = {m['ppl_podado']:.3f}  (branching={m['branching_efectivo']:.3f})")
    print(f"    survival   = {m['survival_rate']:.3f}")
    print(f"    ΔPPL       = {m['delta_ppl_ratio']:.3f}x  (abs {m['delta_ppl_abs']:.3f})")
    print(f"    tokens     = {m['tokens']}")


def run_offline():
    # Respuesta sintética con forma OpenRouter. El paso 1 incluye "banana" (alucinación)
    # que el gate de verificación poda.
    content = [
        {"token": " one", "logprob": -0.5, "top_logprobs": [
            {"token": " one", "logprob": -0.5},
            {"token": " two", "logprob": -1.5},
            {"token": " three", "logprob": -2.5}]},
        {"token": " two", "logprob": -0.6, "top_logprobs": [
            {"token": " two", "logprob": -0.6},
            {"token": " banana", "logprob": -1.0},
            {"token": " four", "logprob": -3.0}]},
        {"token": " three", "logprob": -0.7, "top_logprobs": [
            {"token": " three", "logprob": -0.7},
            {"token": " four", "logprob": -2.0},
            {"token": " five", "logprob": -4.0}]},
    ]
    print("[offline] respuesta sintética (sin red, sin key):")
    _print_metricas(metricas_respuesta(content))


def run_real(key):
    for model in MODELS:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "temperature": 0.0,
            "max_tokens": 8,
            "logprobs": True,
            "top_logprobs": 3,
        }
        req = Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode())
        except HTTPError as e:
            try:
                err = json.loads(e.read().decode())
                msg = (err.get("error") or {}).get("message") or str(e)
            except Exception:
                msg = str(e)
            print(f"ERROR   {model}: {msg[:280]}")
            continue
        except Exception as e:
            print(f"ERROR   {model}: {str(e)[:280]}")
            continue

        lp = (d.get("choices") or [{}])[0].get("logprobs")
        content = (lp or {}).get("content") if isinstance(lp, dict) else None
        if not content:
            print(f"PARCIAL {model}: respondió SIN logprobs (logprobs={lp})")
            continue
        print(f"OK      {model}: {len(content)} tokens con logprobs")
        _print_metricas(metricas_respuesta(content))


def main(argv):
    if "--offline" in argv or "--self-test" in argv:
        run_offline()
        return 0
    key = load_key()
    if not key:
        print("No OPENROUTER_API_KEY (ni en env ni en .env).")
        print("  - prueba offline: python3 test_logprobs.py --offline")
        print("  - prueba real:    OPENROUTER_API_KEY=... python3 test_logprobs.py")
        return 1
    run_real(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
