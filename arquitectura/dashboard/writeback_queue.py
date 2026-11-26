#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""writeback_queue.py — DURABLE write-back queue for chat facts.

The facts the user tells in the chat are ENQUEUED (not written fire-and-forget on a daemon thread
that dies with the process). A pool of workers drains the queue: extracts facts (LLM) + persists
them via the monitor, retrying with exponential backoff on failure (free model rate-limit, restart
mid-write, monitor down).

The queue is SQLite (survives dashboard restarts): an enqueued turn is processed even if the
dashboard restarts before persisting it. Stdlib only + llm_gateway (extract) + monitor_ipc (write).
"""
import json
import os
import sqlite3
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

DB_PATH = os.environ.get("HCMA_WRITEBACK_DB", os.path.join(HERE, "writeback.db"))
MONITOR_SOCK = os.environ.get("HCMA_MONITOR_SOCK", "/run/hcma/gobernanza.sock")

MAX_ATTEMPTS = 8          # after this, status='failed' (visible; not retried on its own)
BACKOFF_BASE = 2.0        # seconds, exponential: 2, 4, 8, 16, ...
WORKERS = int(os.environ.get("HCMA_WRITEBACK_WORKERS", "1"))
POLL_SECONDS = 1.0


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS writeback (
        id           TEXT PRIMARY KEY,
        messages     TEXT NOT NULL,
        attempts     INTEGER NOT NULL DEFAULT 0,
        status       TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed
        next_retry_at REAL NOT NULL DEFAULT 0,
        created_at   REAL NOT NULL,
        last_error   TEXT NOT NULL DEFAULT '',
        usuario      TEXT NOT NULL DEFAULT ''
    )""")
    try:
        c.execute("ALTER TABLE writeback ADD COLUMN usuario TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # the column already existed
    c.commit()
    return c


def enqueue(messages, usuario=""):
    """Enqueues a user turn (raw) to extract+persist. Durable.
    `usuario` is recorded as meta.usuario on each persisted fact (who loaded it)."""
    c = _conn()
    try:
        c.execute("INSERT INTO writeback(id, messages, attempts, status, next_retry_at, created_at, usuario) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (uuid.uuid4().hex, json.dumps(messages, ensure_ascii=False),
                   0, "pending", time.time(), time.time(), usuario or ""))
        c.commit()
    finally:
        c.close()


def _write_fact(key, value, unit="", usuario=""):
    """Writes ONE fact via the monitor (governance socket, single writer)."""
    import socket
    import monitor_ipc as IPC
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10.0)
    try:
        s.connect(MONITOR_SOCK)
    except OSError as e:
        return {"ok": False, "error": f"no conecta a {MONITOR_SOCK}: {e}"}
    try:
        cli = IPC.ClienteMonitor(sock=s)
        return cli.pedir({"tipo": "escribir_hecho", "key": key, "value": value,
                          "unit": unit, "usuario": usuario or ""})
    finally:
        s.close()


def _set_status(task_id, status, attempts=None, next_retry_at=None, last_error=None):
    c = _conn()
    try:
        if status == "done":
            c.execute("UPDATE writeback SET status='done', last_error='' WHERE id=?", (task_id,))
        elif status == "failed":
            c.execute("UPDATE writeback SET status='failed', attempts=?, last_error=? WHERE id=?",
                      (attempts, last_error, task_id))
        else:  # pending (retry)
            c.execute("UPDATE writeback SET attempts=?, next_retry_at=?, last_error=? WHERE id=?",
                      (attempts, next_retry_at, last_error, task_id))
        c.commit()
    finally:
        c.close()


def _retry(task_id, attempts, reason):
    attempts += 1
    if attempts >= MAX_ATTEMPTS:
        _set_status(task_id, "failed", attempts=attempts, last_error=reason)
        print(f"[writeback] ABANDONADO {task_id} tras {attempts} intentos: {reason}",
              file=sys.stderr, flush=True)
        return
    backoff = BACKOFF_BASE * (2 ** (attempts - 1))
    _set_status(task_id, "pending", attempts=attempts,
                next_retry_at=time.time() + backoff, last_error=reason)
    print(f"[writeback] reintento {attempts}/{MAX_ATTEMPTS} para {task_id} en {backoff:.0f}s: {reason}",
          file=sys.stderr, flush=True)


