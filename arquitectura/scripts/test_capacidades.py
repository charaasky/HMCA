#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_capacidades.py — test de la conciencia de HERRAMIENTAS del sistema.

Prueba que el sistema MODELa la carencia de herramienta como dato gobernado: detecta si una
herramienta está disponible y, si no, PROPONE instalarla — en vez de degradar en silencio o
que el desarrollador lo resuelva a mano.

QUÉ LO FALSARÍA:
  - Si una herramienta AUSENTE (kicad) se reportara como disponible, el chequeo estaría roto.
  - Si una herramienta PRESENTE (ngspice) se reportara como faltante, el path estaría mal.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import capacidades as CAP  # noqa: E402
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


print("=" * 74)
print("[1] detección de disponibilidad")
print("=" * 74)
v_ngspice = CAP.verificar_herramienta("ngspice")
check("1a. ngspice disponible (está en .deps)", v_ngspice["disponible"] is True,
      v_ngspice["detalle"])

v_moldex = CAP.verificar_herramienta("moldex3d")
check("1b. moldex3d NO disponible (software comercial sin licencia)",
      v_moldex["disponible"] is False, v_moldex["detalle"])

print("\n[2] propuesta de herramienta faltante")
p = CAP.proponer_herramienta_faltante(None, "moldex3d")
check("2a. marca falta=True", p["falta"] is True)
check("2b. trae instrucción de instalación", bool(p.get("instalacion")), p.get("instalacion"))
check("2c. trae alternativa", bool(p.get("alternativa")), p.get("alternativa"))

print("\n[3] herramientas_faltantes() filtra solo las ausentes")
# kicad YA está instalado (lo instalamos), así que no es 'faltante'.
# Usamos una herramienta claramente ausente (moldex3d) para probar el filtro.
faltan = CAP.herramientas_faltantes(["moldex3d", "ngspice"])
ids = [h["id"] for h in faltan]
check("3a. incluye moldex3d (ausente)", "moldex3d" in ids, ids)
check("3b. NO incluye ngspice (disponible)", "ngspice" not in ids, ids)

print("\n[3b] mapeo tipo de dominio -> herramienta (sin depender del LLM)")
check("3b1. goma EVA -> openfoam (CFD de polimeros, disponible)",
      "openfoam" in CAP.herramientas_para_pedido("fabricar goma EVA espumada"),
      CAP.herramientas_para_pedido("fabricar goma EVA espumada"))
check("3b1b. openfoam disponible (instalado en .deps)",
      CAP.verificar_herramienta("openfoam")["disponible"] is True,
      CAP.verificar_herramienta("openfoam")["detalle"])
check("3b2. teclado PCB -> kicad",
      "kicad" in CAP.herramientas_para_pedido("teclado mecanico PCB"),
      CAP.herramientas_para_pedido("teclado mecanico PCB"))
check("3b3. reloj electronico -> kicad",
      "kicad" in CAP.herramientas_para_pedido("reloj digital electronico"),
      CAP.herramientas_para_pedido("reloj digital electronico"))
check("3b4. motor (sin match) -> vacio",
      CAP.herramientas_para_pedido("motor electrico") == [],
      CAP.herramientas_para_pedido("motor electrico"))

print("\n[4] registro en HCMA como hecho derivado (calculadora)")
m = HCMAMemoriaSQLite(":memory:")
key, version, v = CAP.registrar_estado(m, "kicad")
f = m.get_version(key, version)
check("4a. tool:<id>:estado existe", f is not None, key)
check("4b. fuente = calculadora (derivado, no raíz)", f["fuente"] == "calculadora", f["fuente"])

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
