#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""ablation_lean_abc.py — A/B/C ablation of the LEAN CONNECTOR (Fable 5.2 ruling, step 3).

Measures the contribution of the formal verification layer at detecting false acceptances.

Arms (same LLM/agentic loop, different verification):
  - A (naive): typecheck only (`lean` compiles? returncode==0). Equivalent to `lake build` without defenses.
  - B (connector): `conector_lean` = binding of the statement + axiom auditing + leanchecker.
  - C (full HCMA): B + versioned memory + deontic layer + provenance/lineage. For the FALSE
    ACCEPTANCE metric, C coincides with B (the kernel is the same); C's contribution is on
    the AUDITABILITY/PROVENANCE axis (hash of statement/proof/context, sandbox, re-check), which is
    measured separately. See `resultados/dilworth/ablation_abc.md`.

The central metric: number of EXPLOITS accepted (false acceptance) per arm.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import verificador_lean as VL  # noqa: E402
from suite_adversarial_lean import CASOS  # noqa: E402


def verificar_naive(nombre, enunciado, prueba):
    """Arm A: naive typecheck. Does `lean` compile? (no binding, no auditing, no leanchecker)."""
    binario = VL._lean_bin_real()
    if binario is None:
        return "FALTA_HERRAMIENTA"
    run_dir = tempfile.mkdtemp(prefix="hcma_naive_")
    archivo = "Candidato.lean"
    with open(os.path.join(run_dir, archivo), "w", encoding="utf-8") as f:
        f.write(f"theorem {nombre} : {enunciado} :=\n  {prueba}\n")
    try:
        r = VL._ejecutar_sandbox(binario, run_dir, archivo)
        estado = VL.DEMOSTRADO if r.returncode == 0 else VL.RECHAZADO_KERNEL
    except Exception:
        estado = "ERROR"
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    return estado


def _clasificar(espera, estado):
    """Returns 'ok' | 'falsa_aceptacion' | 'falso_negativo'."""
    demostrado = (estado == VL.DEMOSTRADO)
    if espera and demostrado:
        return "ok"
    if espera and not demostrado:
        return "falso_negativo"
    if (not espera) and demostrado:
        return "falsa_aceptacion"
    return "ok"


def correr():
    res = {}
    print("=" * 78)
    print("ABLACIÓN A/B/C del conector Lean — falsa aceptación por brazo")
    print("=" * 78)

    for etiqueta, fn in (("A (naive typecheck)", verificar_naive),
                         ("B (conector_lean)", lambda n, e, p: VL.conector_lean(
                             {"nombre": n, "enunciado": e, "prueba": p})["estado"])):
        filas = []
        for c in CASOS:
            if etiqueta.startswith("A"):
                estado = fn(c["nombre"], c["enunciado"], c["prueba"])
            else:
                estado = fn(c["nombre"], c["enunciado"], c["prueba"])
            filas.append((c["id"], c["espera"], estado, _clasificar(c["espera"], estado)))

        n_exploit = sum(1 for _, e, _, _ in filas if not e)
        n_legit = sum(1 for _, e, _, _ in filas if e)
        fa = sum(1 for _, _, _, k in filas if k == "falsa_aceptacion")
        fn_neg = sum(1 for _, _, _, k in filas if k == "falso_negativo")
        res[etiqueta] = {"total": len(filas), "exploits": n_exploit, "legitimos": n_legit,
                         "falsas_aceptaciones": fa, "falsos_negativos": fn_neg}

        print(f"\n── {etiqueta} ──")
        for cid, espera, estado, k in filas:
            marca = "← ACEPTÓ" if k == "falsa_aceptacion" else ""
            print(f"  [{cid:26s}] {estado:22s} {marca}")
        print(f"  → falsas aceptaciones: {fa}/{n_exploit} exploits   "
              f"falsos negativos: {fn_neg}/{n_legit} legítimos")

    print("\n" + "=" * 78)
    print("RESUMEN")
    for k, v in res.items():
        print(f"  {k:24s} falsas_aceptaciones={v['falsas_aceptaciones']}/{v['exploits']}  "
              f"falsos_negativos={v['falsos_negativos']}/{v['legitimos']}")
    print("\n  Arm C (HCMA completo): en FALSA ACEPTACIÓN coincide con B (mismo kernel).")
    print("  La contribución de C es el eje de auditabilidad/procedencia (hash, sandbox,")
    print("  re-check, gobernanza), medido aparte. Ver ablation_abc.md.")
    print("=" * 78)

    out = os.path.join(HERE, "..", "resultados", "ablation_lean_abc.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"\nresultado en {os.path.abspath(out)}")
    return res


if __name__ == "__main__":
    correr()
