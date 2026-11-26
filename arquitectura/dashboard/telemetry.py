#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Telemetry of the HCMA architecture (SQLite, no external dependencies).

Records, for each LLM / vision / web search call:
  - model, type (llm|vision|web)
  - prompt_tokens / completion_tokens  (data flow in/out)
  - latency_ms  (response time → bottlenecks)
  - cost_usd    (money spent, computed with PRICING)
  - cached      (cache-hit vs real call)

Used by the dashboard (`app.py`) and written by the orchestrator (`prototipo.py`)
via `telemetry.record(...)`. If the DB does not exist, nothing breaks (it is optional).

Cost: computed with PRICING (USD per 1M tokens). For unlisted models a conservative
default is used; `cost_usd` can be overridden with the real `usage.cost`
(e.g. in web search, which also bills the retrieval).
"""
import json
import os
import sqlite3
import threading
import time


# USD per 1M tokens (prompt, completion). Source: OpenRouter public prices.
PRICING = {
    "openai/gpt-4o-mini":         (0.15, 0.60),
    "openai/gpt-4o":              (2.50, 10.00),
    "openai/gpt-6-luna-pro":      (2.00, 8.00),
    "anthropic/claude-sonnet-4-5": (3.00, 15.00),
    "meta-llama/llama-3.1-8b-instruct": (0.05, 0.08),
    "meta-llama/llama-3.1-70b-instruct": (0.52, 0.75),
    "google/gemini-2.0-flash-001": (0.10, 0.40),
}
DEFAULT_PRICE = (2.0, 8.0)  # conservative fallback


def price_for(model):
    return PRICING.get(model, DEFAULT_PRICE)


def cost_usd(model, prompt_tokens, completion_tokens):
    p, c = price_for(model)
    return prompt_tokens * p / 1e6 + completion_tokens * c / 1e6


_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    call_type         TEXT    NOT NULL DEFAULT 'llm',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms        REAL    NOT NULL DEFAULT 0,
    cost_usd          REAL    NOT NULL DEFAULT 0,
    cached            INTEGER NOT NULL DEFAULT 0,
    note              TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_calls_ts    ON calls(ts);
CREATE INDEX IF NOT EXISTS ix_calls_model ON calls(model);
CREATE INDEX IF NOT EXISTS ix_calls_type  ON calls(call_type);
"""


