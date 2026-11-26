#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""juez_calidad.py — blind quality JUDGE to detect hollow compliance.

Fable (agente_llm verdict): the deontic gate can REDUCE omissions at the cost of producing
empty deliverables ("hollow compliance"), which is WORSE than silent omission because
it is harder to detect. This judge measures that.

BLIND = when evaluating a deliverable it does NOT receive the arm (A/B/C/D/E); only the content, the
deliverable id, and the task input. That way, the quality comparison between arms is
unbiased (the judge cannot favor/penalize the condition).

METRICS per arm (computed AFTER blind evaluation):
  - mean quality (score 0-100).
  - hollow compliance rate: deliverable that PASSES predicate P(e) (minimum structure)
    but the judge marks it as empty/generic (low score / no substance).
  - spurious rate: content that does not correspond to the input (hallucinated).

USAGE:
    PYTHONPATH=/home/.../.deps python3 arquitectura/scripts/juez_calidad.py [archivo.json]
    # default: arquitectura/scripts/agente_llm_entregables.json
"""
import os
import sys
import json

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from prototipo import KEY, parse_json, get_model_max_tokens, _add_usage, _record_llm  # noqa: E402

# The judge uses a model DIFFERENT from the agent (ideally more capable), and can be configured.
JUEZ_MODEL = os.environ.get("ARQ_JUEZ_MODEL", "anthropic/claude-haiku-4.5")


def _llm_juez(prompt, system):
    """Direct call WITHOUT cache (each artifact is unique; we don't want pseudo-replication)."""
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    try:
        mt = get_model_max_tokens(JUEZ_MODEL)
    except Exception:
        mt = 8000
    body = {"model": JUEZ_MODEL, "messages": msgs, "temperature": 0.0, "max_tokens": mt,
            "response_format": {"type": "json_object"}}
    import time
    from urllib.request import Request, urlopen
    payload = json.dumps(body).encode()
    req = Request("https://openrouter.ai/api/v1/chat/completions", data=payload,
                  headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                           "HTTP-Referer": "https://osf.io/vqx95", "X-Title": "HCMA juez de calidad"},
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
    _add_usage(JUEZ_MODEL, pt, ct)
    _record_llm(JUEZ_MODEL, pt, ct, latency_ms=(time.perf_counter() - t0) * 1000.0, cached=False)
    return parse_json(content)


SYS_JUEZ = (
    "Sos un evaluador de calidad de entregables de un asistente de ingeniería. Evaluás un "
    "artefacto aislado. NO sabés en qué condiciones fue producido. Solo te importa si el "
    "contenido es sustantivo y coherente con el input. Respondé SIEMPRE en JSON con:\n"
    "  {\"score\": 0-100, \"vacio_generico\": true/false, \"espurio\": true/false, "
    "\"justificacion\": \"<corta>\"}\n"
    "- score: calidad del contenido (0 vacío, 100 excelente).\n"
    "- vacio_generico: true si el cuerpo es una plantilla vacía o texto genérico sin relación "
    "real con el input (cumplimiento hueco).\n"
    "- espurio: true si el contenido inventa datos que no están en el input (alucinado)."
)


def evaluar_entregable(entregable_id, contenido, input_dato):
    prompt = (f"Entregable: '{entregable_id}'\n"
              f"Input de la tarea: {json.dumps(input_dato, ensure_ascii=False)}\n"
              f"Contenido producido: {json.dumps(contenido, ensure_ascii=False, default=str)}\n"
              f"Evaluá la calidad (JSON).")
    r = _llm_juez(prompt, SYS_JUEZ)
    return r or {}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(SCRIPTS, "agente_llm_entregables.json")
    if not os.path.exists(path):
        print(f"No existe {path}; corré primero experimento_agente_llm.py.")
        return 1
    data = json.load(open(path, encoding="utf-8"))
    inputs = data["input_por_tarea"]
    entregables = data["entregables"]

    print(f"Juez ciego de calidad: {JUEZ_MODEL}  ({len(entregables)} entregables a evaluar)")
    print("=" * 72)

    # evaluate BLIND: without the arm
    evaluaciones = []
    for it in entregables:
        t = it["tarea"]
        input_dato = inputs[t] if t < len(inputs) else {}
        ev = evaluar_entregable(it["entregable"], it["contenido"], input_dato)
        ev["brazo"] = it["brazo"]          # ONLY for post-hoc aggregation, not in the prompt
        ev["entregable"] = it["entregable"]
        ev["pasa_predicado"] = it.get("pasa_predicado", False)
        evaluaciones.append(ev)

    # aggregate by arm
    brazos = sorted(set(e["brazo"] for e in evaluaciones))
    print(f"\n{'brazo':<6}{'n_eval':>8}{'score_med':>11}{'hueco%':>9}{'espurio%':>10}")
    print("-" * 48)
    resumen = {}
    for b in brazos:
        es = [e for e in evaluaciones if e["brazo"] == b]
        n = len(es)
        scores = [e.get("score", 0) for e in es if isinstance(e.get("score"), (int, float))]
        score_med = sum(scores) / len(scores) if scores else 0.0
        # hollow: PASSES the predicate but the judge marks it empty/generic
        huecos = sum(1 for e in es if e.get("pasa_predicado") and e.get("vacio_generico"))
        espurios = sum(1 for e in es if e.get("espurio"))
        pct_hueco = huecos / n * 100 if n else 0
        pct_espurio = espurios / n * 100 if n else 0
        resumen[b] = {"n": n, "score_medio": round(score_med, 1),
                      "huecos": huecos, "espurios": espurios}
        print(f"{b:<6}{n:>8}{score_med:>11.1f}{pct_hueco:>9.1f}{pct_espurio:>10.1f}")

    print("\n" + "=" * 72)
    print("Lectura honesta (pre-registrada):")
    print("  - Si D (enforcement) tiene score_medio MUCHO menor que A => cumplimiento hueco:")
    print("    el gate reduce omisiones a costa de basura. Eso REFUTA el beneficio neto.")
    print("  - Si D mantiene score_medio ~ A y hueco% bajo => el gate produce entregas reales,")
    print("    no basura. El beneficio es genuino.")
    print("=" * 72)

    out = os.path.join(SCRIPTS, "juez_calidad_resultados.json")
    json.dump({"juez_model": JUEZ_MODEL, "resumen": resumen, "evaluaciones": evaluaciones},
              open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"\n[salvado {out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
