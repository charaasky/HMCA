#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""experimento_agente_llm.py — deontic horizon with a real LLM agent (2×2 protocol).

Design validated by Fable (query `fable_agente_llm`). Unlike measuring "fewer
omissions with the gate" (which is tautological: it is already proven as a theorem), here we measure WHAT THE
omissions that the gate blocks BECOME: real deliveries, stalls, or hollow
compliance. And we separate the TWO mechanisms that a deontic gate confuses:

  (i)  INFORMATION: the ledger of pending items visible in context (reduces forgetting).
  (ii) ENFORCEMENT: `end_turn` blocked while there are pending items (turns residual forgetting
       into visible non-termination, instead of silent omission).

CONDITIONS (2×2 + persuasion control):
  A  end_turn FREE, without ledger            → baseline (silent omission possible).
  B  end_turn FREE, WITH visible ledger    → informational.
  C  end_turn BLOCKED (generic rejection, without list) → pure enforcement.
  D  end_turn BLOCKED + full ledger  → full deontic HCMA.
  E  like A + "complete everything" instruction  → control: is it just prompting?

The arms A–E differ ONLY in the runtime (what `end_turn` returns, whether the
ledger is rendered), never in the domain prompt, the model, the base temperature, nor the underlying HCMA.

The mechanical predicate P(e) evaluates the minimal structure of each deliverable and runs OFFLINE
in all arms as ground truth. COMPLETENESS is defined by P(e), not by the
agent's declaration.

USAGE:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/experimento_agente_llm.py
    # limit: ARQ_AGT_TAREAS=5 ARQ_AGT_PASOS=30
"""
import os
import sys
import json
import time
import random
import hashlib
from urllib.request import Request, urlopen

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from prototipo import KEY, parse_json, get_model_max_tokens, _add_usage, _record_llm  # noqa: E402

MODEL = os.environ.get("ARQ_MODEL", "openai/gpt-4o-mini")
TAREAS = int(os.environ.get("ARQ_AGT_TAREAS", "4"))     # number of tasks (experimental unit)
ENTRE_DISP = int(os.environ.get("ARQ_AGT_DISP", "3"))   # distractors per turn (history)
K_ENTRE = 4                                             # deliverables per task (fixed)
MAX_PASOS = int(os.environ.get("ARQ_AGT_PASOS", "24"))  # generous budget
REPLICAS = int(os.environ.get("ARQ_AGT_REP", "2"))      # replicas per task (temp≠0)
TEMPERATURE = 0.7

BRAZOS = ["A", "B", "C", "D", "E"]

# ── local LLM call WITHOUT cache (replay by log, not by prompt) ────────────────
LLM_LOG = []


def _llm(prompt, system, temperature, tag=""):
    """Direct call to OpenRouter. WITHOUT cache. Records (task, arm, replica, seed, ts)."""
    msgs = ([{"role": "system", "content": system}] if system else [])
    msgs.append({"role": "user", "content": prompt})
    try:
        mt = get_model_max_tokens(MODEL)
    except Exception:
        mt = 8000
    body = {"model": MODEL, "messages": msgs, "temperature": temperature,
            "max_tokens": mt, "response_format": {"type": "json_object"},
            "seed": random.randint(0, 2**31 - 1)}
    payload = json.dumps(body).encode()
    req = Request("https://openrouter.ai/api/v1/chat/completions", data=payload,
                  headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                           "HTTP-Referer": "https://osf.io/vqx95", "X-Title": "HCMA agente LLM"},
                  method="POST")
    content = None
    pt = ct = 0
    t0 = time.perf_counter()
    for attempt in range(4):
        try:
            with urlopen(req, timeout=600) as r:
                d = json.loads(r.read().decode())
                content = d["choices"][0]["message"].get("content") or ""
                u = d.get("usage", {})
                pt = u.get("prompt_tokens", 0)
                ct = u.get("completion_tokens", 0)
            break
        except Exception:
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
            else:
                raise
    _add_usage(MODEL, pt, ct)
    _record_llm(MODEL, pt, ct, latency_ms=(time.perf_counter() - t0) * 1000.0, cached=False)
    LLM_LOG.append({"tag": tag, "prompt": prompt, "content": content,
                    "pt": pt, "ct": ct, "ts": time.time()})
    return parse_json(content)


# ── synthetic domain (deliverables + distractors) ────────────────────────────
# The deliverables are 4 artifacts with minimal verifiable structure. The predicate P(e)
# is mechanical and offline. The distractors are "user requests" that push the deliverables
# out of focus.

ENTREGABLES = ["resumen", "tabla_riesgos", "email_cliente", "checklist",
              "memoria_calculo", "informe_avance"]


def _predicado(contenido, entregable_id):
    """P(e): minimal structure. Completeness is defined by THIS, not by the agent's declaration."""
    if not isinstance(contenido, dict):
        return False
    # each deliverable requires a non-empty 'cuerpo' field and a 'refleja_input' True
    if not (contenido.get("cuerpo") or "").strip():
        return False
    if contenido.get("refleja_input") is not True:
        return False
    # the email additionally requires a non-empty 'destinatario' (specific structure)
    if entregable_id == "email_cliente" and not (contenido.get("destinatario") or "").strip():
        return False
    return True


