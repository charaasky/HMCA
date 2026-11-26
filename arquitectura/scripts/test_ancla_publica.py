#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_ancla_publica.py — P0 de Fable (5ta/6ta revisión): ancla de confianza fail-closed.

El agente podría reemplazar la clave PÚBLICA. El pin (hash sha256) lo fija el operador en un
archivo root-owned /etc/hcma/publica.pin. Fail-closed: en producción sin pin válido NO se acepta
ninguna pública; la env var HCMA_PUBLICA_PIN solo vale en modo prueba.

QUÉ LO FALSARÍA:
  - Si en producción sin pin válido se aceptara una pública (fail-open).
  - Si con pin, una pública DISTINTA se aceptara (ancla reemplazada).
  - Si el archivo/directorio con permisos inseguros se aceptara como ancla.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import firma_asimetrica as FA  # noqa: E402

# Aislamos del archivo real: apuntamos a un path inexistente para controlar los casos.
FA.ANCLA_ARCHIVO = "/tmp/inexistente_hcma_publica.pin"

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
print("[1] FAIL-CLOSED: sin pin y como AGENTE (producción) => se rechaza")
os.environ.pop("HCMA_PUBLICA_PIN", None)
os.environ.pop("HCMA_PRUEBA_OVERRIDE", None)
os.environ["HCMA_SIMULAR_AGENTE"] = "1"  # simula el agente (no runner)
check("1a. agente sin pin => pública cualquiera RECHAZADA",
      FA.ancla_publica_valida(b"\x00" * 32) is False)

print("\n[2] en modo prueba (runner) sin pin => acepta (dev)")
os.environ.pop("HCMA_SIMULAR_AGENTE", None)
os.environ["HCMA_PRUEBA_OVERRIDE"] = "1"
check("2a. modo prueba sin pin => acepta", FA.ancla_publica_valida(b"\x00" * 32) is True)

print("\n[3] con pin (env en modo prueba), una pública DISTINTA se rechaza")
pub_correcta = b"A" * 32
pub_maliciosa = b"B" * 32
pin_correcto = FA._hash_publica(pub_correcta)
os.environ["HCMA_PUBLICA_PIN"] = pin_correcto
check("3a. pública correcta coincide con el pin",
      FA.ancla_publica_valida(pub_correcta) is True)
check("3b. pública DISTINTA se rechaza (ancla reemplazada)",
      FA.ancla_publica_valida(pub_maliciosa) is False)

print("\n[4] clave_publica_bytes() con env distinta al pin => FirmaInvalida")
os.environ["HCMA_HUMANO_ED25519_PUB"] = pub_maliciosa.hex()
try:
    FA.clave_publica_bytes()
    check("4a. pública env distinta al pin => FirmaInvalida", False, "no lanzó")
except FA.FirmaInvalida:
    check("4a. pública env distinta al pin => FirmaInvalida", True)
os.environ.pop("HCMA_HUMANO_ED25519_PUB", None)
os.environ.pop("HCMA_PUBLICA_PIN", None)
os.environ.pop("HCMA_PRUEBA_OVERRIDE", None)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
