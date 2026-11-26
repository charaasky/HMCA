#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_pedir_egreso_gate.py — test del GATE EN LÍNEA de pedir_egreso (hito 3).

Verifica el P0-1 (dictamen Fable hito 2): el camino al cable NO opera sin autorización humana
vigente atada al hash del intent, consumida atómicamente antes de reenviar. Sin autorización
=> bloqueado. Con autorización => pasa el gate (y luego reenvía al proxy, que aquí NO corre,
devolviendo 'proxy inalcanzable' — lo que prueba es que LLEGÓ al reenvío, es decir, pasó el gate).

Se corre como:  python3 arquitectura/scripts/test_pedir_egreso_gate.py
Exit 0 = OK, 1 = FAIL.
"""
import os
import sys
import time
import uuid
import hashlib

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import gobernanza_accion as GA  # noqa: E402
import firma_asimetrica as FA   # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
from monitor_fachada import MonitorFachada  # noqa: E402

# claves efímeras de test (igual que suite_adversarial), con PIN alineado a la pública efímera
_PRIV_PEM, _PUB_HEX = FA.generar_par()
os.environ["HCMA_HUMANO_ED25519_PUB"] = _PUB_HEX
# el pin de confianza debe apuntar a la pública efímera (sino la verificación rechaza el ancla).
# El pin es sha256 de los BYTES de la pública (no del hex string), igual que _hash_publica.
os.environ["HCMA_PUBLICA_PIN"] = hashlib.sha256(bytes.fromhex(_PUB_HEX)).hexdigest()

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (("  " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


def _firmar_como_humano(clave_necesidad, tool_id, artifacto, nonce=None):
    if nonce is None:
        nonce = uuid.uuid4().hex
    exp = int(time.time()) + 3600
    payload = GA._payload_accion(clave_necesidad, tool_id, artifacto, exp, nonce)
    priv = FA._cargar_privada_ed25519(_PRIV_PEM)
    firma = priv.sign(FA.mensaje_accion("autorizacion", payload)).hex()
    return firma, exp, nonce


INTENT = {
    "schema": 1, "tipo": "http", "metodo": "GET", "scheme": "https",
    "host": "example.com", "puerto": 443, "path": "/",
}

# El contrato real (Fable hito 2: "validar, puntuar riesgo y reenviar sobre el mismo objeto
# canónico"): la autorización ata el intent CANÓNICO (normalizado por validar_http), no el crudo.
import intent_canonico as IC  # noqa: E402
INTENT_CANON = IC.validar_http(INTENT)


def test_sin_autorizacion_bloqueado():
    m = HCMAMemoriaSQLite(":memory:")
    mf = MonitorFachada(m, epoca_actual=0)
    r = mf.pedir({"tipo": "pedir_egreso", "intent": INTENT_CANON})
    check("sin autorización => bloqueado", r.get("ok") is False and r.get("bloqueado") is True,
          r.get("detalle", "")[:60])


def test_con_autorizacion_pasa_gate():
    m = HCMAMemoriaSQLite(":memory:")
    mf = MonitorFachada(m, epoca_actual=0)
    # declarar necesidad + autorizar (firmar como humano) atado al intent canónico exacto
    k, _ = GA.declarar_necesidad(m, "obj", "http", "egreso web", GA.RIESGO_REVERSIBLE)
    firma, exp, nonce = _firmar_como_humano(k, "http", INTENT_CANON)
    GA.autorizar(m, k, "http", INTENT_CANON, firma, nonce=nonce)
    # ahora pedir_egreso debe PASAR el gate y reenviar (proxy no corre => inalcanzable)
    r = mf.pedir({"tipo": "pedir_egreso", "intent": INTENT_CANON})
    paso_gate = ("requiere autorización" not in r.get("detalle", ""))
    check("con autorización SÍ pasa el gate (no bloquea por falta de permiso)", paso_gate,
          r.get("detalle", "")[:60])


def test_autorizacion_consumida_no_reusable():
    m = HCMAMemoriaSQLite(":memory:")
    mf = MonitorFachada(m, epoca_actual=0)
    k, _ = GA.declarar_necesidad(m, "obj", "http", "egreso web", GA.RIESGO_REVERSIBLE)
    firma, exp, nonce = _firmar_como_humano(k, "http", INTENT_CANON)
    GA.autorizar(m, k, "http", INTENT_CANON, firma, nonce=nonce)
    r1 = mf.pedir({"tipo": "pedir_egreso", "intent": INTENT_CANON})  # consume
    r2 = mf.pedir({"tipo": "pedir_egreso", "intent": INTENT_CANON})  # segundo uso => replay
    replay_bloqueado = ("ya fue consumida" in r2.get("detalle", "")) or r2.get("bloqueado") is True
    check("autorización de un solo uso: segundo pedido es replay", replay_bloqueado,
          r2.get("detalle", "")[:60])


def test_intent_cambiado_no_reusa_autorizacion():
    # firmar para un intent; pedir OTRO intent => la autorización no aplica => bloqueado
    m = HCMAMemoriaSQLite(":memory:")
    mf = MonitorFachada(m, epoca_actual=0)
    k, _ = GA.declarar_necesidad(m, "obj", "http", "egreso web", GA.RIESGO_REVERSIBLE)
    firma, exp, nonce = _firmar_como_humano(k, "http", INTENT_CANON)
    GA.autorizar(m, k, "http", INTENT_CANON, firma, nonce=nonce)
    # intent DISTINTO (otro path) => no debería pasar el gate
    otro = dict(INTENT_CANON, path="/other")
    r = mf.pedir({"tipo": "pedir_egreso", "intent": otro})
    check("intent cambiado => autorización no aplica (bloqueado)", r.get("bloqueado") is True,
          r.get("detalle", "")[:60])


def test_mcp_rechazado():
    m = HCMAMemoriaSQLite(":memory:")
    mf = MonitorFachada(m, epoca_actual=0)
    r = mf.pedir({"tipo": "pedir_egreso", "intent": {
        "schema": 1, "tipo": "mcp", "server_id": "s", "tool": "t",
        "hash_schema": "sha256:" + "0" * 64, "args": {}}})
    check("mcp rechazado (hito propio)", r.get("ok") is False and r.get("bloqueado") is True,
          r.get("detalle", "")[:40])


if __name__ == "__main__":
    test_sin_autorizacion_bloqueado()
    test_con_autorizacion_pasa_gate()
    test_autorizacion_consumida_no_reusable()
    test_intent_cambiado_no_reusa_autorizacion()
    test_mcp_rechazado()
    print("\nRESULTADO: %d FAIL" % len(FAIL))
    sys.exit(1 if FAIL else 0)
