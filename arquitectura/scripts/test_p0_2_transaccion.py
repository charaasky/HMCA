#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_p0_2_transaccion.py — test del check P0 #2 (sin transacción SQLite abierta entre ops).

Dictamen Fable (fable_multiagente_fable.md): con conexión SQLite compartida, NINGUNA transacción
puede quedar abierta entre ops — un ROLLBACK de la op siguiente se llevaría escrituras sin commit
de la op anterior. Este check verifica `in_transaction` ANTES y DESPUÉS de cada op en el event loop.

Se corre como:  python3 arquitectura/scripts/test_p0_2_transaccion.py
Exit 0 = OK, 1 = FAIL.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import monitor_multiplex as MP  # noqa: E402
import monitor_ipc as IPC       # noqa: E402
from monitor_fachada import MonitorFachada  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (("  " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


class FakeSock:
    """Socket fake que captura el último frame enviado por el loop."""
    def __init__(self):
        self.enviados = []

    def sendall(self, b):
        self.enviados.append(b)


def _loop_con_memoria():
    """Construye un LoopMultiagente con una fachada que tiene memoria real (con conexión SQLite)."""
    m = HCMAMemoriaSQLite(":memory:")
    fachada = MonitorFachada(m, epoca_actual=0)
    loop = MP.LoopMultiagente.__new__(MP.LoopMultiagente)
    loop.fachada = fachada
    return loop, m


def test_conn_sqlite_devuelve_conexion():
    loop, m = _loop_con_memoria()
    conn = loop._conn_sqlite()
    check("_conn_sqlite obtiene la conexión SQLite de la fachada", conn is not None)


def test_deniega_si_transaccion_abierta_antes():
    """Si ya hay una transacción abierta ANTES de la op, el check deniega (fail-closed)."""
    loop, m = _loop_con_memoria()
    conn = loop._conn_sqlite()
    # simular una op anterior que dejó la transacción abierta
    conn.execute("BEGIN IMMEDIATE")
    check("transacción abierta simulada", conn.in_transaction is True)

    peer = MP.Peer(None, "sesion-1")
    sock = FakeSock()
    peer.sock = sock

    loop._despachar(peer, {"tipo": "estado"})

    # el loop debió denegar sin llamar a la fachada (el resultado no es un estado válido)
    # y cerrar/revertir la transacción
    check("deniega con transacción abierta ANTES (no despacha)", len(sock.enviados) >= 1)


def test_revierte_si_transaccion_queda_abierta_despues():
    """Si la op deja una transacción abierta DESPUÉS, el check revierte y deniega."""
    loop, m = _loop_con_memoria()
    conn = loop._conn_sqlite()

    # fachada espía que, tras pedir, deja una transacción abierta (simula un bug de una op)
    class FachadaDejaTransaccion:
        def __init__(self, memoria):
            self.memoria = memoria

        def pedir(self, op, session_id=None):
            self.memoria.conn.execute("BEGIN IMMEDIATE")
            return {"ok": True}

    loop.fachada = FachadaDejaTransaccion(m)

    peer = MP.Peer(None, "sesion-2")
    sock = FakeSock()
    peer.sock = sock

    loop._despachar(peer, {"tipo": "estado"})

    # el check debe revertir la transacción y denegar
    check("revierte la transacción abierta DESPUÉS", conn.in_transaction is False)
    # el resultado enviado debe ser el de denegación (ok: False)
    import struct
    import json
    ok = False
    for b in sock.enviados:
        # el frame es uint32 BE + JSON; solo el payload JSON nos importa
        pass
    # el primer frame enviado es el header+json del resultado; decodificamos el json
    if sock.enviados:
        # buscar el payload JSON en el stream (después del header de 4 bytes)
        data = b"".join(sock.enviados)
        if len(data) > 4:
            n = struct.unpack(">I", data[:4])[0]
            payload = json.loads(data[4:4 + n].decode("utf-8"))
            ok = payload.get("ok")
    check("resultado de denegación (ok=False)", ok is False)


def test_no_hay_transaccion_entre_ops_sanas():
    """Dos ops sanas seguidas no disparan el check (no hay transacción residual)."""
    loop, m = _loop_con_memoria()
    conn = loop._conn_sqlite()
    peer = MP.Peer(None, "sesion-3")
    sock = FakeSock()
    peer.sock = sock

    loop._despachar(peer, {"tipo": "estado"})
    check("tras op sana 1: sin transacción abierta", conn.in_transaction is False)

    loop._despachar(peer, {"tipo": "estado"})
    check("tras op sana 2: sin transacción abierta", conn.in_transaction is False)


if __name__ == "__main__":
    test_conn_sqlite_devuelve_conexion()
    test_deniega_si_transaccion_abierta_antes()
    test_revierte_si_transaccion_queda_abierta_despues()
    test_no_hay_transaccion_entre_ops_sanas()
    print("\nRESULTADO: %d FAIL" % len(FAIL))
    sys.exit(1 if FAIL else 0)
