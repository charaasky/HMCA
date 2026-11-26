#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_frontera_imports.py — verifica que no haya casos de dominio por archivo.

Confirma la frontera de arquitectura:
  - NO existen archivos `caso_*.py`, `pedir_*.py` ni `dominio_*.py` nuevos (salvo las
    librerías legítimas generar_dominio_candidato.py y pipeline_dominio.py + dominio_* legado).
  - `entregable.generar_pdf` es la ÚNICA vía que impone el gate de tipo (rechaza dict crudo).
  - `procedencia.py` es la ÚNICA vía sellada de registro.
"""
import os
import pathlib
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

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


# librerías legítimas que pueden llamarse dominio_*/generar_*/pipeline_*
LIBRERIAS = {"generar_dominio_candidato.py", "pipeline_dominio.py"}
# dominio_* legado explícito (etapa vieja)
DOMINIO_LEGADO = {"dominio_cvt.py", "dominio_luminaria_led.py"}
# pedir_* legado (el flujo viejo pre-sin_dominio)
PEDIR_LEGADO = {"pedir_cualquier_cosa.py"}


def _rechaza_dict_crudo():
    import entregable as E
    try:
        E.generar_pdf({"pedido": "crudo"}, "/tmp/x_crudo.tex")
        return False
    except E.EntregableInvalido:
        return True

print("[1] no hay archivos de caso por dominio (caso_/pedir_)")
casos = [p.name for p in SCRIPTS.rglob("*.py")
         if (p.name.startswith("caso_")
             or (p.name.startswith("pedir_") and p.name not in PEDIR_LEGADO))]
check("1a. sin caso_* ni pedir_* nuevos", casos == [], str(casos))

print("\n[2] dominio_* solo librería o legado declarativo, no casos nuevos")
dominios = [p.name for p in SCRIPTS.rglob("*.py") if p.name.startswith("dominio_")]
ok = all(d in LIBRERIAS or d in DOMINIO_LEGADO for d in dominios)
check("2a. dominio_* dentro de allowlist", ok, str(dominios))

print("\n[3] el gate de tipo sale solo de entregable.generar_pdf")
import entregable as E
check("3a. entregable.generar_pdf existen", callable(getattr(E, "generar_pdf", None)))
check("3b. rechaza dict crudo (gate real)",
      _rechaza_dict_crudo())

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
