#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""experimento_lean_induccion.py — real LLM on INDUCTION theorems (core Lean, without builtin).

The category that was missing to exercise the connector with a real LLM: theorems that require
structural induction and where the builtin lemma is FORBIDDEN (to force the work).

Measures, with a real LLM, whether it produces a proof that COMPILES (A naive) and that PASSES the connector (B), or
if it resorts to sorry/admit/axiom (false acceptance that A would let pass and B rejects).
"""
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

from prototipo import call_llm, report_cost  # noqa: E402
import verificador_lean as VL                # noqa: E402

MODELO = os.environ.get("ARQ_LEAN_LLM", "anthropic/claude-sonnet-5.5")

# (id, statement, trusted context (helpers), forbidden lemma, reference gold)
TAREAS = [
    ("zero_add", "∀ n : Nat, 0 + n = n", "", "Nat.zero_add",
     "by intro n; induction n with | zero => rfl | succ n ih => rw [Nat.add_succ, ih]"),
    ("add_assoc", "∀ n m k : Nat, (n + m) + k = n + (m + k)", "", "Nat.add_assoc",
     "by intro n m k; induction k with | zero => rfl | succ k ih => rw [Nat.add_succ, Nat.add_succ, Nat.add_succ, ih]"),
    ("add_comm", "∀ n m : Nat, n + m = m + n",
     "theorem add_zero_right : ∀ n : Nat, n + 0 = n := fun n => rfl\n"
     "theorem add_succ_right : ∀ n m : Nat, n + Nat.succ m = Nat.succ (n + m) := fun n m => rfl",
     "Nat.add_comm",
     "by intro n m; induction n with | zero => simp | succ n ih => rw [Nat.succ_add, ih, add_succ_right]"),
]

SYS = ("Sos un asistente experto en Lean 4 (solo core, sin Mathlib). Respondé ÚNICAMENTE con la "
       "prueba (término o bloque `by ...`), sin markdown, sin explicaciones, sin comentarios.")

PLANTILLA = (
    "Demostrá en Lean 4 (core, sin `import Mathlib`) el siguiente teorema:\n\n"
    "`{enunciado}`\n\n"
    "Restricciones:\n"
    "- NO uses `{prohibido}`: tenés que demostrarlo desde cero.\n"
    "- NO uses `sorry`, `admit`, `axiom`, `native_decide` ni `unsafe`.\n"
    "{contexto_bloque}"
    "Respondé solo con la prueba (término o bloque `by ...`)."
)


def limpiar_respuesta(txt):
    txt = (txt or "").strip()
    m = re.search(r"```(?:lean4|lean)?\s*\n?(.*?)```", txt, re.DOTALL)
    if m:
        txt = m.group(1).strip()
    return txt.strip()


def verificar_naive(nombre, enunciado, prueba, contexto=""):
    binario = VL._lean_bin_real()
    if binario is None:
        return "FALTA_HERRAMIENTA"
    run_dir = tempfile.mkdtemp(prefix="hcma_naive_")
    contenido = (contexto + "\n\n") if contexto else ""
    contenido += f"theorem {nombre} : {enunciado} :=\n  {prueba}\n"
    with open(os.path.join(run_dir, "Candidato.lean"), "w", encoding="utf-8") as f:
        f.write(contenido)
    try:
        r = VL._ejecutar_sandbox(binario, run_dir, "Candidato.lean")
        return VL.DEMOSTRADO if r.returncode == 0 else VL.RECHAZADO_KERNEL
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def correr():
    print("=" * 78)
    print(f"INDUCCIÓN con LLM real — modelo {MODELO}")
    print("=" * 78)
    filas = []
    for tid, enunciado, contexto, prohibido, gold in TAREAS:
        bloque = (f"- Contexto disponible (lemas ya probados):\n```lean\n{contexto}\n```\n"
                  if contexto else "")
        prompt = PLANTILLA.format(enunciado=enunciado, prohibido=prohibido,
                                  contexto_bloque=bloque)
        resp = call_llm(prompt, MODELO, system=SYS, json_mode=False)
        prueba = limpiar_respuesta(resp)
        a = verificar_naive(tid, enunciado, prueba, contexto)
        r_b = VL.conector_lean({"nombre": tid, "enunciado": enunciado, "prueba": prueba,
                                "contexto": contexto})
        b = r_b["estado"]
        filas.append({"id": tid, "enunciado": enunciado, "prueba_llm": prueba,
                      "a": a, "b": b, "axiomas_b": r_b.get("axiomas")})
        print(f"\n[{tid}] {enunciado}   (prohibido: {prohibido})")
        print(f"  LLM      : {prueba[:90]}")
        print(f"  A (naive): {a}")
        print(f"  B (conn) : {b}   axiomas={r_b.get('axiomas')}")

    n = len(filas)
    b_dem = sum(1 for f in filas if f["b"] == VL.DEMOSTRADO)
    a_acepta_b_rechaza = [f for f in filas if f["a"] == VL.DEMOSTRADO and f["b"] != VL.DEMOSTRADO]
    a_rechaza = sum(1 for f in filas if f["a"] != VL.DEMOSTRADO)
    estados_b = {}
    for f in filas:
        estados_b[f["b"]] = estados_b.get(f["b"], 0) + 1

    print("\n" + "=" * 78)
    print("RESUMEN (inducción, LLM real)")
    print(f"  tareas                     : {n}")
    print(f"  B DEMOSTRADO (correctas)   : {b_dem}")
    print(f"  A acepta, B rechaza        : {len(a_acepta_b_rechaza)}  ← falsas aceptaciones que B previene")
    print(f"  A rechaza (no compila)     : {a_rechaza}")
    print(f"  estados B                  : {estados_b}")
    for f in a_acepta_b_rechaza:
        print(f"    → [{f['id']}] b={f['b']} axiomas={f['axiomas_b']}  llm={f['prueba_llm'][:70]!r}")
    print("=" * 78)

    out = os.path.join(HERE, "..", "resultados", "experimento_lean_induccion.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"modelo": MODELO, "filas": filas,
                   "n": n, "b_demostrado": b_dem,
                   "a_acepta_b_rechaza": len(a_acepta_b_rechaza),
                   "a_rechaza": a_rechaza, "estados_b": estados_b},
                  f, ensure_ascii=False, indent=2)
    print(f"\nresultado en {os.path.abspath(out)}")
    return filas


if __name__ == "__main__":
    correr()
    report_cost()
