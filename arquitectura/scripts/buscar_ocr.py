#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Searches for community OCR projects to extract formulas (LaTeX) from PDF without GPU."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prototipo import busqueda_web

CONSULTAS = [
    "mejores proyectos open source para extraer ecuaciones LaTeX desde PDF sin GPU 2026",
    "grobid vs nougat vs pix2tex vs marker para OCR de fórmulas matemáticas CPU",
    "pix2tex LaTeX-OCR github CPU requirements",
]

for q in CONSULTAS:
    r = busqueda_web(q)
    print(f"=== {q}\n")
    print("hechos:", json.dumps(r.get("hechos", {}), ensure_ascii=False))
    print("nota:", r.get("nota"))
    print("fuentes:")
    for f in r.get("fuentes", []):
        print("  -", f["url"])
    print()
