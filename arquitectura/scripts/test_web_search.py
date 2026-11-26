#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Prueba del plugin de búsqueda web de OpenRouter (para no cablear algo que no anda)."""
import json, os
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))


def load_key():
    env = {}
    for p in (os.path.join(HERE, "..", ".env"), os.path.join(HERE, ".env")):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env.get("OPENROUTER_API_KEY", "")


KEY = load_key()

payload = json.dumps({
    "model": "openai/gpt-4o-mini",
    "messages": [{"role": "user",
                  "content": "¿Cuál es el precio actual aproximado por kg de acero estructural "
                             "en Argentina (Acindar/Ternium)?"}],
    "temperature": 0.0,
    "max_tokens": 600,
    "plugins": [{"id": "web", "max_results": 3}],
}).encode()

req = Request("https://openrouter.ai/api/v1/chat/completions", data=payload,
              headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                       "HTTP-Referer": "https://osf.io/vqx95", "X-Title": "HCMA web test"},
              method="POST")

try:
    with urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode())
    print("=== RESPUESTA (top-level keys) ===")
    print(list(d.keys()))
    msg = d.get("choices", [{}])[0].get("message", {})
    print("\n=== message keys ===")
    print(list(msg.keys()))
    print("\n=== content ===")
    print(msg.get("content"))
    print("\n=== annotations (si hay) ===")
    print(json.dumps(msg.get("annotations"), ensure_ascii=False, indent=2)[:2000])
    print("\n=== usage ===")
    print(d.get("usage"))
except Exception as e:
    print("ERROR:", e)
    if hasattr(e, "read"):
        print(e.read().decode()[:3000])
