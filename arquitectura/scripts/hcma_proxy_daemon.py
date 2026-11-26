#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""hcma_proxy_daemon.py — egress network proxy daemon (UID hcma-proxy).

Fable design A (DISENO_RED_MCP.md §3): the network proxy is a SEPARATE process from the monitor and
the agent, owner of the egress network. It has NO access to the store nor the key. It receives ONLY
already-decided intents from the monitor (never direct agent connections).

Transport protocol: framed stream (uint32 BE + JSON) over the Unix socket /run/hcma-proxy.sock,
identical to monitor_ipc framing (strict send/recv). Each frame is {intent: {...}}. The proxy
responds with the snapshot or {ok:False, bloqueado:True, detalle}.

FAIL-CLOSED like the monitor: creates its OWN socket at runtime (the listener dies with the
process), without socket-activation, Restart=no. The socket is 0660 (group) and the daemon ALSO
verifies SO_PEERCRED == MONITOR_UID on each connection (double control: permission + peer
authentication). The AGENT never connects to the proxy.
"""
import os
import socket
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

import hcma_proxy as PROXY  # noqa: E402
import monitor_ipc as IPC   # noqa: E402  (reusa el framing estricto del monitor)

SOCKET_PATH = os.environ.get("HCMA_PROXY_SOCK", "/run/hcma-proxy/proxy.sock")

# UID that can connect to the proxy (only the monitor). Fable milestone 3 conditional P0: without
# SO_PEERCRED peer authentication, any local process bypasses the gate.
MONITOR_UID = int(os.environ.get("HCMA_MONITOR_UID", "999"))


def _peer_uid(conn):
    """Returns the UID of the process that connected, or None if it cannot be determined."""
    try:
        import struct
        SO_PEERCRED = 17  # linux/socket.h
        cred = conn.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, _gid = struct.unpack("3i", cred)
        return uid
    except Exception:
        return None


def _crear_socket_propio():
    """Creates the proxy's Unix socket at runtime (fail-closed, same as the monitor)."""
    if os.path.exists(SOCKET_PATH):
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
    dir_sock = os.path.dirname(SOCKET_PATH)
    if dir_sock and not os.path.isdir(dir_sock):
        try:
            os.makedirs(dir_sock, mode=0o700, exist_ok=True)
        except OSError as e:
            raise RuntimeError(f"no se pudo crear dir del socket {dir_sock}: {e}")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(SOCKET_PATH)
    # Permissions: 0660 so the monitor (dedicated group) can connect, BUT the daemon additionally
    # verifies SO_PEERCRED==MONITOR_UID on each connection (double control: permission + peer auth).
    # The group is set by the systemd unit (Group=hcma-proxy) and the monitor belongs to that group
    # (or a socket with Group= in RuntimeDirectory is used). Here we set 0660 as a floor.
    try:
        os.chmod(SOCKET_PATH, 0o660)
    except OSError:
        os.chmod(SOCKET_PATH, 0o600)  # conservative fallback
    s.listen(16)
    return s


def _atender_frame(conn, frame):
    """Processes a frame {intent: {...}} and returns the result."""
    if not isinstance(frame, dict):
        return {"ok": False, "bloqueado": True, "detalle": "frame no es dict"}
    intent = frame.get("intent")
    if intent is None:
        return {"ok": False, "bloqueado": True, "detalle": "frame sin 'intent'"}
    # nonce/epoch/hash_accion travel SEPARATE from the intent (not inside): the canonical intent
    # does not admit extra fields (execution by reconstruction). The monitor provides them after
    # consuming the authorization.
    nonce = frame.get("nonce")
    epoca = frame.get("epoca")
    hash_accion = frame.get("hash_accion")
    return PROXY.egresar_intent(intent, nonce=nonce, epoca=epoca, hash_accion=hash_accion)


def _atender_conn(conn):
    """Serves a monitor connection: reads a single intent, executes, responds."""
    try:
        frame = IPC.recibir_frame(conn)
    except IPC.FrameError as e:
        return
    resultado = _atender_frame(conn, frame)
    try:
        IPC.enviar_frame(conn, resultado)
    except (OSError, IPC.FrameError):
        pass


def main():
    print("hcma-proxy arrancando (UID %d)..." % os.getuid())
    try:
        s = _crear_socket_propio()
    except Exception as e:
        print(f"HCMA-PROXY MUERE: no se pudo crear el socket: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"escuchando en {SOCKET_PATH} (socket propio, fail-closed)")
    try:
        while True:
            try:
                conn, _ = s.accept()
                conn.settimeout(30.0)
                # peer AUTH (Fable conditional P0): only the monitor (UID) can egress.
                uid = _peer_uid(conn)
                if uid != MONITOR_UID:
                    # refuse to serve any other UID (fail-closed)
                    try:
                        IPC.enviar_frame(conn, {"ok": False, "bloqueado": True,
                                                "detalle": f"par no autorizado (uid={uid})"})
                    except (OSError, IPC.FrameError):
                        pass
                    conn.close()
                    continue
                _atender_conn(conn)
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                continue
    finally:
        try:
            s.close()
            os.unlink(SOCKET_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    main()
