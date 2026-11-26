#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_falsacion_gobernanza.py — test de la BATERÍA DE FALSACIÓN (P1/P2/P3).

Es el gate de CI del claim central del workshop paper (paper_hcma_workshop.tex, Tabla 1):
cada propiedad de una memoria gobernada se cumple POR CONSTRUCCIÓN en HCMA y es VIOLADA por
un baseline sin esa gobernanza (memoria asociativa sin linaje, abstracción Nuestra).

Verifica (determinista, sin seeds):
  P1  stale-use tras revisión de fuente: HCMA == 0, baseline > 0.
  P2  no-canónico por co-activación:      HCMA == 0, baseline > 0.
  P3  horizonte deóntico:                 HCMA 0 omisiones / 0 falsos cierres, baseline omite.

Uso:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/test_falsacion_gobernanza.py
Exit 0 = las 3 propiedades se cumplen (batería verde).
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import experimento_D as E  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        FAIL.append(name)


print("[P1] Revisión de fuente → stale-use (HCMA 0, baseline > 0)")
r1 = E.perturbacion_1()
check("1a. HCMA no sirve derivados stale", r1["hcma"] == 0.0, f"hcma={r1['hcma']}")
check("1b. baseline sin linaje los mantiene vivos", r1["asociativo"] > 0.0,
      f"asociativo={r1['asociativo']}")

print("\n[P2] Contaminación plausible (HCMA 0, baseline > 0)")
r2 = E.perturbacion_2()
check("2a. HCMA no cuela no-canónicos", r2["hcma"] == 0.0, f"hcma={r2['hcma']}")
check("2b. baseline cuela por co-activación", r2["asociativo"] > 0.0,
      f"asociativo={r2['asociativo']}")

print("\n[P3] Horizonte deóntico (HCMA 0 omisiones / 0 falsos cierres)")
r3 = E.perturbacion_3()
check("3a. deóntica no omite entregables", r3["con_deontica"]["omisiones"] == 0,
      f"omisiones={r3['con_deontica']['omisiones']}")
check("3b. deóntica no produce falsos cierres", r3["con_deontica"]["falsos_cierres"] == 0,
      f"falsos_cierres={r3['con_deontica']['falsos_cierres']}")
check("3c. cierre libre (B1) omite productos sin autoridad",
      r3["sin_deontica"]["omisiones"] > 0,
      f"omisiones_sin={r3['sin_deontica']['omisiones']}")

print(f"\nRESULTADO: {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
