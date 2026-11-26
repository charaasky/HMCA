#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""LOCAL formula extractor (deterministic, no LLM).

Reconstructs lines from the positioned words in the PDF text layer
(PyMuPDF) and detects the ones that look like equations. It is the "local/mature" path that aims to
replace the vision LLM, which today is used only as a comparative reference.
"""
import os, re, sys

import fitz  # pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MATEMATICA = re.compile(
    r"[=×÷√∑∫±·∈≤≥]|[α-ωΑ-Ω]|\b(MPa|kN|mm|cm|m²|mm²|kPa)\b|\$|\\frac|\\sum"
)


def extraer_lineas(pdf, paginas):
    """Reconstructs lines (sorted by x) grouping words by (block, line)."""
    doc = fitz.open(pdf)
    por_pagina = {}
    for i in paginas:
        lineas = {}
        for w in doc[i].get_text("words"):
            x0, y0, x1, y1, text, block, line_no, wn = w
            lineas.setdefault((block, line_no), []).append((x0, text))
        por_pagina[i + 1] = []
        for (_b, _l), ws in sorted(lineas.items(), key=lambda kv: kv[0]):
            ws.sort(key=lambda t: t[0])
            por_pagina[i + 1].append(" ".join(t for _, t in ws).strip())
    return por_pagina


def extraer_ecuaciones_local(pdf, paginas):
    """Returns the candidate equation lines (deterministic, no LLM)."""
    por_pagina = extraer_lineas(pdf, paginas)
    resultado = []
    for pag, lineas in por_pagina.items():
        for linea in lineas:
            if not linea:
                continue
            if len(MATEMATICA.findall(linea)) >= 1:
                resultado.append({"pagina": pag, "linea": linea})
    return resultado


if __name__ == "__main__":
    import json, os
    HERE = os.path.dirname(os.path.abspath(__file__))
    pdf = os.path.join(HERE, "documento_ingenieria.pdf")
    paginas = [78, 249, 232]  # the candidates detected before
    r = extraer_ecuaciones_local(pdf, paginas)
    print(f"=== extractor LOCAL (determinista) — {len(r)} líneas candidatas ===\n")
    for e in r:
        print(f"  [pág {e['pagina']}] {e['linea'][:120]}")
