#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Demonstration of the search module built on top of the model.

Evaluates 4 query types (material, price, regulations, geology) to see the result:
the model returns facts + a freshness mark (estable vs drift_temporal), which is
what HCMA needs to decide whether the datum is reliable or requires confirmation/versioning.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prototipo import busqueda, Estado

estado = Estado()  # web_enabled = True by default

CONSULTAS = [
    ("material", "propiedades mecánicas del acero S235JR: límite elástico (MPa) y densidad (kg/m³)"),
    ("precio", "precio aproximado del cobre electrolítico por kg en Argentina (USD/kg)"),
    ("normativa", "normativa argentina vigente para el cálculo de estructuras de acero (CIRSOC)"),
    ("geología", "capacidad portante admisible típica (kPa) de un suelo arcilloso para fundaciones"),
]

print("=== Módulo de búsqueda híbrido (modelo → web) ===\n")
for tipo, q in CONSULTAS:
    r = busqueda(q, estado)
    print(f"── [{tipo}] {q}")
    print(f"    vigencia : {r.get('vigencia')}")
    print(f"    vía      : {r.get('via')}")
    print(f"    hechos   : {json.dumps(r.get('hechos', {}), ensure_ascii=False)}")
    if r.get('fuentes'):
        print(f"    fuentes  : {[f['url'] for f in r['fuentes']]}")
    print(f"    nota     : {r.get('nota')}")
    print()