class Telemetry:
    def __init__(self, path):
        self.path = path
        # check_same_thread=False + lock: the dashboard is a ThreadingHTTPServer and serves
        # each request on a different thread; a single shared connection would fail.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def record(self, model, prompt_tokens=0, completion_tokens=0,
               latency_ms=0.0, cost=None, cached=False, call_type="llm",
               note="", ts=None):
        ts = ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if cost is None:
            cost = cost_usd(model, prompt_tokens, completion_tokens)
        with self._lock:
            self.conn.execute(
                "INSERT INTO calls(ts,model,call_type,prompt_tokens,completion_tokens,"
                "latency_ms,cost_usd,cached,note) VALUES(?,?,?,?,?,?,?,?,?)",
                (ts, model, call_type, int(prompt_tokens), int(completion_tokens),
                 float(latency_ms), float(cost), int(bool(cached)), note))
            self.conn.commit()
        return self

    # ── dashboard aggregations ────────────────────────────────────────────
    def overview(self):
        with self._lock:
            r = self.conn.execute(
                "SELECT COUNT(*) n, "
                "COALESCE(SUM(prompt_tokens),0) pt, "
                "COALESCE(SUM(completion_tokens),0) ct, "
                "COALESCE(SUM(cost_usd),0) usd, "
                "COALESCE(SUM(cached),0) cache_hits "
                "FROM calls").fetchone()
        return {"calls": r["n"], "prompt_tokens": r["pt"],
                "completion_tokens": r["ct"], "total_tokens": r["pt"] + r["ct"],
                "cost_usd": round(r["usd"], 4), "cache_hits": r["cache_hits"],
                "cache_hit_rate": round(r["cache_hits"] / r["n"], 3) if r["n"] else 0.0}

    def per_model(self):
        with self._lock:
            rows = self.conn.execute(
                "SELECT model, COUNT(*) n, SUM(prompt_tokens) pt, "
                "SUM(completion_tokens) ct, SUM(cost_usd) usd, SUM(cached) ch "
                "FROM calls GROUP BY model ORDER BY usd DESC").fetchall()
        return [{"model": r["model"], "calls": r["n"], "prompt_tokens": r["pt"],
                 "completion_tokens": r["ct"], "cost_usd": round(r["usd"], 4),
                 "cache_hits": r["ch"]} for r in rows]

    def per_type(self):
        with self._lock:
            rows = self.conn.execute(
                "SELECT call_type, COUNT(*) n, SUM(prompt_tokens) pt, "
                "SUM(completion_tokens) ct, SUM(cost_usd) usd "
                "FROM calls GROUP BY call_type").fetchall()
        return [{"type": r["call_type"], "calls": r["n"],
                 "prompt_tokens": r["pt"], "completion_tokens": r["ct"],
                 "cost_usd": round(r["usd"], 4)} for r in rows]

    def timeline(self, bucket="10 minute"):
        with self._lock:
            rows = self.conn.execute(
                "SELECT substr(ts,1,16) AS t, SUM(prompt_tokens+completion_tokens) tok, "
                "SUM(cost_usd) usd, AVG(latency_ms) lat, COUNT(*) n "
                "FROM calls GROUP BY t ORDER BY t").fetchall()
        return [{"t": r["t"], "tokens": r["tok"], "usd": round(r["usd"], 4),
                 "latency_ms": round(r["lat"], 1), "calls": r["n"]} for r in rows]

    def latency(self):
        with self._lock:
            r = self.conn.execute(
                "SELECT COUNT(*) n, AVG(latency_ms) avg, "
                "MAX(latency_ms) max, MIN(latency_ms) min FROM calls "
                "WHERE latency_ms > 0").fetchone()
            if not r["n"]:
                return {"n": 0, "avg_ms": 0, "p50": 0, "p95": 0, "p99": 0, "max_ms": 0}
            def _pct(p):
                off = max(0, int(r["n"] * p) - 1)
                row = self.conn.execute(
                    "SELECT latency_ms FROM calls WHERE latency_ms>0 "
                    "ORDER BY latency_ms LIMIT 1 OFFSET ?", (off,)).fetchone()
                return row["latency_ms"] if row else 0
            p50, p95, p99 = _pct(0.50), _pct(0.95), _pct(0.99)
            mx = r["max"]
        return {"n": r["n"], "avg_ms": round(r["avg"], 1),
                "p50": p50, "p95": p95, "p99": p99, "max_ms": round(mx, 1)}

    def slowest(self, n=15):
        with self._lock:
            rows = self.conn.execute(
                "SELECT ts, model, call_type, latency_ms, prompt_tokens, "
                "completion_tokens, cost_usd, cached FROM calls "
                "WHERE latency_ms > 0 ORDER BY latency_ms DESC LIMIT ?", (n,)).fetchall()
        return [{"ts": r["ts"], "model": r["model"], "type": r["call_type"],
                 "latency_ms": round(r["latency_ms"], 1),
                 "prompt_tokens": r["prompt_tokens"],
                 "completion_tokens": r["completion_tokens"],
                 "cost_usd": round(r["cost_usd"], 4), "cached": r["cached"]}
                for r in rows]

    def latency_per_model(self):
        with self._lock:
            rows = self.conn.execute(
                "SELECT model, COUNT(*) n, AVG(latency_ms) avg, MAX(latency_ms) max "
                "FROM calls WHERE latency_ms > 0 GROUP BY model ORDER BY avg DESC").fetchall()
        return [{"model": r["model"], "calls": r["n"], "avg_ms": round(r["avg"], 1),
                 "max_ms": round(r["max"], 1)} for r in rows]

    def backfill_llm_cache(self, cache_dir):
        """Populates telemetry from llm_cache/*.json (historical, without model or latency).

        The cache files store tokens per call but not the model (the name is a hash). They are
        recorded as model 'cache_historico' with latency=0 so the dashboard shows the accumulated
        historical spend, distinct from the instrumented one.
        """
        if not os.path.isdir(cache_dir):
            return 0
        n = 0
        for fn in os.listdir(cache_dir):
            if not fn.endswith(".json") or fn.startswith("web_") or fn.startswith("vis_"):
                continue
            try:
                d = json.load(open(os.path.join(cache_dir, fn), encoding="utf-8"))
            except Exception:
                continue
            pt = int(d.get("prompt_tokens", 0))
            ct = int(d.get("completion_tokens", 0))
            if pt or ct:
                self.record("cache_historico", pt, ct, latency_ms=0.0,
                            cost=cost_usd("openai/gpt-4o-mini", pt, ct),
                            cached=True, call_type="llm", note=fn[:12])
                n += 1
        return n

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
