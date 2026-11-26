#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""generar_pdf_cvt.py — generates the PDF of the ferroresonant stabilizer (Gafa refrigerator).

Fixes the "asked for a design → no PDF generated" bug: registers the declarative CVT domain
(dominio_cvt.py) and passes it through the SAME generic generator of the deliverable contract
(build_entregable + render_latex), just like habitación/motor/red.

Usage:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/generar_pdf_cvt.py
"""
import os
import subprocess
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import dominio_cvt  # noqa: E402
from prototipo import build_entregable, render_latex  # noqa: E402

DOM_ID = "estabilizador_ferroresonante"


def main():
    dom = dominio_cvt.DOMINIO_CVT
    inputs = dict(dom["demo_inputs"])
    Q = dom["calcular"](inputs)

    ent = build_entregable(DOM_ID, dom, inputs, Q)

    resultados = os.path.join(SCRIPTS, "..", "resultados")
    resultados = os.path.abspath(resultados)
    os.makedirs(resultados, exist_ok=True)
    salida = os.path.join(resultados, f"{DOM_ID}_gafa.tex")
    open(salida, "w", encoding="utf-8").write(render_latex(ent))

    for _ in range(2):
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
                        salida], cwd=os.path.dirname(salida), check=False)
    pdf = salida.replace(".tex", ".pdf")
    ok = os.path.exists(pdf)
    print("PDF generado:", pdf, "->", ok)

    # also print to console (same tuple, by construction identical to the PDF)
    from prototipo import render_consola
    print("\n" + render_consola(ent))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