def _process(task_id, messages, attempts, usuario=""):
    """Extracts facts (LLM) and persists them via the monitor. Best effort with retry."""
    import llm_gateway as LG
    try:
        facts = LG.extract_facts(messages)
    except Exception as e:
        _retry(task_id, attempts, f"extract: {e}")
        return
    if not facts:
        _set_status(task_id, "done")  # no declared facts: task fulfilled
        return
    fallos = []
    for f in facts:
        try:
            r = _write_fact(f["key"], f["value"], f.get("unit", ""), usuario)
        except Exception as e:
            fallos.append(f"{f['key']}: {e}")
            continue
        if not r.get("ok"):
            fallos.append(f"{f['key']}: {r.get('detalle') or r.get('error')}")
    if fallos:
        _retry(task_id, attempts, "; ".join(fallos[:3]))
        return
    print(f"[writeback] persistidos {len(facts)} hechos (task {task_id[:8]})",
          file=sys.stderr, flush=True)
    _set_status(task_id, "done")


def _claim_one():
    """Takes ONE pending and due task, atomically (avoids double processing between workers)."""
    c = _conn()
    try:
        row = c.execute(
            "SELECT id, messages, attempts, usuario FROM writeback "
            "WHERE status='pending' AND next_retry_at <= ? ORDER BY created_at LIMIT 1",
            (time.time(),)).fetchone()
        if row is None:
            return None
        c.execute("UPDATE writeback SET next_retry_at=? WHERE id=?", (time.time() + 300, row["id"]))
        c.commit()
        return row
    finally:
        c.close()


def _worker_loop():
    while True:
        try:
            row = _claim_one()
            if row is not None:
                _process(row["id"], json.loads(row["messages"]), row["attempts"],
                         row["usuario"] or "")
        except Exception as e:
            print(f"[writeback] worker error: {e}", file=sys.stderr, flush=True)
        time.sleep(POLL_SECONDS)


def start_worker():
    """Starts the worker pool (daemon). Called ONCE at dashboard startup."""
    for i in range(max(1, WORKERS)):
        threading.Thread(target=_worker_loop, daemon=True, name=f"writeback-{i}").start()
    for i in range(max(1, WORKERS)):
        threading.Thread(target=_resp_worker_loop, daemon=True, name=f"writeback-resp-{i}").start()
    print(f"[writeback] pool de {max(1, WORKERS)} worker(s) + {max(1, WORKERS)} resp arrancado",
          file=sys.stderr, flush=True)


