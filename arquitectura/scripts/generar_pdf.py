#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""GENERIC PDF generator for any deliverable-contract domain.

Usage:  python3 generar_pdf.py --dominio habitacion [--salida ../resultados/xxx.tex]

The PDF comes out of the SAME `build_entregable` + `render_latex` used by the console report,
so console and PDF are identical by construction (Invariant 2, INVARIANTES_DOMINIO.md).
There is no per-domain render.
"""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from prototipo import DOMINIOS, build_entregable, render_latex  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dominio", required=True, choices=sorted(DOMINIOS))
    ap.add_argument("--salida", default=None)
    args = ap.parse_args()

    dom = DOMINIOS[args.dominio]
    inputs = dom["demo_inputs"]
    Q = {o["name"]: o["value"] for o in dom["calcular"](inputs)}
    ent = build_entregable(args.dominio, dom, inputs, Q)

    salida = args.salida or os.path.join(HERE, "..", "resultados", f"{args.dominio}_diseno.tex")
    salida = os.path.abspath(salida)
    os.makedirs(os.path.dirname(salida), exist_ok=True)
    open(salida, "w", encoding="utf-8").write(render_latex(ent))

    for _ in range(2):
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
                        salida], cwd=os.path.dirname(salida), check=False)
    pdf = salida.replace(".tex", ".pdf")
    print("PDF generado:", pdf, "->", os.path.exists(pdf))


if __name__ == "__main__":
    main()
