#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_conductual_flurbo.py — falsador real: un dominio INVENTADO pasa por el flujo genérico.

Si el flujo genérico requiere que yo agregue código de dominio para resolver un pedido nuevo,
falla este test. La spec del dominio 'flurbo' la devuelve un LLM FAKE (patched), y verificamos
que sin_dominio.resolver llega de punta a punta SIN hardcode de dominio.

  - lanzo el pedido 'flurbeame una cosa' con un LLM fake que devuelve una spec trivial.
  - verifico: estado hueco_detectado, candidata con la fórmula, inscripta unverified.
  - verifico que NO existe ningún archivo caso_*/pedir_*/dominio_* nuevo.
"""
import os
import pathlib
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generar_dominio_candidato as GDC  # noqa: E402
import sin_dominio as SD  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")
    return cond


# ── LLM fake: devuelve una spec de un dominio INVENTADO "flurbo" ──
_SPEC_FLURBO = {
    "nombre": "flurbo",
    "autor": "modelo",
    "inputs": [{"name": "insumo", "question": "insumo (unidad flurbo)", "unit": "fl"}],
    "formulas": ["flurbeo = 2 * insumo"],
    "dimensiones": {"flurbeo": "fl"},
    "ranges": {"flurbeo": (-100, 100)},
    "fuentes_citadas": ["manual del flurbo (cita ficticia)"],
    "lookup_queries": [],
}


# monkeypatch: el generador devuelve la spec fake (simula redacción del LLM) y desactiva web
_orig_gen = GDC.generar_spec_candidata
GDC.generar_spec_candidata = lambda pedido, usar_llm=False, modelo=None: _SPEC_FLURBO
SD._HAY_BUSQUEDA = False

print("[1] pedido de dominio inventado pasa por el flujo genérico sin hardcode")
m = HCMAMemoriaSQLite(":memory:")
r = SD.resolver("flurbeame una cosa", m=m, usar_llm=True, buscar_web=False)

check("1a. estado hueco_detectado", r["estado"] == "hueco_detectado", r["estado"])
check("1b. dominio_id normalizado a 'flurbo'", r["dominio_id"] == "flurbo", r["dominio_id"])
check("1c. candidata lleva la fórmula del flurbo",
      "flurbeo" in str(r["candidata"].get("formulas", [])), str(r["candidata"].get("formulas")))
check("1d. candidata autor=modelo", r["candidata"]["autor"] == "modelo")
check("1e. candidata inscripta unverified", m.current("dominio:flurbo:candidata") is None)

print("\n[2] no existe ningún archivo de caso/pedir/dominio nuevo de 'flurbo'")
nuevos = [p.name for p in SCRIPTS.glob("*.py")
          if p.name.lower().startswith(("caso_", "pedir_", "dominio_"))
          and "flurbo" not in p.name.lower()]  # no puede existir ninguno con flurbo
check("2a. no se creó un archivo de dominio flurbo", not any("flurbo" in n for n in
      [p.name for p in SCRIPTS.glob('*.py')]), "existe archivo flurbo")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
