#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""monitor_ipc.py — Phase 2 (step 2): monitor IPC transport (strict framing).

Fable ruling (fable_fase2_monitor_fable.md):
  - Transport = inherited FD (pipe/socketpair), NOT stdio fd 0/1, NOT a Unix socket.
  - Strict framing: length prefix, cap applied BEFORE parsing, timeouts. An invalid frame CLOSES
    the session, without resynchronizing.
  - The monitor is NOT runsc's parent: a dumb launcher creates the FD pair and hands one end to
    each side.
  - Any monitor exception is denial.

Frame protocol (binary, over a socket):
  ┌──────────────┬──────────────────────────────────────────────┐
  │ uint32 BE    │          payload JSON (op)                   │
  │ n = len(json)│          UTF-8, validated on parse           │
  └──────────────┴──────────────────────────────────────────────┘
  Response: same format (uint32 BE + result JSON).

Rules:
  - The length prefix is read FIRST; if n > MAX_FRAME_BYTES (1 MiB) => close.
  - The payload is parsed with json.loads; if it fails => close (no resynchronization).
  - Read timeout => close (the agent stops).
  - socket.recv/sendall is used (not os.read of the fileno) so settimeout applies.
"""
import json
import os
import socket
import struct
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

MAX_FRAME_BYTES = 1 << 20  # 1 MiB (cap before parsing)

# Default descriptor inside the sandbox (the launcher passes it with `runsc -pass-fd M:3`).
AGENT_FD = int(os.environ.get("HCMA_AGENT_FD", "3"))


class FrameError(ValueError):
    """Invalid frame (negative/excessive length, non-JSON payload, timeout). => close."""


def _leer_exacto(sock, n):
    """Reads exactly n bytes from a socket. FrameError on timeout/EOF."""
    chunks = []
    restante = n
    while restante > 0:
        try:
            data = sock.recv(restante)
        except socket.timeout:
            raise FrameError("timeout")
        except OSError:
            raise FrameError("el peer cerró")
        if not data:
            raise FrameError("EOF (el peer cerró)")
        chunks.append(data)
        restante -= len(data)
    return b"".join(chunks)


def enviar_frame(sock, obj):
    """Serializes and writes a frame (uint32 BE + JSON) over a socket."""
    payload = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise FrameError("payload excede tope")
    header = struct.pack(">I", len(payload))
    try:
        sock.sendall(header + payload)
    except OSError:
        raise FrameError("el peer cerró (sendall)")


def recibir_frame(sock):
    """Reads a frame (uint32 BE + JSON) from a socket. FrameError on any invalidity."""
    try:
        header = _leer_exacto(sock, 4)
    except FrameError:
        raise
    (n,) = struct.unpack(">I", header)
    if n == 0 or n > MAX_FRAME_BYTES:
        raise FrameError(f"longitud de frame inválida: {n}")
    body = _leer_exacto(sock, n)
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        # RecursionError: deeply nested JSON (parser DoS). Treated as invalid frame.
        raise FrameError(f"payload no JSON válido: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# SERVER (monitor UID 103): request/response loop over an inherited socket.
# ─────────────────────────────────────────────────────────────────────────────
class ServidorMonitor:
    """Receives `pedir(op)` frames over a socket and returns results over the same socket.

    Imports the facade (monitor_fachada.MonitorFachada) that wraps the real modules (memoria_sqlite,
    gobernanza, nonces, objetivo, epoca). ONLY this process imports that.
    """

    def __init__(self, fachada, sock):
        self.fachada = fachada
        self.sock = sock

    def atender(self, max_iter=0):
        """Serves requests until EOF/close (max_iter=0 => indefinite). Returns number of ops."""
        n = 0
        while True:
            try:
                op = recibir_frame(self.sock)
            except FrameError:
                break  # invalid frame => session close
            resultado = self.fachada.pedir(op)
            try:
                enviar_frame(self.sock, resultado)
                n += 1
            except (OSError, FrameError):
                break
            if max_iter and n >= max_iter:
                break
        return n


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT (agent inside the sandbox): sends an op and receives the result.
# ─────────────────────────────────────────────────────────────────────────────
class ClienteMonitor:
    """Minimal client: does NOT import memoria_sqlite/gobernanza/nonces. Only framing + pedir."""

    def __init__(self, sock):
        self.sock = sock

    def pedir(self, op):
        enviar_frame(self.sock, op)
        return recibir_frame(self.sock)


if __name__ == "__main__":
    print("monitor_ipc.py cargado (framing estricto + servidor/cliente monitor, Fase 2 paso 2)")
