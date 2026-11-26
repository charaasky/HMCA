#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Prueba del OCR matemático con visión (modelo multimodal) sobre la tesis real."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extraccion import extraer_ecuaciones_pdf

HERE = os.path.dirname(os.path.abspath(__file__))
pdf = os.path.join(HERE, "documento_ingenieria.pdf")

# páginas del capítulo de cálculo (armaduras), donde estaban las ecuaciones
r = extraer_ecuaciones_pdf(pdf, motor="vision", paginas=[251, 252])
print("motor:", r.get("motor"), "| páginas:", r.get("paginas"))
print("ecuaciones:", len(r.get("ecuaciones", [])))
for e in r.get("ecuaciones", [])[:10]:
    print("  -", e.get("latex"))
print("\ntexto_relevante (recorte):")
print((r.get("texto_relevante") or "")[:800])
