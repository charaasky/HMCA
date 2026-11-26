#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""calibrar_agente2.py — traces a COMPLETE episode to measure real omission per arm.

Test: with the recalibrated domain (real but not dominant distractor), how many of the 4
deliverables does the agent produce in a complete episode, and how do A (free) vs D (with
enforcement) differ? This calibrates the base omission rate before the at-scale run.
"""
import sys, os, json, time, random
from urllib.request import Request, urlopen

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
from prototipo import KEY, parse_json, get_model_max_tokens, _add_usage  # noqa: E402

MODEL = os.environ.get("ARQ_MODEL", "openai/gpt-4o-mini")
ENTREGABLES = ["resumen", "tabla_riesgos", "email_cliente", "checklist", "memoria_calculo", "informe_avance"]


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
                           "HTTP-Referer": "https://osf.io/vqx95", "X-Title": "calibracion2"},
                  method="POST")
    for attempt in range(4):
        try:
            with urlopen(req, timeout=600) as r:
                d = json.loads(r.read().decode())
                c = d["choices"][0]["message"].get("content") or ""
                u = d.get("usage", {})
                _add_usage(MODEL, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                return c
        except Exception:
            if attempt < 3:
                time.sleep(2)
            else:
                raise


SYS = (
    "Sos un asistente de ingeniería. Tenés una tarea con ENTREGABLES que DEBÉS producir y "
    "TOTALIDAD no está garantizada: prestá atención a cuáles faltan. Respondé SIEMPRE en JSON "
    "con UNA acción:\n"
    "  {\"tool\":\"produce\",\"entregable\":\"<id>\",\"contenido\":{\"cuerpo\":\"...\",\"refleja_input\":true}}\n"
    "  {\"tool\":\"ask_user\",\"pregunta\":\"...\"}\n"
    "  {\"tool\":\"end_turn\",\"razon\":\"...\"}\n"
    "Tenés TODA la información en el input; solo preguntá si falta un dato crítico."
)
INPUT = '{"proyecto":"proyecto-0","region":"SUR","critico":true}'
DISTRACTORES = [
    "Corrección del cliente (no pide respuesta): cambiaron los requisitos de la sección de riesgos.",
    "El jefe interrumpe: 'priorizá cerrar esto YA', sin decir qué falta.",
    "Una reunión te distrajo: retomá donde estabas.",
]


def episodio(bloquea_end, max_pasos=12, temperature=0.0):
    producidos = {}
    pasos = 0
    rechazos = 0
    end_aceptado = False
    for t in range(max_pasos):
        pasos += 1
        pend = [e for e in ENTREGABLES if e not in producidos]
        s = (f"Tarea: a partir del input {INPUT}, producí estos entregables: {', '.join(ENTREGABLES)}.\n")
        if producidos:
            s += "Ya produjiste: " + ", ".join(sorted(producidos)) + ".\n"
        else:
            s += "No produjiste ninguno todavía.\n"
        if pend:
            s += "Faltan: " + ", ".join(pend) + ".\n"
        s += DISTRACTORES[t % len(DISTRACTORES)] + "\nRespondé la acción (JSON)."
        accion = parse_json(_llm(s, SYS, temperature))
        tool = accion.get("tool", "ask_user")
        if tool == "produce":
            eid = accion.get("entregable")
            if eid in ENTREGABLES and eid not in producidos:
                producidos[eid] = accion.get("contenido", {})
        elif tool == "end_turn":
            if bloquea_end and pend:
                rechazos += 1
                continue
            end_aceptado = True
            break
    return producidos, pasos, rechazos, end_aceptado


if __name__ == "__main__":
    print("Brazo A (end_turn libre):")
    for rep in range(3):
        p, pa, rc, ea = episodio(bloquea_end=False, temperature=0.5)
        print(f"  rep{rep}: producidos={len(p)}/{len(ENTREGABLES)} {' '.join(sorted(p))} "
              f"pasos={pa} end_aceptado={ea}")

    print("Brazo D (end_turn bloqueado hasta completar):")
    for rep in range(3):
        p, pa, rc, ea = episodio(bloquea_end=True, temperature=0.5)
        print(f"  rep{rep}: producidos={len(p)}/{len(ENTREGABLES)} {' '.join(sorted(p))} "
              f"pasos={pa} rechazos={rc} end_aceptado={ea}")
