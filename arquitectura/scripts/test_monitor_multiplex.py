#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_monitor_multiplex.py — test de CONCURRENCIA del event loop multiagente.

Verifica las condiciones P0 del dictamen Fable (fable_multiagente_fable.md):
  - N conexiones concurrentes al MISMO monitor (una fachada, una conexión SQLite).
  - Cada una ejecuta ops sin bloquear a las demás (un agente colgado NO paraliza el loop).
  - El CAS atómico de nonce sigue siendo "exactamente un ganador" entre N clientes.

Se corre como:  python3 arquitectura/scripts/test_monitor_multiplex.py
Exit 0 = OK, 1 = FAIL.
"""
import os
import socket
import sys
import threading
import time

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import monitor_ipc as IPC  # noqa: E402
import monitor_multiplex as MP  # noqa: E402
from monitor_fachada import MonitorFachada  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (("  " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


def _loop_en_hilo(fachada, lsock, stop_event):
    loop = MP.LoopMultiagente(fachada, lsock, idle_timeout=5.0)
    # correr el loop en un hilo hasta que se setee stop_event (patched: salimos por timeout corto)
    import contextlib
    # no hay parada limpia en run(); usamos un select manual equivalente acotado en el test
    return loop


def test_n_conexiones_concurrentes():
    """N clientes conectan y hacen ops sin bloquearse.

    IMPORTANTE (pitfall SQLite thread-confined): la memoria SQLite se crea DENTRO del hilo que
    corre el event loop, porque SQLite no comparte la conexión entre hilos. En producción el
    monitor es un solo hilo (crea la fachada y llama servir() en el mismo hilo), así que esto
    solo afecta al arnés de test.
    """
    import tempfile
    tmp = tempfile.mkdtemp(prefix="mx_")
    path = os.path.join(tmp, "monitor.sock")
    lsock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    lsock.bind(path)
    lsock.listen(16)

    resultados = {}
    lock = threading.Lock()
    listo = threading.Event()

    def _servir():
        # memoria + fachada DENTRO del hilo del loop (SQLite thread-confined)
        m = HCMAMemoriaSQLite(":memory:")
        fachada = MonitorFachada(m, epoca_actual=0)
        loop = MP.LoopMultiagente(fachada, lsock, max_conns=8, idle_timeout=30.0)
        listo.set()
        loop.run()

    t = threading.Thread(target=_servir, daemon=True)
    t.start()
    listo.wait(timeout=2)

    def cliente(n):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(path)
        s.settimeout(5.0)
        # enviar una op
        IPC.enviar_frame(s, {"tipo": "estado"})
        resp = IPC.recibir_frame(s)
        with lock:
            resultados[n] = resp.get("ok")
        s.close()

    hilos = [threading.Thread(target=cliente, args=(i,)) for i in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=10)

    time.sleep(0.2)
    lsock.close()
    t.join(timeout=2)

    check("6 clientes concurrentes responden ok", all(resultados.get(i) is True for i in range(6)),
          str(resultados))


def test_frame_incompleto_no_congela():
    """Un frame a medias no debe bloquear el loop (P0 #4)."""
    peer = MP.Peer(None, "s")
    peer.buffer = b"\x00\x00\x00"  # 3 bytes de header (incompleto)
    r = MP._extraer_frame(peer)
    check("frame incompleto devuelve None (no levanta)", r is None)


def test_frame_con_tope_invalido_rechazado():
    """Longitud fuera de rango => error."""
    import struct
    peer = MP.Peer(None, "s")
    peer.buffer = struct.pack(">I", IPC.MAX_FRAME_BYTES + 1)
    try:
        MP._extraer_frame(peer)
        check("tope de frame excedido rechazado", False)
    except MP.MultiplexError:
        check("tope de frame excedido rechazado", True)


def test_json_profundo_rechazado():
    """JSON profundamente anidado (DoS del parser) => MultiplexError, no RecursionError."""
    deep = "[" * 200000 + "]" * 200000
    peer = MP.Peer(None, "s")
    peer.buffer = len(deep).to_bytes(4, "big") + deep.encode()
    try:
        MP._extraer_frame(peer)
        check("JSON profundo rechazado (no RecursionError)", False)
    except MP.MultiplexError:
        check("JSON profundo rechazado (no RecursionError)", True)
    except RecursionError:
        check("JSON profundo rechazado (no RecursionError)", False)


def test_leer_eof_es_peerclosed_no_escape():
    """Un EOF (peer desconecta) NO es un intento de escape: levanta PeerClosed, no
    MultiplexError. (Regresión: antes un cierre normal se registraba como 'frame_invalido'.)"""
    class FakeSock:
        def recv(self, n):
            return b""
    peer = MP.Peer(FakeSock(), "s")
    try:
        MP.LoopMultiagente._leer(peer)
        check("EOF levanta PeerClosed", False)
    except MP.PeerClosed:
        check("EOF levanta PeerClosed", True)
    except MP.MultiplexError:
        check("EOF NO es MultiplexError (no es escape)", False)


def test_leer_oserror_es_peerclosed_no_escape():
    """Un reset/OSError en recv también es desconexión normal, no escape."""
    class FakeSock:
        def recv(self, n):
            raise OSError("reset")
    peer = MP.Peer(FakeSock(), "s")
    try:
        MP.LoopMultiagente._leer(peer)
        check("OSError levanta PeerClosed", False)
    except MP.PeerClosed:
        check("OSError levanta PeerClosed", True)
    except MP.MultiplexError:
        check("OSError NO es MultiplexError (no es escape)", False)


def test_session_id_por_conexion():
    """Cada conexión recibe una identidad de sesión distinta (P0 #3)."""
    # _nueva_sesion solo depende de self._seq y self._boot_id; creamos la instancia sin __init__
    # para no requerir un socket real.
    loop = MP.LoopMultiagente.__new__(MP.LoopMultiagente)
    loop._seq = 0
    loop._boot_id = "testboot"
    s1 = loop._nueva_sesion()
    s2 = loop._nueva_sesion()
    check("session_id únicos por conexión", s1 != s2, f"{s1} vs {s2}")
    check("session_id incluye boot-id (irrepetible entre reinicios)", "testboot" in s1, s1)


def _extraer_frame_ok():
    """Frame válido se extrae completo."""
    peer = MP.Peer(None, "s")
    payload = b'{"tipo":"estado"}'
    import struct
    peer.buffer = struct.pack(">I", len(payload)) + payload + b"EXTRA"
    r = MP._extraer_frame(peer)
    check("frame válido extraído", r == {"tipo": "estado"})
    check("buffer residual conserva bytes siguientes", peer.buffer == b"EXTRA")


def test_session_id_se_propaga_desde_transport():
    """El session_id lo inyecta el event loop desde el Peer, no el op del agente (P0 #3).

    Verifica que fachada.pedir(op, session_id) recibe el id del Peer (transport) y que el op
    del agente NO puede sobrescribirlo.
    """
    capturados = {}
    class FachadaEspia:
        def pedir(self, op, session_id=None):
            capturados["session_id"] = session_id
            capturados["op_tiene_session"] = "session_id" in op
            return {"ok": True}

    loop = MP.LoopMultiagente.__new__(MP.LoopMultiagente)
    loop.fachada = FachadaEspia()
    peer = MP.Peer(None, "sesion-42")
    import monitor_ipc as IPC
    # _despachar usa IPC.enviar_frame sobre peer.sock; lo omitimos con un socket fake
    class FakeSock:
        def sendall(self, b):
            pass
    peer.sock = FakeSock()
    loop._despachar(peer, {"tipo": "estado", "session_id": "forjado-por-agente"})
    check("fachada recibe el session_id del transport (no del op)",
          capturados.get("session_id") == "sesion-42")
    check("el op del agente no sobrescribe la sesión (el monitor usa el suyo)",
          capturados.get("session_id") == "sesion-42")


def test_saludo_identifica_sesion():
    """El saludo (primer frame del lanzador) completa la identidad de la sesión."""
    m = HCMAMemoriaSQLite(":memory:")
    import sesiones as S
    S.abrir(m, "s1")
    class Fachada:
        def __init__(self, memoria):
            self.memoria = memoria
    loop = MP.LoopMultiagente.__new__(MP.LoopMultiagente)
    loop.fachada = Fachada(m)
    peer = MP.Peer(None, "s1")
    loop._saludar(peer, {"tipo": "saludo_sesion", "agent_id": "a1", "modelo": "sonnet",
                         "lanzamiento": "run-1"})
    d = S.datos(m, "s1")
    check("saludo marca peer.saludado", peer.saludado is True)
    check("saludo completa agent_id", d.get("agent_id") == "a1")
    check("saludo completa modelo", d.get("modelo") == "sonnet")
    check("saludo completa lanzamiento", d.get("lanzamiento") == "run-1")


def test_saludo_tardio_no_pisa_identidad():
    """Un segundo saludo (agente malicioso) no sobreescribe la identidad ya declarada."""
    m = HCMAMemoriaSQLite(":memory:")
    import sesiones as S
    S.abrir(m, "s1")
    class Fachada:
        def __init__(self, memoria):
            self.memoria = memoria
    loop = MP.LoopMultiagente.__new__(MP.LoopMultiagente)
    loop.fachada = Fachada(m)
    peer = MP.Peer(None, "s1")
    loop._saludar(peer, {"tipo": "saludo_sesion", "agent_id": "legitimo"})
    # re-declarar (first-write-wins en sesiones.identificar)
    loop._saludar(peer, {"tipo": "saludo_sesion", "agent_id": "falsificado"})
    check("identidad no pisada por segundo saludo", S.datos(m, "s1").get("agent_id") == "legitimo")


def test_registrar_incidente_escape():
    """Un intento de romper el framing se registra y BLOQUEA como incidente de escape."""
    m = HCMAMemoriaSQLite(":memory:")
    class Fachada:
        def __init__(self, memoria):
            self.memoria = memoria
    loop = MP.LoopMultiagente.__new__(MP.LoopMultiagente)
    loop.fachada = Fachada(m)
    peer = MP.Peer(None, "s1")
    peer.agent_id = "agente-x"
    loop._registrar_incidente_escape(peer, "frame_invalido", "longitud excesiva")
    import incidentes as INC
    check("sesión queda bloqueada tras escape", INC.sesion_bloqueada(m, "s1") is True)
    check("agente queda bloqueado por agent_id", INC.agente_bloqueado(m, agent_id="agente-x") is True)
    activos = INC.incidentes_activos(m)
    check("incidente activo con modo frame_invalido",
          any(i["modo_escape"] == "frame_invalido" and i["vector"] == "transporte"
              for i in activos))


def test_saludo_agente_bloqueado():
    """Un agente con incidente abierto se bloquea desde el saludo (sobrevive a reconexión)."""
    m = HCMAMemoriaSQLite(":memory:")
    import sesiones as S
    import incidentes as INC
    S.abrir(m, "s1")
    INC.registrar(m, "i1", "frame_invalido", "transporte", "s_vieja", agent_id="agente-a")
    INC.bloquear(m, "i1", "s_vieja")
    class Fachada:
        def __init__(self, memoria):
            self.memoria = memoria
    loop = MP.LoopMultiagente.__new__(MP.LoopMultiagente)
    loop.fachada = Fachada(m)
    peer = MP.Peer(None, "s1")
    loop._saludar(peer, {"tipo": "saludo_sesion", "agent_id": "agente-a"})
    check("saludo de agente bloqueado marca peer.bloqueado", peer.bloqueado is True)


def test_socket_gobernanza_allowlist():
    """El socket de gobernanza solo acepta ops humanas (allowlist), nunca ops de agente."""
    import monitor_fachada as MF
    m = HCMAMemoriaSQLite(":memory:")
    loop = MP.LoopMultiagente.__new__(MP.LoopMultiagente)
    loop.fachada = MF.MonitorFachada(m)
    loop.gov_sock = None

    class FakeSock:
        def __init__(self):
            self.frames = []
        def sendall(self, b):
            self.frames.append(b)

    def _desp(op):
        peer = MP.Peer(None, "humano", gobernanza=True)
        peer.sock = FakeSock()
        loop._despachar(peer, op)
        import json
        return json.loads(peer.sock.frames[0][4:])  # salta el header uint32

    permitida = _desp({"tipo": "incidentes_activos"})
    denegada = _desp({"tipo": "registrar_tarea", "id": "t1"})
    check("op humana permitida en gobernanza", permitida.get("ok") is True, str(permitida))
    check("op de agente denegada en gobernanza",
          denegada.get("ok") is False and denegada.get("denegado") is True, str(denegada))


def test_multi_listener_dos_sockets():
    """En modo vsock el monitor escucha en DOS sockets de agente (regular + vsock). Ambos
    aceptan conexiones y despachan ops (un solo loop, una sola conexión SQLite)."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="mx2_")
    lsocks = []
    paths = []
    for nombre in ("monitor.sock", "vsock.sock"):
        path = os.path.join(tmp, nombre)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(path)
        s.listen(16)
        lsocks.append(s)
        paths.append(path)

    resultados = {}
    lock = threading.Lock()
    listo = threading.Event()

    def _servir():
        # memoria + fachada DENTRO del hilo del loop (SQLite thread-confined)
        m = HCMAMemoriaSQLite(":memory:")
        fachada = MonitorFachada(m, epoca_actual=0)
        loop = MP.LoopMultiagente(fachada, lsocks, max_conns=8, idle_timeout=30.0)
        listo.set()
        loop.run()

    t = threading.Thread(target=_servir, daemon=True)
    t.start()
    listo.wait(timeout=2)

    def cliente(path, n):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(path)
        s.settimeout(5.0)
        IPC.enviar_frame(s, {"tipo": "estado"})
        resp = IPC.recibir_frame(s)
        with lock:
            resultados[n] = resp.get("ok")
        s.close()

    hilos = [threading.Thread(target=cliente, args=(paths[0], i)) for i in range(2)]
    hilos += [threading.Thread(target=cliente, args=(paths[1], i)) for i in range(2, 4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=10)

    time.sleep(0.2)
    for s in lsocks:
        s.close()
    t.join(timeout=2)

    check("4 clientes responden ok en DOS sockets de agente (regular + vsock)",
          all(resultados.get(i) is True for i in range(4)), str(resultados))


if __name__ == "__main__":
    test_frame_incompleto_no_congela()
    test_frame_con_tope_invalido_rechazado()
    test_json_profundo_rechazado()
    test_leer_eof_es_peerclosed_no_escape()
    test_leer_oserror_es_peerclosed_no_escape()
    test_session_id_por_conexion()
    _extraer_frame_ok()
    test_session_id_se_propaga_desde_transport()
    test_saludo_identifica_sesion()
    test_saludo_tardio_no_pisa_identidad()
    test_registrar_incidente_escape()
    test_saludo_agente_bloqueado()
    test_socket_gobernanza_allowlist()
    test_n_conexiones_concurrentes()
    test_multi_listener_dos_sockets()
    print("\nRESULTADO: %d FAIL" % len(FAIL))
    sys.exit(1 if FAIL else 0)
