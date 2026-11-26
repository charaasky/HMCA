#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_ligado_sesion_autorizacion.py — test adversarial del LIGADO de sesión a autorización.

Dictamen Fable (fable_sesion_autorizacion_fable.md): "B intenta consumir lo de A, queda bloqueado
y la autorización de A sigue intacta." Verifica el hito 3c completo:
  - la sesión se copia de la NECESIDAD a la autorización al autorizar (no del agente);
  - autorizacion_vigente filtra por sesión;
  - marcar_usada (CAS) exige la sesión como predicado: un intento desde sesión ajena NO consume.

Se corre como:  python3 arquitectura/scripts/test_ligado_sesion_autorizacion.py
Exit 0 = OK, 1 = FAIL.
"""
import hashlib
import os
import sys
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
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402

_PRIV_PEM, _PUB_HEX = FA.generar_par()
os.environ["HCMA_HUMANO_ED25519_PUB"] = _PUB_HEX
os.environ["HCMA_PUBLICA_PIN"] = hashlib.sha256(bytes.fromhex(_PUB_HEX)).hexdigest()

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (("  " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


def _firmar(clave_necesidad, tool_id, artifacto):
    nonce = uuid.uuid4().hex
    exp = int(time.time()) + 3600
    payload = GA._payload_accion(clave_necesidad, tool_id, artifacto, exp, nonce)
    priv = FA._cargar_privada_ed25519(_PRIV_PEM)
    firma = priv.sign(FA.mensaje_accion("autorizacion", payload)).hex()
    return firma, nonce


ART = {"netlist": {}}


def test_sesion_copiada_de_necesidad():
    """Al autorizar, la sesión se copia de la NECESIDAD (no la declara el agente)."""
    m = HCMAMemoriaSQLite(":memory:")
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "simular", GA.RIESGO_REVERSIBLE,
                                 session_id="boot1:10")
    firma, nonce = _firmar(k, "openfoam", ART)
    ak, _ = GA.autorizar(m, k, "openfoam", ART, firma, nonce=nonce)
    f = m.current(ak)
    val = f.get("value") or {}
    check("autorización hereda session_id de la necesidad", val.get("session_id") == "boot1:10",
          str(val.get("session_id")))


def test_filtro_por_sesion_vigente():
    """autorizacion_vigente filtra por sesión: la sesión correcta la ve, la ajena no."""
    m = HCMAMemoriaSQLite(":memory:")
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "simular", GA.RIESGO_REVERSIBLE,
                                 session_id="boot1:20")
    firma, nonce = _firmar(k, "openfoam", ART)
    GA.autorizar(m, k, "openfoam", ART, firma, nonce=nonce)
    check("sesión correcta ve la autorización vigente",
          GA.autorizacion_vigente(m, "openfoam", ART, session_id="boot1:20") is not None)
    check("sesión ajena NO ve la autorización",
          GA.autorizacion_vigente(m, "openfoam", ART, session_id="boot1:21") is None)


def test_cas_no_consume_desde_sesion_ajena():
    """El CAS exige sesión como predicado: un intento desde sesión ajena NO consume nada, y la
    autorización queda INTACTA (el ataque adversarial que Fable pidió)."""
    m = HCMAMemoriaSQLite(":memory:")
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "simular", GA.RIESGO_REVERSIBLE,
                                 session_id="boot1:30")
    firma, nonce = _firmar(k, "openfoam", ART)
    ak, _ = GA.autorizar(m, k, "openfoam", ART, firma, nonce=nonce)
    # B (sesión ajena) intenta consumir lo de A
    r_b = GA.marcar_usada(m, ak, session_id="boot1:31")
    check("B (sesión ajena) NO consume la autorización de A", r_b is False, str(r_b))
    # la autorización de A sigue intacta y consumible por A
    check("A sigue viendo su autorización vigente",
          GA.autorizacion_vigente(m, "openfoam", ART, session_id="boot1:30") is not None)
    # A la consume correctamente
    r_a = GA.marcar_usada(m, ak, session_id="boot1:30")
    check("A consume su autorización correctamente", r_a is True, str(r_a))
    # tras consumir A, ya no está vigente
    check("tras consumir A, la autorización ya no está vigente",
          GA.autorizacion_vigente(m, "openfoam", ART, session_id="boot1:30") is None)


def test_fail_closed_sin_sesion():
    """Una autorización sin sesión (necesidad sin session_id) NO es consumible con filtro activo."""
    m = HCMAMemoriaSQLite(":memory:")
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "simular", GA.RIESGO_REVERSIBLE)  # sin sesión
    firma, nonce = _firmar(k, "openfoam", ART)
    ak, _ = GA.autorizar(m, k, "openfoam", ART, firma, nonce=nonce)
    # con filtro de sesión activo, una autorización sin sesión (None) no coincide con "boot1:40"
    check("autorización sin sesión no se consume desde una sesión concreta",
          GA.autorizacion_vigente(m, "openfoam", ART, session_id="boot1:40") is None)


if __name__ == "__main__":
    test_sesion_copiada_de_necesidad()
    test_filtro_por_sesion_vigente()
    test_cas_no_consume_desde_sesion_ajena()
    test_fail_closed_sin_sesion()
    print("\nRESULTADO: %d FAIL" % len(FAIL))
    sys.exit(1 if FAIL else 0)
