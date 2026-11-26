#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""tier0_depuracion.py — Fable Tier 0: measure repair rate and timeouts in C/D.

Diagnosis: in phase 2, C/D had high timeouts and D more blocks than C. Fable's hypothesis:
(a) the rejection message was NOT re-injected into the prompt (the agent never saw why it
was rejected -> it does not repair), and (b) P(e) may be stricter than what the agent can
infer. This script fixes (a) and LOGS per turn the real state of each artifact to
measure the REPAIR RATE (fraction of blocks that manage to take the artifact from ¬P to P).

Pass objective (pre-registered by Fable): repair rate in D >= 0.7, timeouts <= 10%.

Only runs C and D with 4 tasks x 2 replicates, full JSONL logging per turn.
"""
import os
import sys
import json
import time
import random
import re
from urllib.request import Request, urlopen

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from prototipo import KEY, parse_json, get_model_max_tokens, _add_usage, _record_llm  # noqa: E402
import experimento_agente_llm_v2 as V2  # noqa: E402  (reusa briefs + predicado + llm)

MODEL = os.environ.get("ARQ_MODEL", "openai/gpt-4o-mini")
TAREAS = int(os.environ.get("ARQ_AGT_TAREAS", "4"))
REPLICAS = int(os.environ.get("ARQ_AGT_REP", "2"))
MAX_PASOS = int(os.environ.get("ARQ_AGT_PASOS", "20"))
TEMPERATURE = float(os.environ.get("ARQ_AGT_TEMP", "0.7"))
SEMILLA = int(os.environ.get("ARQ_AGT_SEED", "1234"))
TOPE_RECHAZOS = int(os.environ.get("ARQ_AGT_TOPE_RECHAZOS", "4"))

BRAZOS = ["C", "D"]  # Tier 0: only enforcement (opaque) and complete (with list)

ENTREGABLES = V2.ENTREGABLES
_predicado = V2._predicado
_generar_brief = V2._generar_brief
_llm = V2._llm
SYS_AGENTE = V2.SYS_AGENTE


def _taxonomia(producidos, brief):
    """Per artifact: ausente / incorrecto / correcto (Fable's taxonomy)."""
    out = {}
    for e in ENTREGABLES:
        c = producidos.get(e)
        if c is None:
            out[e] = "ausente"
        elif _predicado(c, e, brief):
            out[e] = "correcto"
        else:
            out[e] = "incorrecto"
    return out


def _render(brief, producidos, ledger_visible, turno, rechazo_pendiente):
    s = ("Brief: " + json.dumps(brief, ensure_ascii=False) + "\n"
         f"Entregables a producir: {', '.join(ENTREGABLES)}.\n")
    # show real compliance state per deliverable (key + whether it passes P)
    if ledger_visible:
        estados = []
        for e in ENTREGABLES:
            c = producidos.get(e)
            if c is None:
                estados.append(f"{e}: ausente")
            elif _predicado(c, e, brief):
                estados.append(f"{e}: conforme")
            else:
                estados.append(f"{e}: INCONFORME (no pasa la verificación)")
        s += "Ledger de entregables: " + "; ".join(estados) + ".\n"
    else:
        s += "Continuá trabajando en la tarea.\n"
    if rechazo_pendiente:
        s += f"Respuesta del sistema a tu end_turn anterior: {rechazo_pendiente}\n"
    s += f"Turno {turno}."
    return s


def _mensaje_rechazo(brazo, no_conformes):
    if brazo == "C":
        return "end_turn rechazado."  # opaque
    return "end_turn rechazado: quedan pendientes " + ", ".join(no_conformes) + "."


def _correr(brazo, brief, replica):
    ledger_visible = (brazo == "D")
    producidos = {}
    bloqueos = 0
    end_aceptado = False
    rechazo_pendiente = None
    eventos = []          # JSONL per turn
    reparaciones = []     # per block: (num_no_conformes_before, num_no_conformes_after)

    for turno in range(MAX_PASOS):
        antes = _taxonomia(producidos, brief)
        estado = _render(brief, producidos, ledger_visible, turno, rechazo_pendiente)
        prompt = estado + "\nRespondé la acción de herramienta (JSON)."
        accion = _llm(prompt, SYS_AGENTE, TEMPERATURE, tag=f"{brazo}:{replica}:{turno}")
        tool = accion.get("tool", "ask_user")

        if tool == "produce":
            eid = accion.get("entregable")
            if eid in ENTREGABLES:
                producidos[eid] = accion.get("contenido", {})
        elif tool == "end_turn":
            no_conformes = [e for e in ENTREGABLES if not _predicado(producidos.get(e), e, brief)]
            if no_conformes:
                bloqueos += 1
                reparaciones.append((len(no_conformes),))
                # rejection cap: force close (forced flag)
                if bloqueos >= TOPE_RECHAZOS:
                    end_aceptado = True
                    eventos.append({"turno": turno, "accion": "end_turn", "forzado": True,
                                    "no_conformes": no_conformes})
                    break
                rechazo_pendiente = _mensaje_rechazo(brazo, no_conformes)
                eventos.append({"turno": turno, "accion": "end_turn rechazado",
                                "no_conformes": no_conformes, "rechazo": rechazo_pendiente,
                                "taxonomia_antes": antes})
            else:
                end_aceptado = True
                eventos.append({"turno": turno, "accion": "end_turn aceptado", "forzado": False})
                break
        elif tool == "ask_user":
            eventos.append({"turno": turno, "accion": "ask_user"})

        # record the taxonomy AFTER a possible repair
        despues = _taxonomia(producidos, brief)
        # if there was a block in the previous turn, measure repair
        if rechazo_pendiente is not None and tool in ("produce", "end_turn"):
            reparaciones[-1] += (len([e for e in ENTREGABLES
                                      if not _predicado(producidos.get(e), e, brief)]),)

    final = _taxonomia(producidos, brief)
    # repair rate: in how many blocks the number of non-conforming DROPPED after the block
    reparaciones_validas = [r for r in reparaciones if len(r) == 2]
    reparo = sum(1 for (a, b) in reparaciones_validas if b < a)
    return {
        "brazo": brazo, "replica": replica, "final": final,
        "bloqueos": bloqueos, "end_aceptado": end_aceptado,
        "timeout": (not end_aceptado),
        "n_reparaciones": len(reparaciones_validas),
        "reparaciones_exitosas": reparo,
        "eventos": eventos,
    }


def run():
    print("=" * 72)
    print("TIER 0 — depuración de tasa de reparación (C y D)")
    print(f"modelo={MODEL}  tareas={TAREAS}  replica={REPLICAS}  tope_rechazos={TOPE_RECHAZOS}")
    print("=" * 72)

    briefs = [_generar_brief(SEMILLA + t) for t in range(TAREAS)]
    res = {"C": {"corridas": 0, "bloqueos": 0, "reparo": 0, "n_rep": 0, "timeouts": 0},
           "D": {"corridas": 0, "bloqueos": 0, "reparo": 0, "n_rep": 0, "timeouts": 0}}
    eventos_todos = []

    for brazo in BRAZOS:
        for brief in briefs:
            for replica in range(REPLICAS):
                r = _correr(brazo, brief, replica)
                res[brazo]["corridas"] += 1
                res[brazo]["bloqueos"] += r["bloqueos"]
                res[brazo]["reparo"] += r["reparaciones_exitosas"]
                res[brazo]["n_rep"] += r["n_reparaciones"]
                res[brazo]["timeouts"] += (1 if r["timeout"] else 0)
                eventos_todos.append(r)

    print(f"\n{'brazo':<6}{'bloqueos':>9}{'reparo_exitoso':>16}{'n_rep':>7}{'tasa_rep':>9}"
          f"{'timeouts':>10}")
    print("-" * 58)
    for b in BRAZOS:
        x = res[b]
        tasa = x["reparo"] / x["n_rep"] if x["n_rep"] else 0.0
        print(f"{b:<6}{x['bloqueos']:>9}{x['reparo']:>16}{x['n_rep']:>7}{tasa:>9.2f}"
              f"{x['timeouts']:>10}")

    print("\nCriterio de paso (Fable): tasa_rep D >= 0.7 y timeouts <= 10%.")
    print("=" * 72)

    out = os.path.join(SCRIPTS, "tier0_depuracion.json")
    json.dump({"resumen": res, "eventos": eventos_todos, "briefs": briefs},
              open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"[salvado {out}]")
    return 0


if __name__ == "__main__":
    sys.exit(run())
