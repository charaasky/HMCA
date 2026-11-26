#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_verificador_fisico.py — test de falsación del chequeo cruzado de física (I-SIM).

Prueba que el verificador CONECTA la física: un observable simulado/derivado se confronta contra
la ley física objetivo, y una ALUCINACIÓN de parámetro (cristal de frecuencia errónea) se
DETECTA como incoherente. Sin esto, el sistema le creería al LLM un dato físicamente falso.

QUÉ LO FALSARÍA:
  - Si una mutación del cristal (32700 Hz en vez de 32768 Hz) diera `coherente=True`, el
    verificador estaría roto (auto-confirmación contra la propia aritmética, no contra el
    objetivo).
  - Si el `extract_hash` cambiara entre dos corridas idénticas, no sería determinista.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import verificador_fisico as VF  # noqa: E402
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


def mem():
    return HCMAMemoriaSQLite(":memory:")


print("=" * 74)
print("[1] división binaria correcta (cristal 32768 Hz) -> coherente")
print("=" * 74)
pl_ok = VF.plantilla_division_binaria(32768.0, 15, f_objetivo_Hz=1.0)
v = VF.verificar(pl_ok)
check("1a. f_salida = 1.0 Hz", abs(v["simulada"] - 1.0) < 1e-9, v["simulada"])
check("1b. coherente contra objetivo", v["coherente"] is True, v["coherente"])

print("\n[2] FALSACIÓN: cristal erróneo 32700 Hz DEBE ser incoherente")
pl_bad = VF.plantilla_division_binaria(32700.0, 15, f_objetivo_Hz=1.0)
vb = VF.verificar(pl_bad)
check("2a. f_salida != 1.0", abs(vb["simulada"] - 1.0) > 1e-6, vb["simulada"])
check("2b. coherente == False (detecta alucinación de frecuencia)",
      vb["coherente"] is False, vb["coherente"])

print("\n[3] determinismo: dos corridas idénticas -> mismo extract_hash")
v_a = VF.verificar(pl_ok)
v_b = VF.verificar(pl_ok)
check("3a. extract_hash idéntico", v_a["extract_hash"] == v_b["extract_hash"],
      f"{v_a['extract_hash']} vs {v_b['extract_hash']}")
check("3b. plantilla_hash idéntico", v_a["plantilla_hash"] == v_b["plantilla_hash"])

print("\n[4] integración HCMA: sim: es DERIVADO (calculadora), no raíz 'tabla'")
m = mem()
p = VF.plantilla_division_binaria(32768.0, 15)
vx = VF.verificar(p)
clave, version = VF.registrar_simulacion(m, p, vx)
# I-SIM: sin insumos autoritativos (params_versions vacío), el hecho NO se auto-verifica.
# 'calculadora' sin linaje => unverified. Esto es CORRECTO (el sim es derivado, no raíz).
f = m.get_version(clave, version)
check("4a. sim:* existe por version (clave derivada)", f is not None, clave)
check("4b. fuente = 'calculadora' (NUNCA 'tabla')", f["fuente"] == "calculadora", f["fuente"])
check("4c. sin insumos autoritativos NO se auto-verifica (I-SIM)",
      f["verified"] is False, f["verified"])

# con un insumo autoritativo real (param vigente) el sim SÍ hereda autoridad
m2 = mem()
v_param = m2.write("fcristal_Hz", 32768.0, unit="Hz", fuente="ingeniero", verified=True)
clave2, version2 = VF.registrar_simulacion(m2, p, vx, params_versions={"fcristal_Hz": v_param})
f2 = m2.get_version(clave2, version2)
# el write con derived_from a fcristal_Hz (verified+authority) => verified + authority
check("4d. con param autoritativo, sim hereda autoridad (verified)",
      f2 is not None and f2["verified"] is True,
      None if f2 is None else f2["verified"])

print("\n[5] Resonancia LC: chequeo cruzado ngspice vs analítica")
pl_lc = VF.plantilla_resonancia_lc(L_mH=30.81, C_uF=328.8, f_objetivo_Hz=50.0)
vlc = VF.verificar(pl_lc)
check("5a. coherente (error < 1%)", vlc["coherente"] is True, vlc.get("coherente"))
check("5b. fuente_simulacion = ngspice", vlc.get("fuente_simulacion") == "ngspice",
      vlc.get("fuente_simulacion"))

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
