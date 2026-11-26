#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Extracts from the thesis fragment with the highest equation density."""
import json, os, re, sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prototipo import call_llm, parse_json, MODEL

HERE = os.path.dirname(os.path.abspath(__file__))

SYS_EXTRACTOR = (
    "Eres un extractor de conocimiento de ingeniería. Dado un fragmento técnico, extraé SOLO "
    "JSON (json_object):\n"
    '- "formulas": lista de {"expresion", "variables", "contexto"}\n'
    '- "tablas": lista de {"nombre", "columnas", "nota"}\n'
    '- "hechos": objeto {clave: valor} (coeficientes, factores, propiedades, normas)\n'
    '- "limitacion": string\n'
    "Reglas: sé fiel al texto; NO inventes números ni fórmulas que no estén explícitas."
)


def es_ecuacion(linea):
    return bool(re.search(r"[=×÷√∑∫±·]|[α-ωΑ-Ω]|/(?![/])|\b(MPa|kN|kN·m|mm|cm|m²|mm²)\b", linea))


def densidades(texto, ventana=6000, paso=2000):
    lineas = texto.splitlines()
    # map chars -> approximate line index to choose the window
    chunks = []
    for i in range(0, len(texto) - ventana, paso):
        frag = texto[i:i + ventana]
        n = sum(1 for l in frag.splitlines() if es_ecuacion(l))
        chunks.append((n, i, frag))
    chunks.sort(reverse=True)
    return chunks


def main():
    path = os.path.join(HERE, "documento_ingenieria.pdf")
    doc = fitz.open(path)
    texto = "\n".join(page.get_text() for page in doc)

    chunks = densidades(texto)
    print(f"=== Densidad de ecuaciones (top 3 ventanas de {6000} chars) ===\n")
    for n, i, _ in chunks[:3]:
        print(f"   offset={i:>6}  lineas_eq={n}")

    n, i, frag = chunks[0]
    print(f"\n=== Extrayendo de la ventana más densa (offset={i}, {n} líneas con ecuación) ===\n")
    out = parse_json(call_llm(f"Fragmento:\n\n{frag}", MODEL, system=SYS_EXTRACTOR))
    print(json.dumps(out, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()
