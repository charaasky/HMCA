#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""monitor_multiplex.py — event loop multiagente del monitor (single-writer).

Dictamen de Fable 5.2 (fable_multiagente_fable.md): el monitor serializa por SESIÓN hoy
(`atender(max_iter=0)` retiene el monitor hasta EOF), lo que es un DoS trivial desde el agente
no confiable y desperdicia el escritor único. El cambio mínimo es un EVENT LOOP en el MISMO
proceso/hilo/conexión SQLite que multiplexa N conexiones y ejecuta UNA op completa por vez.

CONDICIONES P0 (Fable):
  1. Nada de estado de sesión en la fachada: el estado por-conexión vive en el objeto Peer.
  2. Ninguna transacción abierta entre ops: se verifica al entrar/salir de cada op.
  3. Identidad de sesión asignada por el monitor (los agentes son UID 101, indistinguibles).
  4. El agente es adversario: lectura no bloqueante con deadline, tope de frame, límite de
     conexiones.

Este módulo implementa SOLO el transporte (framing + multiplex). La fachada sigue siendo la
misma (única conexión SQLite); el event loop NUNCA introduce un segundo escritor.
"""
import os
import selectors
import struct
import socket
import sys
import time

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import monitor_ipc as IPC  # noqa: E402  (reusa framing estricto)


class MultiplexError(ValueError):
    """Error de protocolo/transporte (=> cierre de ese peer, no del monitor)."""


class PeerClosed(Exception):
    """Desconexión normal del peer (EOF o reset). NO es un intento de escape: es el fin de
    una conexión, no una violación del framing. El loop cierra la sesión SIN registrar incidente."""


# Allowlist del socket de GOBERNANZA (dictamen Fable): un segundo socket solo para las ACCIONES
# HUMANAS. No acepta ops de agente (registrar_tarea/tomar/resolver/pedir_egreso/...). Cualquier
# otra op => denegada (fail-closed).
GOBERNANZA_OPS = {"revisar_incidente", "parchear_incidente", "rehabilitar_incidente",
                 "cuarentenar_incidente", "eliminar_incidente", "incidentes_activos",
                 # borrado multi-firma de hechos (memoria): proponer → votar (N validadores) → ejecutar
                 "proponer_revocacion_hecho", "votar_revocacion_hecho",
                 "listar_revocaciones_hecho", "set_quorum_revocacion_hecho",
                 "restaurar_hecho",
                 # registro de claves públicas de validadores (P0 A5, multi-firma real)
                 "registrar_pubkey_hecho", "listar_pubkeys_hecho",
                 # ciclo de vida de herramientas (herramientas.py): solo el HUMANO aprueba
                 # instalación/cableado; el agente solo puede `solicitar_herramienta`.
                 "aprobar_instalacion_herramienta", "marcar_instalada_herramienta",
                 "escanear_instalar_herramienta",
                 "aprobar_cableado_herramienta", "rechazar_herramienta",
                 "listar_herramientas",
                 # acción gobernada (quorum para acciones que degradan la seguridad)
                 "proponer_accion_gobernada", "votar_accion_gobernada",
                 "listar_acciones_gobernadas",
                 # memoria: un humano puede persistir un hecho contado en el chat (claim)
                 "escribir_hecho",
                 # cache de respuestas advisory del LLM (reutilizar, no re-consultar)
                 "escribir_respuesta",
                 # ingesta de documentos largos: chunks del córtex (libro mapeado)
                 "escribir_chunk",
                 # promoción humana de un claim a verified (A2, fuente promocion:humano)
                 "promover_claim",
                 # señal explícita: crear candidata de skill (autor=modelo, unverified)
                 "crear_skill",
                 # router de cálculo: resultado determinista con linaje (fuente calculadora)
                 "escribir_calculo",
                 # backfill/migración de alias (P0.1/P0.3): arista alias:<surface> -> target y retiro
                 # del namespace legacy superseded. Acción humana (confirmación), no del agente.
                 "crear_alias", "retirar"}


class Peer:
    """Una conexión de agente: su socket + su buffer de recepción + su identidad de sesión.

    CONDICIÓN P0 #1: todo estado por-sesión vive AQUÍ, no en la fachada. La fachada sigue siendo
    stateless respecto a la sesión; el session_id se le pasa por op si la gobernanza lo exige.
    """

    __slots__ = ("sock", "buffer", "session_id", "last_activity", "ops", "saludado",
                 "agent_id", "bloqueado", "gobernanza")

    def __init__(self, sock, session_id, gobernanza=False):
        self.sock = sock
        self.buffer = b""            # bytes pendientes de armar un frame completo
        self.session_id = session_id
        self.last_activity = time.monotonic()
        self.ops = 0
        self.saludado = False        # True tras procesar el saludo de identidad (primer frame)
        self.agent_id = None         # identidad estable declarada por el lanzador (saludo)
        self.bloqueado = False       # True si el agente tiene un incidente abierto (no se le sirve)
        self.gobernanza = gobernanza  # True si es una conexión del socket de GOBERNANZA (humano)


def _extraer_frame(peer):
    """Intenta extraer UN frame completo del buffer del peer. Devuelve el dict o None si
    el frame aún está incompleto. Levanta MultiplexError si el frame es inválido (tope,
    longitud, o no-JSON)."""
    if len(peer.buffer) < 4:
        return None
    (n,) = struct.unpack(">I", peer.buffer[:4])
    if n == 0 or n > IPC.MAX_FRAME_BYTES:
        raise MultiplexError(f"longitud de frame inválida: {n}")
    if len(peer.buffer) < 4 + n:
        return None  # frame incompleto: esperar más bytes
    payload = peer.buffer[4:4 + n]
    peer.buffer = peer.buffer[4 + n:]  # consumir el frame
    try:
        import json
        return json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        # RecursionError: JSON profundamente anidado (DoS del parser del monitor).
        raise MultiplexError(f"payload no JSON válido: {e}")


class LoopMultiagente:
    """Event loop: multiplexa N peers y ejecuta una op completa por vez (single-writer)."""

    def __init__(self, fachada, listen_sock, gov_sock=None, max_conns=64, idle_timeout=120.0):
        self.fachada = fachada
        # Normalizar: un socket solo o una LISTA de sockets de agente. En modo vsock el monitor
        # escucha en DOS sockets de agente: el regular (watchers host: observador_kernel/audit)
        # y el UDS del virtio-vsock (Firecracker conecta acá cuando el guest usa vsock).
        if isinstance(listen_sock, (list, tuple)):
            self.lsocks = list(listen_sock)
        else:
            self.lsocks = [listen_sock]
        self.gov_sock = gov_sock
        self.max_conns = max_conns
        self.idle_timeout = idle_timeout
        self.sel = selectors.DefaultSelector()
        self.peers = {}          # sock -> Peer
        self._seq = 0            # contador de sesión (reinicia por proceso, ver _nueva_sesion)
        self._boot_id = self._leer_boot_id()  # irrepetible entre reinicios (P0 Fable)
        for s in self.lsocks:
            s.setblocking(False)
            self.sel.register(s, selectors.EVENT_READ, data=("agente", s))
        if gov_sock is not None:
            gov_sock.setblocking(False)
            self.sel.register(gov_sock, selectors.EVENT_READ, data="gobernanza")

    @staticmethod
    def _leer_boot_id():
        """Lee el boot-id del kernel (irrepetible entre reinicios). Fallback determinista si no
        está disponible (p.ej. contenedor sin /proc). La combinación boot-id + contador hace que
        'session-N' no renazca tras reiniciar el monitor (Fable P0: si renace, hereda lo
        persistido por la sesión vieja)."""
        try:
            with open("/proc/sys/kernel/random/boot_id", "r") as f:
                return f.read().strip()
        except OSError:
            import uuid
            return uuid.uuid4().hex  # degradación: al menos único por proceso

    def _nueva_sesion(self):
        self._seq += 1
        # boot-id (fijo por arranque) + contador (monótono por proceso) => irrepetible
        return f"{self._boot_id[:8]}:{self._seq}"

    def _aceptar(self, listen_sock, gobernanza=False):
        conn, _ = listen_sock.accept()
        if len(self.peers) >= self.max_conns:
            # límite de conexiones (P0 #4): rechazar cerrando con un frame de error
            try:
                IPC.enviar_frame(conn, {"ok": False, "detalle": "límite de conexiones alcanzado"})
            except Exception:
                pass
            conn.close()
            return
        conn.setblocking(False)
        # gobernanza: session_id fijo 'humano' (no es una sesión de agente, no se registra sesión).
        session_id = "humano" if gobernanza else self._nueva_sesion()
        peer = Peer(conn, session_id, gobernanza=gobernanza)
        self.peers[conn] = peer
        self.sel.register(conn, selectors.EVENT_READ, data=peer)
        if not gobernanza:
            self._abrir_sesion(peer)

    @staticmethod
    def _leer(peer):
        """Lee lo que esté disponible (no bloqueante) y devuelve lista de frames completos."""
        frames = []
        try:
            dato = peer.sock.recv(65536)
        except (socket.timeout, BlockingIOError):
            return frames
        except OSError:
            raise PeerClosed("peer cerró")
        if not dato:
            raise PeerClosed("EOF (peer cerró)")
        peer.buffer += dato
        peer.last_activity = time.monotonic()
        # extraer todos los frames completos que quepan
        while True:
            frame = _extraer_frame(peer)
            if frame is None:
                break
            frames.append(frame)
        return frames

    def _conn_sqlite(self):
        """Devuelve la conexión SQLite de la fachada, o None si no aplica (tests sin memoria)."""
        mem = getattr(self.fachada, "memoria", None)
        if mem is None:
            return None
        return getattr(mem, "conn", None)

    def _despachar(self, peer, op):
        """Ejecuta UNA op en la fachada y responde.

        CONDICIÓN P0 #2 (Fable multiagente): con conexión SQLite compartida, NINGUNA transacción
        puede quedar abierta entre ops — un ROLLBACK de la op siguiente se llevaría escrituras sin
        commit de la op anterior. Acá se verifica `in_transaction` ANTES y DESPUÉS de cada op:
          - ANTES: si ya hay transacción abierta => bug, denegar (fail-closed).
          - DESPUÉS: si la op dejó transacción abierta => revertir y denegar (no arrastrar estado).

        CONDICIÓN P0 #3: el session_id lo inyecta el event loop desde el Peer (NUNCA del frame
        del agente). El agente no puede declarar su propia sesión.
        """
        if getattr(peer, "bloqueado", False):
            # fail-closed: un agente con incidente abierto NO recibe servicio.
            try:
                IPC.enviar_frame(peer.sock, {"ok": False, "bloqueado": True,
                                             "detalle": "agente bloqueado por incidente de escape abierto"})
            except (OSError, IPC.FrameError):
                pass
            return
        # Socket de GOBERNANZA: solo ops humanas (allowlist). Nada de ops de agente.
        if getattr(peer, "gobernanza", False) and op.get("tipo") not in GOBERNANZA_OPS:
            try:
                IPC.enviar_frame(peer.sock, {"ok": False, "denegado": True,
                                             "detalle": "socket de gobernanza solo acepta ops humanas"})
            except (OSError, IPC.FrameError):
                pass
            return
        conn = self._conn_sqlite()
        # P0 #2 (ANTES): no debe haber transacción abierta de una op anterior.
        if conn is not None and conn.in_transaction:
            IPC.enviar_frame(peer.sock, {"ok": False,
                                        "detalle": "monitor: transacción SQLite abierta ANTES "
                                                   "de op (P0 #2, fail-closed)"})
            return

        resultado = self.fachada.pedir(op, session_id=peer.session_id)

        # P0 #2 (DESPUÉS): la op no debe dejar transacción abierta.
        if conn is not None and conn.in_transaction:
            try:
                conn.rollback()
            except Exception:
                pass
            resultado = {"ok": False,
                         "detalle": "monitor: transacción SQLite abierta DESPUÉS de op "
                                    "(P0 #2, fail-closed; revertida)"}

        IPC.enviar_frame(peer.sock, resultado)
        peer.ops += 1

    def _memoria(self):
        """Memoria gobernada de la fachada, o None si no aplica (tests sin store)."""
        return getattr(self.fachada, "memoria", None)

    def _abrir_sesion(self, peer):
        """Abre el registro de sesión al aceptar la conexión (imputación de session_id)."""
        m = self._memoria()
        if m is None:
            return
        try:
            import sesiones as S
            S.abrir(m, peer.session_id)
        except Exception as e:
            # no matar el accept por falla del registro; el write ya loguea su propio fallo.
            try:
                m.log_op("sesion.abrir.rechazo", key=peer.session_id,
                         session_id=peer.session_id, payload={"razon": str(e)})
            except Exception:
                pass

    def _saludar(self, peer, frame):
        """Procesa el saludo de identidad (PRIMER frame, escrito por el LANZADOR antes de que
        el agente exista). Completa la identidad de la sesión.

        NO responde: el saludo es fire-and-forget. El socket se entrega al agente, y una
        respuesta extra aquí rompería el par pedir/responder del agente (que recibiría el ack
        del saludo como respuesta a su primer op)."""
        peer.saludado = True
        agent_id = frame.get("agent_id")
        peer.agent_id = agent_id
        m = self._memoria()
        modelo = frame.get("modelo")
        lanzamiento = frame.get("lanzamiento")
        if m is not None:
            try:
                import sesiones as S
                S.identificar(m, peer.session_id, agent_id=agent_id, modelo=modelo,
                              lanzamiento=lanzamiento)
                m.log_op("sesion.saludo", key=peer.session_id, session_id=peer.session_id,
                         payload={"agent_id": agent_id, "modelo": modelo,
                                  "lanzamiento": lanzamiento})
                # BLOQUEO por identidad estable: si el agente tiene un incidente abierto, se
                # le niega el servicio desde el saludo (sobrevive a la reconexión).
                import incidentes as INC
                if agent_id and INC.agente_bloqueado(m, agent_id=agent_id):
                    peer.bloqueado = True
                    m.log_op("sesion.saludo.bloqueado", key=peer.session_id,
                             session_id=peer.session_id,
                             payload={"agent_id": agent_id, "razon": "incidente_abierto"})
            except Exception:
                pass

    def _registrar_incidente_escape(self, peer, modo_escape, detalle):
        """Registra y BLOQUEA un incidente de escape al detectar un intento (p.ej. frame inválido).

        Fail-closed: el incidente queda en la memoria gobernada (DETECTADO → BLOQUEADO) con la
        sesión del agente; el humano decide después si parchea y re-habilita. El agente NO puede
        auto-re-habilitarse (la transición es solo del humano)."""
        m = self._memoria()
        if m is None:
            return
        try:
            import uuid
            import incidentes as INC
            iid = uuid.uuid4().hex[:16]
            INC.registrar(m, iid, modo_escape, "transporte", peer.session_id,
                          evidencia={"detalle": detalle}, agent_id=peer.agent_id,
                          problema="intento de romper el protocolo IPC (frame inválido)",
                          detalle=detalle, comando=detalle)
            INC.bloquear(m, iid, peer.session_id, motivo="escape detectado (transporte)")
            INC.cuarentenar(m, iid, peer.session_id, motivo="aislamiento total tras bloqueo")
        except Exception:
            pass

    def _cerrar_sesion(self, peer):
        """Cierra la sesión y RECLAMA sus tareas en_curso al ver morir el socket (Fable paso 4).
        Las conexiones de GOBERNANZA no tienen sesión ni tareas: no hay nada que reclamar."""
        if getattr(peer, "gobernanza", False):
            return
        m = self._memoria()
        if m is None:
            return
        try:
            import sesiones as S
            S.cerrar(m, peer.session_id, motivo="socket_cerrado")
        except Exception:
            pass
        try:
            import tareas as T
            T.liberar_sesion(m, peer.session_id, motivo="desconexion")
        except Exception:
            pass

    def _cerrar(self, peer):
        self._cerrar_sesion(peer)
        try:
            self.sel.unregister(peer.sock)
        except Exception:
            pass
        self.peers.pop(peer.sock, None)
        try:
            peer.sock.close()
        except OSError:
            pass

    def run(self):
        """Loop principal: acepta, lee frames por peer, despacha una op, repite."""
        while True:
            eventos = self.sel.select(timeout=1.0)
            ahora = time.monotonic()
            for key, mask in eventos:
                if isinstance(key.data, tuple) and key.data[0] == "agente":
                    # socket de escucha del AGENTE (regular o vsock)
                    try:
                        self._aceptar(key.data[1], gobernanza=False)
                    except OSError:
                        continue
                    continue
                if key.data == "gobernanza":
                    # socket de escucha de GOBERNANZA (humano)
                    try:
                        self._aceptar(self.gov_sock, gobernanza=True)
                    except OSError:
                        continue
                    continue
                peer = key.data
                try:
                    frames = LoopMultiagente._leer(peer)
                except PeerClosed:
                    # Fin NORMAL de conexión (EOF/reset): no es un intento de escape. Se cierra
                    # la sesión (reclamo de tareas) sin registrar incidente.
                    self._cerrar(peer)
                    continue
                except MultiplexError as e:
                    # frame inválido = intento de romper el protocolo (vector de escape)
                    self._registrar_incidente_escape(peer, "frame_invalido", str(e))
                    self._cerrar(peer)
                    continue
                for op in frames:
                    # saludo de identidad: solo el PRIMER frame de la conexión (el lanzador lo
                    # escribe antes de lanzar el agente). Un saludo tardío es un op normal => la
                    # fachada lo rechaza como desconocido (fail-closed).
                    if op.get("tipo") == "saludo_sesion" and not peer.saludado and peer.ops == 0:
                        self._saludar(peer, op)
                        continue
                    try:
                        self._despachar(peer, op)
                    except (OSError, IPC.FrameError, MultiplexError):
                        self._cerrar(peer)
                        break
            # purgar peers inactivos (idle_timeout) — un agente colgado no retiene el monitor
            for sock, peer in list(self.peers.items()):
                if ahora - peer.last_activity > self.idle_timeout:
                    self._cerrar(peer)


def servir(fachada, listen_sock, gov_sock=None, max_conns=64, idle_timeout=120.0):
    """Punto de entrada: corre el event loop multiagente (agente + gobernanza) hasta el cierre.

    `gov_sock` es el socket de GOBERNANZA (opcional): solo acepta las ops humanas de
    incidentes (allowlist GOBERNANZA_OPS). Corre en el MISMO hilo/selector: nunca hay un
    segundo escritor SQLite."""
    loop = LoopMultiagente(fachada, listen_sock, gov_sock=gov_sock,
                           max_conns=max_conns, idle_timeout=idle_timeout)
    loop.run()
