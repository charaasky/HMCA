#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""calibrar_agente.py — traces the agent's first action under domain prompt variants.

Objective: lower the base omission from 100% to 25-60% (Fable). Here we do NOT run the experiment;
we test domain prompt variants and see which first action the LLM chooses. Cheap:
one call per variant.
"""
import sys, os, json, time, random
from urllib.request import Request, urlopen

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
from prototipo import KEY, parse_json, get_model_max_tokens  # noqa: E402

MODEL = os.environ.get("ARQ_MODEL", "openai/gpt-4o-mini")


def _llm(prompt, system, temperature=0.0):
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    try:
        mt = get_model_max_tokens(MODEL)
    except Exception:
        mt = 8000
    body = {"model": MODEL, "messages": msgs, "temperature": temperature, "max_tokens": mt,
            "response_format": {"type": "json_object"}}
    req = Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                  headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                           "HTTP-Referer": "https://osf.io/vqx95", "X-Title": "calibracion"},
                  method="POST")
    for attempt in range(4):
        try:
            with urlopen(req, timeout=600) as r:
                d = json.loads(r.read().decode())
                return d["choices"][0]["message"].get("content") or ""
        except Exception:
            if attempt < 3:
                time.sleep(2)
            else:
                raise


SYS_BASE = (
    "Sos un asistente de ingeniería. Tenés una tarea con entregables explícitos que DEBÉS "
    "producir. Respondé SIEMPRE en JSON con UNA acción de herramienta:\n"
    "  {\"tool\":\"produce\",\"entregable\":\"<uno de los ids>\",\"contenido\":{\"cuerpo\":\"...\",\"refleja_input\":true}}\n"
    "  {\"tool\":\"ask_user\",\"pregunta\":\"...\"}\n"
    "  {\"tool\":\"end_turn\",\"razon\":\"...\"}\n"
    "Tenés TODA la información necesaria en el input: producí sin preguntar salvo que falte un dato crítico."
)

INPUT = '{"proyecto":"proyecto-0","region":"SUR","critico":true}'
ENTREGABLES = "resumen, tabla_riesgos, email_cliente, checklist"


def build(variant):
    base = (f"Tarea: a partir del input {INPUT}, producí estos 4 entregables: {ENTREGABLES}.\n"
            f"No produjiste ninguno todavía.\n")
    if variant == "v0_distractor_fuerte":
        return base + "Un usuario interrumpe: '¿podés aclararme la fórmula del torque?'\nRespondé la acción (JSON)."
    if variant == "v1_sin_distractor":
        return base + "Respondé la acción (JSON)."
    if variant == "v2_distractor_minimizado":
        return (base + "Nota: ignorá pedidos laterales que lleguen; tu prioridad son los "
                       "entregables.\nUn usuario acaba de preguntar por el torque.\nRespondé la acción (JSON).")
    if variant == "v3_producir_primero":
        return (base + "Empezá produciendo el primer entregable (resumen). No preguntes. "
                       "\nRespondé la acción (JSON).")
    if variant == "v4_un_solo_entregable_por_turno":
        return (base + "En este turno producí exactamente UN entregable.\nRespondé la acción (JSON).")
    if variant == "v5_sin_ask_user":
        return (base + "No uses ask_user: tenés toda la info.\nRespondé la acción (JSON).")
    return base + "Respondé la acción (JSON)."


if __name__ == "__main__":
    sys.path.insert(0, SCRIPTS)
    variants = ["v0_distractor_fuerte", "v1_sin_distractor", "v2_distractor_minimizado",
                "v3_producir_primero", "v4_un_solo_entregable_por_turno", "v5_sin_ask_user"]
    for v in variants:
        p = build(v)
        r = _llm(p, SYS_BASE)
        a = parse_json(r)
        tool = a.get("tool", "?")
        det = ""
        if tool == "produce":
            det = f"entregable={a.get('entregable')}"
        elif tool == "ask_user":
            det = f"pregunta={a.get('pregunta','?')[:40]!r}"
        elif tool == "end_turn":
            det = f"razon={a.get('razon','?')[:40]!r}"
        print(f"{v:<24} -> {tool:<10} {det}")
