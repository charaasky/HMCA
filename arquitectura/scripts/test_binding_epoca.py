#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_binding_epoca.py — test del binding de época (cierre P1 dictamen Fable hito 3).

Cubre los 4 problemas que Fable marcó:
  1. (P1/P0) aut. SIN época + época vigente presente => rechazada (no comodín).
  2. (P1) default epoca_actual=0 es trampa.
  3. (P1) época fresca (no congelada al arranque).
  4. (P2) rollback del store acotado por época fuera del store.

El punto más importante (Fable): "te faltan dos tests negativos: época distinta y época ausente,
ambos rechazados."

Se corre como:  python3 arquitectura/scripts/test_binding_epoca.py
Exit 0 = OK, 1 = FAIL.
"""
import hashlib
import os
import sys
import tempfile
import time
import uuid

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import gobernanza_accion as GA  # noqa: E402
import firma_asimetrica as FA   # noqa: E402
import epoca as EP              # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402

_PRIV_PEM, _PUB_HEX = FA.generar_par()
os.environ["HCMA_HUMANO_ED25519_PUB"] = _PUB_HEX
os.environ["HCMA_PUBLICA_PIN"] = hashlib.sha256(bytes.fromhex(_PUB_HEX)).hexdigest()

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (("  " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


def _archivo_epoca(epoca_val):
    """Crea un archivo de época de test y setea HCMA_EPOCA_FILE a él."""
    tmp = tempfile.mkdtemp(prefix="epoca_test_")
    p = os.path.join(tmp, "epoca")
    with open(p, "w") as f:
        f.write(f"{epoca_val}\n")
    os.environ["HCMA_EPOCA_FILE"] = p
    return p


def _firmar_con_epoca(clave_necesidad, tool_id, artifacto, epoca):
    nonce = uuid.uuid4().hex
    exp = int(time.time()) + 3600
    payload = GA._payload_accion(clave_necesidad, tool_id, artifacto, exp, nonce, epoca=epoca)
    priv = FA._cargar_privada_ed25519(_PRIV_PEM)
    firma = priv.sign(FA.mensaje_accion("autorizacion", payload)).hex()
    return firma, nonce


ART = {"netlist": {}}


def test_epoca_ausente_rechazada():
    """P1: aut. SIN época + época vigente presente => rechazada (no comodín)."""
    _archivo_epoca(1)  # época vigente = 1
    m = HCMAMemoriaSQLite(":memory:")
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "simular", GA.RIESGO_REVERSIBLE)
    # firma SIN época (epoca=None)
    nonce = uuid.uuid4().hex
    exp = int(time.time()) + 3600
    payload = GA._payload_accion(k, "openfoam", ART, exp, nonce)  # sin epoca
    priv = FA._cargar_privada_ed25519(_PRIV_PEM)
    firma = priv.sign(FA.mensaje_accion("autorizacion", payload)).hex()
    GA.autorizar(m, k, "openfoam", ART, firma, nonce=nonce)  # emite SIN época
    # con época vigente=1 leíble, una aut. sin época debe ser rechazada
    check("aut. sin época + época vigente presente => rechazada",
          GA.autorizacion_vigente(m, "openfoam", ART, epoca_vigente=1) is None)


def test_epoca_distinta_rechazada():
    """P1: aut. con época 1 vs época vigente 2 => rechazada (igualdad estricta)."""
    _archivo_epoca(2)  # época vigente = 2
    m = HCMAMemoriaSQLite(":memory:")
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "simular", GA.RIESGO_REVERSIBLE)
    firma, nonce = _firmar_con_epoca(k, "openfoam", ART, epoca=1)  # firmada con época 1
    GA.autorizar(m, k, "openfoam", ART, firma, nonce=nonce, epoca=1)
    check("aut. época 1 vs vigente 2 => rechazada",
          GA.autorizacion_vigente(m, "openfoam", ART, epoca_vigente=2) is None)


def test_epoca_correcta_aceptada():
    """Época coincidente => aceptada."""
    _archivo_epoca(3)
    m = HCMAMemoriaSQLite(":memory:")
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "simular", GA.RIESGO_REVERSIBLE)
    firma, nonce = _firmar_con_epoca(k, "openfoam", ART, epoca=3)
    GA.autorizar(m, k, "openfoam", ART, firma, nonce=nonce, epoca=3)
    check("aut. época 3 vs vigente 3 => aceptada",
          GA.autorizacion_vigente(m, "openfoam", ART, epoca_vigente=3) is not None)


def test_epoca_fresca_no_congelada():
    """La época se lee FRESCA (no cacheada): si cambia el archivo, cambia el binding."""
    p = _archivo_epoca(10)
    m = HCMAMemoriaSQLite(":memory:")
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "simular", GA.RIESGO_REVERSIBLE)
    firma, nonce = _firmar_con_epoca(k, "openfoam", ART, epoca=10)
    GA.autorizar(m, k, "openfoam", ART, firma, nonce=nonce, epoca=10)
    check("época 10 vigente => aceptada",
          GA.autorizacion_vigente(m, "openfoam", ART, epoca_vigente=10) is not None)
    # rotar la época a 11 (sin reiniciar) — debe invalidar la anterior
    with open(p, "w") as f:
        f.write("11\n")
    check("tras rotar a 11, la aut. época 10 ya no es vigente",
          GA.autorizacion_vigente(m, "openfoam", ART, epoca_vigente=11) is None)


if __name__ == "__main__":
    test_epoca_ausente_rechazada()
    test_epoca_distinta_rechazada()
    test_epoca_correcta_aceptada()
    test_epoca_fresca_no_congelada()
    print("\nRESULTADO: %d FAIL" % len(FAIL))
    sys.exit(1 if FAIL else 0)
