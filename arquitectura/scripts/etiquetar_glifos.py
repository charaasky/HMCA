#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Labels a glyph sheet (glyph_atlas_N.png) with the vision oracle.

Each sheet is a 10x5 grid; below each glyph is its GID. The model
returns JSON: { "<gid>": {"char": ..., "latex": ..., "nombre": ...}, ... }.

The goal is to build ONE local GID→Unicode/LaTeX dictionary with a one-time cost.
"""
import base64, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prototipo import call_vision, parse_json, report_cost

SYS = (
    "Sos un experto en fuentes matemáticas (OpenType MATH) y en glifos de Cambria Math. "
    "Recibís una IMAGEN que es una grilla de glifos. Debajo de cada glifo está impreso su "
    "número de GID (glyph id). Devolvé SOLO JSON (json_object) con un mapa: "
    '{"<gid>": {"char": "<carácter Unicode o vacío>", "latex": "<comando LaTeX>", '
    '"nombre": "<nombre breve>", "tipo": "<letra|digito|operador|puntuacion|variante|desconocido>"}}.\n'
    "Reglas:\n"
    "1. Para letras griegas: char = la letra (ej λ), latex = \\lambda.\n"
    "2. Para operadores: latex = el comando (ej \\sum, \\int, \\leq, \\geq, \\pm, \\sqrt).\n"
    "3. Para variantes de tamaño / piezas de ensamblaje (radicales extensibles, paréntesis "
    "extensibles, etc.) que NO tienen Unicode propio: char=\"\", tipo=\"variante\", y en "
    "latex poné el comando de la pieza si lo identificás (ej \\sqrt, (, )), si no, nombre descriptivo.\n"
    "4. Si un glifo es ambiguo o no lo reconocés, marcá tipo=\"desconocido\" y dejá char=\"\".\n"
    "5. NO inventes: si no estás seguro, \"desconocido\".\n"
    "6. Respondé TODOS los GIDs visibles en la grilla."
)


def etiquetar(png, modelo=None):
    with open(png, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    out = call_vision(SYS, [b64], model=modelo)
    d = parse_json(out)
    return d


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("png")
    ap.add_argument("--out", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    d = etiquetar(args.png, modelo=args.model)
    print(json.dumps(d, ensure_ascii=False, indent=2))
    if args.out:
        json.dump(d, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n[guardado en {args.out}]")
    report_cost()