def _conn_resp():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS writeback_resp (
        id           TEXT PRIMARY KEY,
        candidate    TEXT NOT NULL,
        attempts     INTEGER NOT NULL DEFAULT 0,
        status       TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed
        next_retry_at REAL NOT NULL DEFAULT 0,
        created_at   REAL NOT NULL,
        last_error   TEXT NOT NULL DEFAULT ''
    )""")
    c.commit()
    return c


def enqueue_respuesta(candidate):
    """Enqueues an advisory response cache_candidate to persist via the monitor. Durable."""
    if not isinstance(candidate, dict) or not candidate.get("key") or not candidate.get("value"):
        return
    c = _conn_resp()
    try:
        c.execute("INSERT INTO writeback_resp(id, candidate, attempts, status, next_retry_at, created_at) "
                  "VALUES(?,?,?,?,?,?)",
                  (uuid.uuid4().hex, json.dumps(candidate, ensure_ascii=False),
                   0, "pending", time.time(), time.time()))
        c.commit()
    finally:
        c.close()


def _write_respuesta(candidate):
    """Persists ONE cached response via the monitor (op `escribir_respuesta`, single writer)."""
    import socket
    import monitor_ipc as IPC
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10.0)
    try:
        s.connect(MONITOR_SOCK)
    except OSError as e:
        return {"ok": False, "error": f"no conecta a {MONITOR_SOCK}: {e}"}
    try:
        cli = IPC.ClienteMonitor(sock=s)
        return cli.pedir({"tipo": "escribir_respuesta",
                          "key": candidate.get("key"),
                          "value": candidate.get("value"),
                          "usuario": candidate.get("usuario") or "",
                          "modelo": candidate.get("modelo") or "",
                          "ctx_fp": candidate.get("ctx_fp") or "",
                          "model_fp": candidate.get("model_fp") or "",
                          "ts": candidate.get("ts") or "",
                          "derived_from": candidate.get("derived_from") or {}})
    finally:
        s.close()


def _set_resp_status(task_id, status, attempts=None, next_retry_at=None, last_error=None):
    c = _conn_resp()
    try:
        if status == "done":
            c.execute("UPDATE writeback_resp SET status='done', last_error='' WHERE id=?", (task_id,))
        elif status == "failed":
            c.execute("UPDATE writeback_resp SET status='failed', attempts=?, last_error=? WHERE id=?",
                      (attempts, last_error, task_id))
        else:
            c.execute("UPDATE writeback_resp SET attempts=?, next_retry_at=?, last_error=? WHERE id=?",
                      (attempts, next_retry_at, last_error, task_id))
        c.commit()
    finally:
        c.close()


def _retry_resp(task_id, attempts, reason):
    attempts += 1
    if attempts >= MAX_ATTEMPTS:
        _set_resp_status(task_id, "failed", attempts=attempts, last_error=reason)
        print(f"[writeback-resp] ABANDONADO {task_id} tras {attempts} intentos: {reason}",
              file=sys.stderr, flush=True)
        return
    backoff = BACKOFF_BASE * (2 ** (attempts - 1))
    _set_resp_status(task_id, "pending", attempts=attempts,
                     next_retry_at=time.time() + backoff, last_error=reason)
    print(f"[writeback-resp] reintento {attempts}/{MAX_ATTEMPTS} para {task_id} en {backoff:.0f}s: {reason}",
          file=sys.stderr, flush=True)


def _process_resp(task_id, candidate, attempts):
    try:
        r = _write_respuesta(candidate)
    except Exception as e:
        _retry_resp(task_id, attempts, f"write: {e}")
        return
    if not r.get("ok"):
        _retry_resp(task_id, attempts, r.get("detalle") or r.get("error"))
        return
    print(f"[writeback-resp] respuesta cacheada {candidate.get('key', '')[:24]}... (task {task_id[:8]})",
          file=sys.stderr, flush=True)
    _set_resp_status(task_id, "done")


def _claim_resp_one():
    c = _conn_resp()
    try:
        row = c.execute(
            "SELECT id, candidate, attempts FROM writeback_resp "
            "WHERE status='pending' AND next_retry_at <= ? ORDER BY created_at LIMIT 1",
            (time.time(),)).fetchone()
        if row is None:
            return None
        c.execute("UPDATE writeback_resp SET next_retry_at=? WHERE id=?",
                  (time.time() + 300, row["id"]))
        c.commit()
        return row
    finally:
        c.close()


def _resp_worker_loop():
    while True:
        try:
            row = _claim_resp_one()
            if row is not None:
                _process_resp(row["id"], json.loads(row["candidate"]), row["attempts"])
        except Exception as e:
            print(f"[writeback-resp] worker error: {e}", file=sys.stderr, flush=True)
        time.sleep(POLL_SECONDS)


def stats():
    """Queue status (for the dashboard / audit): pending, failed, total."""
    c = _conn()
    try:
        pend = c.execute("SELECT COUNT(*) FROM writeback WHERE status='pending'").fetchone()[0]
        failed = c.execute("SELECT COUNT(*) FROM writeback WHERE status='failed'").fetchone()[0]
        done = c.execute("SELECT COUNT(*) FROM writeback WHERE status='done'").fetchone()[0]
        return {"pending": pend, "failed": failed, "done": done}
    finally:
        c.close()


if __name__ == "__main__":
    print(json.dumps(stats(), indent=2))
