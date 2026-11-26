#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""hcma_monitor_daemon.py — Phase 2 (step 3): monitor daemon entrypoint (UID hcma-monitor).

Fable (fable_fase2_revocacion_fable.md): the monitor as a daemon does justify a listening Unix
socket, created by systemd (socket-activation). The monitor:
  1. On STARTUP verifies the checkpoint (signed head) against the on-disk state. If it does not
     match or is missing, it DIES (fail-closed).
  2. Loads the facade with the real modules (memoria_sqlite -> monitor store, nonces_monitor ->
     monitor log, epoca -> epoch file).
  3. Accepts connections from the inherited socket (systemd LISTEN_FDS) and serves `pedir(op)`.

Any monitor exception = denial. If the monitor dies, the agent stops.
"""
import os
import sys
import socket

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

MONITOR_HOME = "/var/lib/hcma-monitor"
STORE_PATH = os.path.join(MONITOR_HOME, "store", "memoria.db")
NONCE_LOG = os.path.join(MONITOR_HOME, "nonces.log")
EPOCA_FILE = os.path.join(MONITOR_HOME, "epoca")

# Fail-closed checkpoint (anchor signed by the human). At module level so it can be patched in
# tests without touching /etc/hcma.
CKP_JSON = "/etc/hcma/checkpoint.json"
CKP_SIG = "/etc/hcma/checkpoint.sig"

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + ((" " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


def _estado_logico():
    """Reads the LOGICAL state of the store with mode=ro (the SAME .db + WAL path the monitor will
    use) and returns {n_facts, n_facts_log, head_facts_log, last_log_hash}.

    P1 (phantom WAL, Fable ruling): the digest of the .db file does NOT see the WAL. A foreign
    `-wal` with valid magic/salts/checksums OVERWRITES the data without altering the .db. Reading
    via SQL (with WAL active) does detect it, because it returns the real view the monitor will
    operate on.

    Does not use `immutable=1`: that would ignore the WAL and would not detect the phantom. If it
    cannot be read, it raises (the caller treats it as fail-closed)."""
    import sqlite3
    conn = sqlite3.connect(f"file:{STORE_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        n_facts = conn.execute("SELECT COUNT(*) AS c FROM facts").fetchone()["c"]
        n_log = conn.execute("SELECT COUNT(*) AS c FROM facts_log").fetchone()["c"]
        # REAL head of the chain = last row by seq (facts_log.version is -1 for nonce/consumir/
        # stale/log_op ops, so 'ORDER BY version DESC' is NOT the head).
        head = conn.execute("SELECT hash FROM facts_log ORDER BY seq DESC LIMIT 1").fetchone()
        last = conn.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
        return {
            "n_facts": n_facts,
            "n_facts_log": n_log,
            "head_facts_log": head["hash"] if head else None,
            "last_log_hash": last["v"] if last else None,
        }
    finally:
        conn.close()


def verificar_checkpoint():
    """Verifies that the store is a VALID (append-only) EXTENSION of the signed checkpoint.

    Fail-closed against TAMPERING, but tolerates legitimate store GROWTH:
      - chain integrity: head by seq == last_log_hash (detects in-place alteration);
      - monotonicity: n_facts / n_facts_log can NOT DEcrease (detects deletion);
      - prefix: the checkpoint's seq == n_facts_log row keeps its head_facts_log
        (detects rewriting history while keeping or increasing the count).

    The exact digest of the .db file is retired as fail-closed: in WAL mode it does not see the WAL
    and changes with every legitimate append. The logical checks (via SQL, with WAL active) are the
    ones that detect the phantom WAL and real tampering (P1).
    """
    import json
    if not os.path.exists(CKP_JSON):
        return {"ok": False, "detalle": "sin checkpoint (arranque sin ancla, solo dev)"}
    if not os.path.exists(CKP_SIG):
        return {"ok": False, "detalle": "checkpoint sin firma (ancla incompleta)"}
    ckp = json.load(open(CKP_JSON))
    try:
        s = _estado_logico()
    except Exception as e:
        return {"ok": False, "detalle": f"no se pudo leer el estado lógico del store: {e}"}

    # 1) facts_log chain integrity (head by seq == last_log_hash in hcma_meta)
    if s["head_facts_log"] != s["last_log_hash"]:
        return {"ok": False, "detalle": "cadena facts_log rota (head por seq != last_log_hash)"}

    # 2) monotonicity: append-only => never decreases; a decrease is deletion/tampering
    if ckp.get("n_facts") is not None and s["n_facts"] < ckp["n_facts"]:
        return {"ok": False, "detalle": f"n_facts decreció respecto al checkpoint "
                                        f"({s['n_facts']} < {ckp['n_facts']})"}
    if ckp.get("n_facts_log") is not None and s["n_facts_log"] < ckp["n_facts_log"]:
        return {"ok": False, "detalle": f"n_facts_log decreció respecto al checkpoint "
                                        f"({s['n_facts_log']} < {ckp['n_facts_log']})"}

    # 3) prefix: the row at seq == n_facts_log of the checkpoint keeps the checkpoint head
    if ckp.get("head_facts_log") is not None and ckp.get("n_facts_log") is not None:
        import sqlite3
        conn = sqlite3.connect(f"file:{STORE_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT hash FROM facts_log WHERE seq = ?",
                               (ckp["n_facts_log"],)).fetchone()
        finally:
            conn.close()
        if row is None or row["hash"] != ckp["head_facts_log"]:
            return {"ok": False, "detalle": "prefijo roto: la historia no es extensión del checkpoint "
                                            f"(head del checkpoint no está en seq {ckp['n_facts_log']})"}

    return {"ok": True, "detalle": f"checkpoint verificado (extensión válida, n_facts={s['n_facts']}, "
                                   f"head={s['head_facts_log'][:12] if s['head_facts_log'] else None}...)"}


def construir_fachada():
    """Loads the facade with the real modules pointing at the monitor store."""
    from memoria_sqlite import HCMAMemoriaSQLite
    from nonces_monitor import RegistroNonces
    import monitor_fachada as MF

    memoria = HCMAMemoriaSQLite(STORE_PATH)
    # nonce log: open fail-closed (if missing/corrupt, the monitor does not accept authorizations)
    reg = RegistroNonces(path=NONCE_LOG, epoca_actual=_epoca())
    reg.abrir(fail_closed=True)
    return MF.MonitorFachada(memoria, registro_nonces=reg, epoca_actual=_epoca())


def _epoca():
    import epoca as EP
    return EP.epoca_actual()


SOCKET_PATH = os.environ.get("HCMA_MONITOR_SOCK", "/run/hcma/monitor.sock")
# GOVERNANCE socket (Fable ruling): separate from the agent socket. Only accepts the human
# incident ops (GOBERNANZA_OPS allowlist in monitor_multiplex). The dashboard connects here.
GOVERNANZA_SOCK = os.environ.get("HCMA_GOVERNANZA_SOCK", "/run/hcma/gobernanza.sock")
# UDS of the Firecracker virtio-vsock (optional): if set, the AGENT socket is this path
# (Firecracker connects here when the guest uses vsock), instead of the runsc launcher socket.
VSOCK_UDS = os.environ.get("HCMA_VSOCK_UDS", "")


def _crear_socket_propio():
    """Creates the monitor's Unix socket at runtime (NOT systemd socket-activation).

    Fable ruling (fail-closed): the monitor must create its own socket, so that the listener lives
    and dies WITH the process (the kernel closes it when the monitor dies). Without
    socket-activation, there is no orphaned systemd listener that revives the service after a
    crash.

    It does a prior unlink() of an old socket (orphaned from a previous run), and sets mode 0600
    (only the monitor should connect; the launcher accesses it via its TCB path).
    """
    # clean up orphaned socket (if left over from a previous run that did not close)
    if os.path.exists(SOCKET_PATH):
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
    # ensure the socket directory (RuntimeDirectory is created by systemd; in case it runs outside)
    dir_sock = os.path.dirname(SOCKET_PATH)
    if dir_sock and not os.path.isdir(dir_sock):
        try:
            os.makedirs(dir_sock, mode=0o700, exist_ok=True)
        except OSError as e:
            raise RuntimeError(f"no se pudo crear el dir del socket {dir_sock}: {e}")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o600)
    s.listen(16)
    return s


def _crear_socket_gobernanza():
    """Creates the GOVERNANCE Unix socket (human actions: review/patch/rehabilitate).

    Unlike the agent socket (0600), this one can be accessible by the hcma-audit group (0660) so
    the DASHBOARD can forward human actions. If the group does not exist or the monitor cannot
    chown (no CAP_CHOWN), it degrades to 0600 (fail-closed: only the monitor connects).

    The op allowlist (GOBERNANZA_OPS) is applied by monitor_multiplex: even if a process connects,
    it can only run human ops, never agent ops."""
    if os.path.exists(GOVERNANZA_SOCK):
        try:
            os.unlink(GOVERNANZA_SOCK)
        except OSError:
            pass
    dir_sock = os.path.dirname(GOVERNANZA_SOCK)
    if dir_sock and not os.path.isdir(dir_sock):
        try:
            os.makedirs(dir_sock, mode=0o700, exist_ok=True)
        except OSError as e:
            raise RuntimeError(f"no se pudo crear el dir {dir_sock}: {e}")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(GOVERNANZA_SOCK)
    try:
        import grp
        gid = grp.getgrnam("hcma-audit").gr_gid
        os.chown(GOVERNANZA_SOCK, -1, gid)
        os.chmod(GOVERNANZA_SOCK, 0o660)
    except (KeyError, OSError, PermissionError):
        # group missing or without chown privileges => fail-closed (only the monitor connects)
        os.chmod(GOVERNANZA_SOCK, 0o600)
    # The socket directory (/run/hcma) is created 0700 by systemd's RuntimeDirectory. The
    # dashboard (hcma-audit group) must be able to TRAVERSE it to connect to the governance socket.
    # The agent socket (/run/hcma/monitor.sock) stays 0600, so traversing does not expose it.
    try:
        os.chmod(dir_sock, 0o711)
    except OSError:
        pass
    s.listen(16)
    return s


def _crear_socket_vsock():
    """Creates the virtio-vsock UDS (Firecracker connects here when the guest uses vsock).

    The monitor OWNS the listener (bind + listen). Firecracker runs as a NON-root user
    (hcma-firecracker); for it to connect, the socket is chowned to the hcma-vsock group and left
    0660 (only monitor + hcma-firecracker, which share that group). If the chown fails (group
    missing or without privileges), it degrades to 0600 (fail-closed: firecracker will not be able
    to connect and the monitor will see the agent without a channel). Returns the socket."""
    if os.path.exists(VSOCK_UDS):
        try:
            os.unlink(VSOCK_UDS)
        except OSError:
            pass
    dir_sock = os.path.dirname(VSOCK_UDS)
    if dir_sock and not os.path.isdir(dir_sock):
        os.makedirs(dir_sock, mode=0o700, exist_ok=True)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(VSOCK_UDS)
    try:
        import grp
        gid = grp.getgrnam("hcma-vsock").gr_gid
        os.chown(VSOCK_UDS, -1, gid)
        os.chmod(VSOCK_UDS, 0o660)
    except (KeyError, OSError, PermissionError):
        os.chmod(VSOCK_UDS, 0o600)  # fail-closed: only the monitor connects
    s.listen(16)
    return s


def _crear_socket_agente():
    """Creates the AGENT sockets. Returns a LIST of (socket, path).

    - ALWAYS creates the regular socket (/run/hcma/monitor.sock): used by the runsc launcher
      (gVisor) and the HOST watchers (observador_kernel for the gVisor log, and the auditd watcher
      for the out-of-band signal).
    - If HCMA_VSOCK_UDS is set, it ALSO creates the virtio-vsock UDS: Firecracker connects here
      when the guest uses vsock (CID 2). The monitor owns the listener in both (fail-closed)."""
    sockets = [(_crear_socket_propio(), SOCKET_PATH)]
    if VSOCK_UDS:
        sockets.append((_crear_socket_vsock(), VSOCK_UDS))
    return sockets


def main():
    print("hcma-monitor arrancando (UID %d)..." % os.getuid())

    # 1. verify checkpoint
    ckp = verificar_checkpoint()
    print("checkpoint:", ckp["detalle"])
    if ckp["ok"] is False and os.path.exists("/etc/hcma/checkpoint.json"):
        # there is a checkpoint but it did not verify => FAIL-CLOSED
        print("MONITOR MUERE: checkpoint no verificado", file=sys.stderr)
        sys.exit(1)

    # 2. load facade
    try:
        fachada = construir_fachada()
    except Exception as e:
        print(f"MONITOR MUERE: no se pudo cargar el estado: {e}", file=sys.stderr)
        sys.exit(1)
    print("fachada cargada (store + nonces + epoca)")

    # 3. create sockets (agent(s) + governance), fail-closed: listeners die with the process
    try:
        sockets = _crear_socket_agente()   # list of (socket, path)
        g = _crear_socket_gobernanza()
    except Exception as e:
        print(f"MONITOR MUERE: no se pudo crear los sockets: {e}", file=sys.stderr)
        sys.exit(1)
    rutas = ", ".join(p for _, p in sockets)
    print(f"escuchando en {rutas} (agente) y {GOVERNANZA_SOCK} (gobernanza), fail-closed")
    # MULTI-AGENT (Fable ruling fable_multiagente_fable.md): single-writer event loop.
    # Multiplexes N agent connections + the governance socket, executing ONE complete op at a time
    # on the SAME SQLite connection (never a second writer).
    import monitor_multiplex as MP
    try:
        MP.servir(fachada, [s for s, _ in sockets], gov_sock=g)
    finally:
        # on exit, close and unlink the sockets (do not leave orphaned listeners)
        for sock, path in sockets + [(g, GOVERNANZA_SOCK)]:
            try:
                sock.close()
                os.unlink(path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
