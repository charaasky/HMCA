#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_epoca.py — ÉPOCA (binding anti-restore) para autorizaciones.

P0 de Fable (fable_nonce_fable.md): la época se incrementa en todo restore/rollback, fuera del
store, e invalida de golpe las autorizaciones emitidas antes. El nonce solo NO alcanza ante
restore del host completo.

QUÉ LO FALSARÍA:
  - Si una autorización de época vieja verificara con la época vigente (replay por restore).
  - Si incrementar_epoca con archivo ausente creara uno nuevo (reinicía el contador; fail-closed).
  - Si la época entra al payload firmado y cambiarla invalida la firma.
"""
import os
import sys
import tempfile

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import epoca as EP  # noqa: E402
import gobernanza_accion as GA  # noqa: E402
import firma_asimetrica as FA  # noqa: E402

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


tmp = tempfile.mkdtemp(prefix="epoca_test_")
EPOCA_FILE = os.path.join(tmp, "epoca")

print("=" * 74)
print("[1] época: fail-closed ante archivo ausente")
try:
    EP.epoca_actual(EPOCA_FILE)
    check("1a. ausente => EpocaError", False, "no lanzó")
except EP.EpocaError:
    check("1a. ausente => EpocaError", True)

print("\n[2] época: incrementar monótono")
with open(EPOCA_FILE, "w") as f:
    f.write("0\n")
check("2a. actual = 0", EP.epoca_actual(EPOCA_FILE) == 0)
nueva = EP.incrementar_epoca(EPOCA_FILE)
check("2b. incrementar => 1", nueva == 1)
check("2c. actual = 1", EP.epoca_actual(EPOCA_FILE) == 1)

print("\n[3] binding: la época entra al payload firmado y cambiarla invalida la firma")
# generamos un par efímero para firmar/verificar
priv_pem, pub_hex = FA.generar_par()
os.environ["HCMA_HUMANO_ED25519_PUB"] = pub_hex
# el pin de confianza debe apuntar a la pública efímera (como test_gobernanza_accion),
# sino verificar() rechaza la pública por no coincidir con el ancla root-owned.
import hashlib as _hl
os.environ["HCMA_PUBLICA_PIN"] = _hl.sha256(bytes.fromhex(pub_hex)).hexdigest()
os.environ["HCMA_PRUEBA_OVERRIDE"] = "1"
art = {"netlist": "R1 1 2 1k"}
nonce = "abc" * 8
exp = 1234567890
payload_ep0 = GA._payload_accion("necesidad:eva:openfoam", "openfoam", art, exp, nonce, epoca=0)
payload_ep1 = GA._payload_accion("necesidad:eva:openfoam", "openfoam", art, exp, nonce, epoca=1)
check("3a. época entra al payload", payload_ep0.get("epoca") == 0)
check("3b. época distinta => payload distinto", payload_ep0 != payload_ep1)
# firmar para epoca 0 y verificar contra epoca 1 debe fallar
from cryptography.hazmat.primitives import serialization as _ser
priv = _ser.load_pem_private_key(priv_pem, password=None)
firma_ep0 = priv.sign(FA.mensaje_accion("autorizacion", payload_ep0)).hex()
ok_ep0 = FA.verificar("autorizacion", payload_ep0, firma_ep0)
ok_ep1_con_firma_ep0 = FA.verificar("autorizacion", payload_ep1, firma_ep0)
check("3c. firma ep0 verifica contra payload ep0", ok_ep0 is True)
check("3d. firma ep0 NO verifica contra payload ep1 (binding)", ok_ep1_con_firma_ep0 is False)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
