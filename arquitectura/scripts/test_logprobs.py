#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Prueba qué modelos (vía OpenRouter) soportan `logprobs` para poder medir perplejidad.

No calcula PPL todavía: solo responde la pregunta de si cada modelo devuelve
log-probabilidades por token (requisito previo para PPL / ΔPPL)."""
import json
import os
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError

HERE = os.path.dirname(os.path.abspath(__file__))


def load_key():
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


KEY = load_key()
if not KEY:
    print("No OPENROUTER_API_KEY")
    sys.exit(1)

MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "anthropic/claude-sonnet-4-5",
    "anthropic/claude-fable-5.2",
    "openai/gpt-6-luna-pro",
]

PROMPT = "Say the numbers one, two, three, four, five, one word each."

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
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as r:
            d = json.loads(r.read().decode())
        lp = (d.get("choices") or [{}])[0].get("logprobs")
        content = (lp or {}).get("content") if isinstance(lp, dict) else None
        if content:
            t0 = content[0]
            print(f"OK      {model}: {len(content)} tokens con logprobs "
                  f"(token0={t0.get('token')!r}, logprob={t0.get('logprob')})")
        else:
            print(f"PARCIAL {model}: respondió SIN logprobs (logprobs={lp})")
    except HTTPError as e:
        try:
            err = json.loads(e.read().decode())
            msg = (err.get("error") or {}).get("message") or str(e)
        except Exception:
            msg = str(e)
        print(f"ERROR   {model}: {msg[:280]}")
    except Exception as e:
        print(f"ERROR   {model}: {str(e)[:280]}")