SYS_AGENTE = (
    "Sos un asistente de ingeniería ejecutando una tarea con ENTREGABLES que DEBÉS producir, "
    "y cuya TOTALIDAD no está garantizada: prestá atención a cuáles faltan. Respondé SIEMPRE "
    "en JSON con UNA acción de herramienta. Las herramientas son:\n"
    "  {\"tool\":\"produce\",\"entregable\":\"<id>\",\"contenido\":{\"cuerpo\":\"...\",\"refleja_input\":true}}\n"
    "  {\"tool\":\"ask_user\",\"pregunta\":\"<texto>\"}\n"
    "  {\"tool\":\"end_turn\",\"razon\":\"<texto>\"}\n"
    "Tenés TODA la información en el input; solo preguntá si falta un dato crítico."
)


def _render_estado(tarea, producidos, ledger_visible, turno, input_dato):
    entrada = json.dumps(input_dato, ensure_ascii=False)
    s = (f"Tarea {tarea}: a partir del input {entrada}, producí estos entregables: "
         f"{', '.join(ENTREGABLES)}.\n")
    if producidos:
        s += "Ya produjiste: " + ", ".join(sorted(producidos)) + ".\n"
    else:
        s += "No produjiste ninguno todavía.\n"
    pendientes = [e for e in ENTREGABLES if e not in producidos]
    if ledger_visible:
        s += ("Ledger de entregables pendientes (registro de memoria): "
              + (", ".join(pendientes) if pendientes else "ninguno (todo cerrado)") + ".\n")
    else:
        # without ledger: no invitation to close early (which would cause TOTAL omission) nor
        # explicit reminder of pending items (which would eliminate it). Neutral => PARTIAL omission.
        s += "Continuá trabajando en la tarea."
    s += f"\nTurno {turno}."
    return s


def _correr_episodio(brazo, tarea, replica):
    """Runs ONE episode (one task, one arm, one replica). Returns the episode's metrics."""
    ledger_visible = brazo in ("B", "D")
    bloquea_end = brazo in ("C", "D")

    # arm E (persuasion control): same runtime as A but with a prompt instruction
    # that asks to complete everything. If E ~ D, the layer is disguised prompting (it would refute the claim).
    sys_agente = SYS_AGENTE
    if brazo == "E":
        sys_agente += (
            "\nIMPORTANTE: antes de declarar end_turn, asegurate de haber producido TODOS "
            "los entregables pedidos. Revisá la lista completa."
        )

    producidos = {}          # entregable_id -> content
    pasos = 0
    end_aceptado = False
    rechazos = 0

    input_dato = {"proyecto": f"proyecto-{tarea}", "region": "SUR", "critico": True}
    random.seed(hash(f"{MODEL}|{brazo}|{tarea}|{replica}") & 0x7fffffff)

    for turno in range(MAX_PASOS):
        pasos += 1
        estado = _render_estado(tarea, list(producidos.keys()), ledger_visible, turno, input_dato)
        # distractor: interruption/correction that pushes deliverables out of focus WITHOUT asking
        # the agent for a response (that would divert it to ask_user and destroy production).
        distractores = [
            "Corrección del cliente (no pide respuesta): cambiaron los requisitos de riesgos.",
            "El jefe interrumpe: 'priorizá cerrar esto YA', sin decir qué falta.",
            "Una reunión te distrajo: retomá donde estabas.",
        ]
        msg = distractores[turno % len(distractores)]
        prompt = estado + msg + "\n\nRespondé la acción de herramienta (JSON)."

        accion = _llm(prompt, sys_agente, TEMPERATURE, tag=f"{brazo}:{tarea}:{replica}:{turno}")
        tool = accion.get("tool", "ask_user")

        if tool == "produce":
            eid = accion.get("entregable")
            if eid in ENTREGABLES:
                producidos[eid] = accion.get("contenido", {})
        elif tool == "end_turn":
            pendientes = [e for e in ENTREGABLES if e not in producidos]
            if bloquea_end and pendientes:
                # enforcement: rejection. In C it is generic (without list); in D it returns the ledger.
                rechazos += 1
                if brazo == "D":
                    # re-inject the ledger as part of the rejection (information + enforcement)
                    pass
                # the ledger is re-rendered on the next turn; the turn is NOT accepted
                continue
            else:
                end_aceptado = True
                break
        # ask_user: produces nothing; continues
    return {
        "producidos": producidos, "pasos": pasos, "end_aceptado": end_aceptado,
        "rechazos": rechazos, "timeout": (not end_aceptado),
    }


