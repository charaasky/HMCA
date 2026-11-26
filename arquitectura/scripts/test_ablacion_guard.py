#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_ablacion_guard.py — P0 de Fable: los flags de ablación son inalcanzables en producción.

Rediseño por UID (5ta revisión): la 'llave maestra' HCMA_TEST_MODE se eliminó. Ahora la autoridad
de apagar defensas es el UID del runner de prueba (humano/root), NO una env var que el agente
pueda setear. La lectura del flag (`activo`) devuelve SIEMPRE True para un no-runner.

QUÉ LO FALSARÍA:
  - Si un NO-runner pudiera apagar una defensa (A2 desactiva el monitor).
  - Si `activo()` devolviera False para un no-runner cuando el estado interno quedó apagado.
  - Si el runner de prueba no pudiera apagar (rompería la matriz de ablación).
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# Arrancar SIMULANDO el agente (producción: runtime corre como hcma-agent, no puede apagar)
os.environ["HCMA_SIMULAR_AGENTE"] = "1"
os.environ.pop("HCMA_PRUEBA_OVERRIDE", None)

import ablacion as AB  # noqa: E402

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
print("[1] P0 (llave maestra cerrada): un NO-runner NO puede apagar una defensa")
print("=" * 74)
AB.reset()
try:
    AB.apagar(AB.SANDBOX_ALLOWLIST)
    check("1a. apagar como no-runner lanza RuntimeError", False, "no lanzó")
except RuntimeError:
    check("1a. apagar como no-runner lanza RuntimeError", True)
check("1b. la defensa sigue ACTIVA (activo devuelve True)", AB.activo(AB.SANDBOX_ALLOWLIST) is True)

print("\n[2] P0 (lectura del flag): aunque el estado interno quede apagado, el no-runner ve ACTIVA")
# forzamos el estado interno a False (como si algo lo hubiera mutado); el no-runner debe
# seguir viendo la defensa ACTIVA (activo() protege en la LECTURA, no solo en apagar()).
AB._ESTADO[AB.SANDBOX_ALLOWLIST] = False
check("2a. estado interno forzado a False, activo() (no-runner) sigue True",
      AB.activo(AB.SANDBOX_ALLOWLIST) is True)
AB._ESTADO[AB.SANDBOX_ALLOWLIST] = True  # limpiar

print("\n[3] P0: defensas_apagadas() para un no-runner devuelve vacío (no expone estado)")
check("3a. no-runner no ve defensas apagadas", AB.defensas_apagadas() == [])

print("\n[4] P0: arranque_verificado() para un no-runner siempre True (defensas prendidas por construcción)")
check("4a. arranque_verificado (no-runner) => True", AB.arranque_verificado() is True)

print("\n[5] runner de prueba (humano/root) SÍ puede apagar (la ablación no se rompe)")
os.environ.pop("HCMA_SIMULAR_AGENTE", None)  # ya no simulamos el agente
os.environ["HCMA_PRUEBA_OVERRIDE"] = "1"
AB.apagar(AB.SANDBOX_ALLOWLIST)
check("5a. runner de prueba apaga y activo() refleja False",
      AB.activo(AB.SANDBOX_ALLOWLIST) is False)
check("5b. defensas_apagadas() muestra el mecanismo (para el runner)",
      AB.SANDBOX_ALLOWLIST in AB.defensas_apagadas())
AB.reset()
os.environ.pop("HCMA_PRUEBA_OVERRIDE", None)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
