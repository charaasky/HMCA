#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_firmante_uid_guard.py — P0 de Fable: la frontera UID rota NEGA la operación (fail-closed).

QUÉ LO FALSARÍA:
  - Si `exigir_frontera_uid()` NO abortara cuando el UID actual puede leer la privada del firmante.
  - Si abortara INCLUSO cuando la privada es inaccesible (frontera bien puesta) en producción.
  - Si en modo test se rompiera (debe permitir la evaluación sin frontera real).
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import firmante_uid as FU  # noqa: E402

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
print("[1] exigir_frontera_uid: en producción con frontera rota => ABORTA (fail-closed)")
print("=" * 74)
# Simulamos frontera ROTA: un /tmp con privada legible por el UID actual, sin modo test.
import tempfile
tmp = tempfile.mkdtemp(prefix="frontera_rota_")
priv = os.path.join(tmp, "humano_ed25519.key")
with open(priv, "wb") as f:
    f.write(b"clave-falsa-legible")
os.chmod(priv, 0o644)  # legible por cualquiera = frontera ROTA

# apuntar la privada del FIRMANTE a ese path legible (sin override de test, sin modo test)
FU.SIGNER_HOME = tmp
FU.PRIVADA_SIGNER = priv
FU.PUBLICA_SIGNER = os.path.join(tmp, "humano_ed25519.pub")
FU._DIR_TEST_OVERRIDE = ""  # sin override de test -> verifica de verdad
# forzar a 'no firmante' (este proceso no es hcma-signer)
FU.SIGNER_USER = "hcma-signer-inexistente"

try:
    FU.exigir_frontera_uid()
    check("1a. frontera rota => exigir_frontera_uid ABORTA", False, "no abortó")
except RuntimeError:
    check("1a. frontera rota => exigir_frontera_uid ABORTA", True)

print("\n[2] con HCMA_SIGNER_TEST_DIR, exigir_frontera_uid NO aborta (test con claves efímeras)")
FU._DIR_TEST_OVERRIDE = tmp
FU.SIGNER_HOME = "/var/lib/hcma-signer"  # restaurar (el override de test la ignora)
try:
    r = FU.exigir_frontera_uid()
    check("2a. con SIGNER_TEST_DIR => pasa (True)", r is True, r)
except RuntimeError:
    check("2a. con SIGNER_TEST_DIR => pasa (True)", False, "abortó")

print("\n[3] verificar_frontera_uid detecta la frontera rota (legible)")
FU._DIR_TEST_OVERRIDE = ""  # quitar el override de test
ok, motivo = FU.verificar_frontera_uid()
check("3a. verificar detecta 'PUEDE leer la privada'", ok is False and "PUEDE" in motivo, motivo)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
