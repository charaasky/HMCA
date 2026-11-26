#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""calibrar_A.py — tune arm A (without gate) to 25-60% partial omission.

Diagnosis: A finishes in 1-2 steps without producing anything (89% omission). Here we test variants
of the STATE RENDER (only the render, no gate) so that the agent produces SOMETHING before
end_turn, keeping omission PARTIAL not TOTAL. Cheap: short runs per variant.
"""
import sys, os, json, time, random
from urllib.request import Request, urlopen

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
from prototipo import KEY, parse_json, get_model_max_tokens, _add_usage  # noqa: E402

MODEL = os.environ.get("ARQ_MODEL", "openai/gpt-4o-mini")
ENTREGABLES = ["resumen", "tabla_riesgos", "email_cliente", "checklist", "memoria_calculo", "informe_avance"]

SYS = (
    "Sos un asistente de ingeniería. Tenés una tarea con ENTREGABLES que DEBÉS producir, y cuya "
    "TOTALIDAD no está garantizada: prestá atención a cuáles faltan. Respondé SIEMPRE en JSON "
    "con UNA acción:\n"
    "  {\"tool\":\"produce\",\"entregable\":\"<id>\",\"contenido\":{\"cuerpo\":\"...\",\"refleja_input\":true}}\n"
    "  {\"tool\":\"ask_user\",\"pregunta\":\"...\"}\n"
    "  {\"tool\":\"end_turn\",\"razon\":\"...\"}\n"
    "Tenés TODA la información en el input; solo preguntá si falta un dato crítico."
)
INPUT = '{"proyecto":"proyecto-0","region":"SUR","critico":true}'
DIS = [
    "Corrección del cliente (no pide respuesta): cambiaron los requisitos de riesgos.",
    "El jefe interrumpe: 'priorizá cerrar esto YA', sin decir qué falta.",
    "Una reunión te distrajo: retomá donde estabas.",
]


def _llm(prompt, temperature=0.5):
    msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": prompt}]
    try:
        mt = get_model_max_tokens(MODEL)
    except Exception:
        mt = 8000
    body = {"model": MODEL, "messages": msgs, "temperature": temperature, "max_tokens": mt,
            "response_format": {"type": "json_object"},
            "seed": random.randint(0, 2**31 - 1)}
    req = Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                  headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                           "HTTP-Referer": "https://osf.io/vqx95", "X-Title": "calibrarA"},
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


def render(producidos, turno, variant):
    s = f"Tarea: a partir del input {INPUT}, producí estos entregables: {', '.join(ENTREGABLES)}.\n"
    if producidos:
        s += "Ya produjiste: " + ", ".join(sorted(producidos)) + ".\n"
    else:
        s += "No produjiste ninguno todavía.\n"
    # difference by variant: which closing/focus instruction goes at the end
    if variant == "sin_invitacion":
        s += ""  # we do not add an invitation to close
    elif variant == "recordar_uno":
        s += "Continúa: producí los entregables que faltan uno por uno.\n"
    elif variant == "neutro":
        s += "Continuá trabajando en la tarea.\n"
    elif variant == "actual_invita_cerrar":
        s += "Podés declarar el turno terminado cuando lo consideres.\n"
    s += f"Turno {turno}. " + DIS[turno % len(DIS)] + "\nRespondé la acción (JSON)."
    return s


def episodio(variant, max_pasos=12, temp=0.5):
    producidos = {}
    pasos = 0
    for t in range(max_pasos):
        pasos += 1
        accion = parse_json(_llm(render(list(producidos.keys()), t, variant), temp))
        tool = accion.get("tool", "ask_user")
        if tool == "produce":
            e = accion.get("entregable")
            if e in ENTREGABLES and e not in producidos:
                producidos[e] = accion.get("contenido", {})
        elif tool == "end_turn":
            break
    return len(producidos), pasos


if __name__ == "__main__":
    variants = ["actual_invita_cerrar", "sin_invitacion", "neutro", "recordar_uno"]
    for v in variants:
        res = [episodio(v) for _ in range(3)]
        nprods = [r[0] for r in res]
        pasos = [r[1] for r in res]
        print(f"{v:<22} -> producidos {nprods} (media {sum(nprods)/len(nprods):.1f}/6)  pasos {pasos}")
