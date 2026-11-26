#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_monitor_ipc.py — test del FRAMING del protocolo IPC (capa 2 de Fable).

Fable: la capa de protocolo se prueba contra input hostil: frame con longitud excesiva,
payload no JSON, replays, y round-trip servidor<->cliente. Acá probamos el framing y el
round-trip servidor real <-> cliente real sobre un socketpair (transporte en memoria,
NO mock a mano).
"""
import os
import socket
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

import monitor_ipc as IPC  # noqa: E402
import monitor_fachada as MF  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402

FAIL = []


def check(nombre, cond, detalle=""):
    if cond:
        print(f"  ✅ {nombre}")
    else:
        print(f"  ❌ {nombre} {detalle}")
        FAIL.append(nombre)


def par_sockets():
    a, b = socket.socketpair()
    a.settimeout(10.0)
    b.settimeout(10.0)
    return a, b


print("=== [1] round-trip servidor -> cliente (transporte real, no mock) ===")
srv_sock, cli_sock = par_sockets()
resultados = {}


def _servir():
    # SQLite no comparte conexión entre threads (pitfall conocido): la memoria se crea
    # DENTRO del thread del servidor, que es el proceso/thread que la usa.
    m = HCMAMemoriaSQLite(":memory:")
    fachada = MF.MonitorFachada(m)
    servidor = IPC.ServidorMonitor(fachada, srv_sock)
    servidor.atender(max_iter=0)
    srv_sock.close()


t = threading.Thread(target=_servir)
t.start()
cliente = IPC.ClienteMonitor(sock=cli_sock)
r = cliente.pedir({"tipo": "estado"})
check("1a. estado round-trip ok", r.get("ok") is True)
r = cliente.pedir({"tipo": "propuesta_write", "key": "k", "value": 1,
                   "fuente": "calculadora"})
check("1b. propuesta_write round-trip ok", r.get("ok") is True)
r = cliente.pedir({"tipo": "leer_clave", "key": "k"})
check("1c. leer_clave round-trip ok (None esperado)", r.get("fact") is None)
# el cliente cierra => el servidor ve EOF y termina
cli_sock.close()
t.join(timeout=5)
check("1d. servidor terminó limpio", not t.is_alive())
cli_sock.close()

print("=== [2] frame con longitud excesiva -> cierre ===")
# estas secciones corren en el thread principal; la memoria se crea acá (mismo thread).
fachada2 = MF.MonitorFachada(HCMAMemoriaSQLite(":memory:"))
srv_sock2, cli_sock2 = par_sockets()
servidor2 = IPC.ServidorMonitor(fachada2, srv_sock2)
# escribir un header que declara longitud > MAX_FRAME_BYTES
cli_sock2.send(__import__("struct").pack(">I", IPC.MAX_FRAME_BYTES + 1))
n = servidor2.atender(max_iter=1)
check("2a. servidor cierra ante longitud excesiva (0 ops)", n == 0)
cli_sock2.close()
srv_sock2.close()

print("=== [3] payload no JSON -> cierre ===")
fachada3 = MF.MonitorFachada(HCMAMemoriaSQLite(":memory:"))
srv_sock3, cli_sock3 = par_sockets()
servidor3 = IPC.ServidorMonitor(fachada3, srv_sock3)
body = b"esto no es json"
cli_sock3.send(b"\x00\x00\x00\x0e" + body)  # longitud 14
n = servidor3.atender(max_iter=1)
check("3a. servidor cierra ante JSON inválido (0 ops)", n == 0)
cli_sock3.close()
srv_sock3.close()

print("\nRESULTADO:", len(FAIL), "FAIL")
sys.exit(1 if FAIL else 0)