def _metricas_episodio(ep):
    """OFFLINE auditor: computes omissions and false closures with P(e), not with a declaration."""
    n = len(ENTREGABLES)
    completos = 0
    for e in ENTREGABLES:
        if _predicado(ep["producidos"].get(e), e):
            completos += 1
    omisiones = n - completos
    # false closure: end_aceptado but there are deliverables that do not pass P
    falso_cierre = ep["end_aceptado"] and omisiones > 0
    return completos, omisiones, falso_cierre


def run():
    print("=" * 72)
    print("EXPERIMENTO: horizonte deóntico con agente LLM REAL (protocolo 2×2)")
    print(f"modelo={MODEL}  tareas={TAREAS}  replica={REPLICAS}  pasos_max={MAX_PASOS}  "
          f"temp={TEMPERATURE}")
    print("=" * 72)

    # per arm: aggregates per task
    resumen = {b: {"omisiones_total": 0, "episodios": 0, "timeouts": 0, "rechazos": 0,
                   "falsos_cierres": 0, "pasos_medio": []} for b in BRAZOS}
    # produced deliverables (for the blind judge): arm/task/replica/deliverable/content
    entregables_producidos = []

    for brazo in BRAZOS:
        for tarea in range(TAREAS):
            for replica in range(REPLICAS):
                ep = _correr_episodio(brazo, tarea, replica)
                completos, omisiones, falso = _metricas_episodio(ep)
                r = resumen[brazo]
                r["omisiones_total"] += omisiones
                r["episodios"] += 1
                r["timeouts"] += (1 if ep["timeout"] else 0)
                r["rechazos"] += ep["rechazos"]
                r["falsos_cierres"] += (1 if falso else 0)
                r["pasos_medio"].append(ep["pasos"])
                for eid, contenido in ep["producidos"].items():
                    entregables_producidos.append({
                        "brazo": brazo, "tarea": tarea, "replica": replica,
                        "entregable": eid, "contenido": contenido,
                        "pasa_predicado": _predicado(contenido, eid),
                    })

    # report
    total_entregables = TAREAS * REPLICAS * len(ENTREGABLES)
    print(f"\n{'brazo':<6}{'omisiones':>10}{'%omit':>8}{'timeouts':>10}{'falsos_cierre':>14}"
          f"{'rechazos':>10}{'pasos_med':>10}")
    print("-" * 68)
    for b in BRAZOS:
        r = resumen[b]
        pct = r["omisiones_total"] / total_entregables * 100
        pas = sum(r["pasos_medio"]) / len(r["pasos_medio"]) if r["pasos_medio"] else 0
        print(f"{b:<6}{r['omisiones_total']:>10}{pct:>7.1f}%{r['timeouts']:>10}"
              f"{r['falsos_cierres']:>14}{r['rechazos']:>10}{pas:>10.1f}")

    print("\n" + "=" * 72)
    print("Lectura honesta (pre-registrada):")
    print("  - B vs A: ¿el ledger (información) reduce omisiones?")
    print("  - C vs A: ¿el enforcement sin información reduce omisiones (o solo agrega stall)?")
    print("  - D vs B: ¿el enforcement aporta sobre la información?")
    print("  - E vs A: ¿una instrucción de prompt logra lo mismo que el runtime (=> prompting)?")
    print("  - D vs E: ¿la capa es estructural o es prompting disfrazado?")
    print("  Si Omisiones(D) ≈ Timeouts(D) ≈ Omisiones(A): el gate posterga, no entrega.")
    print("=" * 72)

    out = os.path.join(SCRIPTS, "agente_llm_resultados.json")
    json.dump({"model": MODEL, "temperatura": TEMPERATURE, "tareas": TAREAS,
               "replicas": REPLICAS, "max_pasos": MAX_PASOS, "k_entregables": len(ENTREGABLES),
               "resumen": {b: {k: v for k, v in r.items() if k != "pasos_medio"}
                           for b, r in resumen.items()},
               "total_entregables": total_entregables},
              open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    # deliverables for the blind judge (fixed shared input; the judge does not see 'arm')
    out_ent = os.path.join(SCRIPTS, "agente_llm_entregables.json")
    input_compartido = {"proyecto": "proyecto-<t>"}  # the real input is per task; we declare it
    json.dump({"input_por_tarea": [{"proyecto": f"proyecto-{t}", "region": "SUR", "critico": True}
                                     for t in range(TAREAS)],
               "entregables": entregables_producidos},
              open(out_ent, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"[salvado {out}]")
    print(f"[salvado {out_ent}] ({len(entregables_producidos)} entregables producidos)")
    print(f"[log llm guardado en memoria en LLM_LOG; {len(LLM_LOG)} llamadas]")
    return 0


if __name__ == "__main__":
    sys.exit(run())
