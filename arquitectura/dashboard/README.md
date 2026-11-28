# HCMA Dashboard

Self-contained panel (Python stdlib only: `http.server` + `sqlite3` + `json`, no frameworks or new
dependencies) to operate the HCMA architecture.

## What it shows

- **Telemetry** (tokens in/out, cost in USD, latency p50/p95/p99, calls, cache hits).
- **Bottlenecks** (slowest calls per model/type).
- **Data flow** (input vs output tokens + timeline).
- **Memory (hippocampus)**: current/stale/unverified facts, search, and **governed deletion** (marks
  `stale` + removes authority; stays in the hash chain with `op=delete`).
- **Audit log** (`facts_log` hash chain).

## Usage

```bash
cd arquitectura/dashboard

# 1) optional: backfill telemetry from the historical llm_cache (once)
HCMA_DASH_LLM_CACHE_DIR=../scripts/llm_cache python3 -c \
  "from telemetry import Telemetry; print('backfill:', Telemetry('telemetry.db').backfill_llm_cache('../scripts/llm_cache'))"

# 2) start the dashboard
HCMA_DASH_TELEMETRY_DB=telemetry.db \
HCMA_DASH_MEMORY_DB=/path/to/your/memoria.db \
python3 app.py
# -> http://localhost:8787
```

## Configuration

| Variable | Default | What it is |
|----------|---------|--------|
| `HCMA_DASH_PORT` | 8787 | HTTP port |
| `HCMA_DASH_TELEMETRY_DB` | `./telemetry.db` | SQLite telemetry |
| `HCMA_DASH_MEMORY_DB` | (empty) | HCMA SQLite memory (the one from `ARQ_MEMORIA_DB`) |
| `HCMA_DASH_LLM_CACHE_DIR` | `../scripts/llm_cache` | Historical cache for backfill |

## Orchestrator instrumentation

`prototipo.py` already records telemetry automatically (`call_llm`, `call_vision`, `busqueda_web`):
model, tokens, latency, cost and cache hit. To make the orchestrator and the dashboard share the same
DB:

```bash
ARQ_MEMORIA_DB=/path/memoria.db \
ARQ_TELEMETRY_DB=arquitectura/dashboard/telemetry.db \
python3 arquitectura/scripts/prototipo.py
```

Telemetry is **optional and fail-safe**: if the DB does not exist or recording fails, it does not
interrupt the run (ignored silently).

## Endpoints

| Route | Method | Returns |
|------|--------|----------|
| `/` | GET | UI |
| `/api/overview` | GET | telemetry + memory + timeline |
| `/api/models` | GET | tokens/cost per model and type |
| `/api/latency` | GET | percentiles + slowest calls |
| `/api/memory` | GET | facts (`?q=` filter) + stats + logs |
| `/api/memory/delete` | POST `{"key":"..."}` | governed deletion |
| `/api/backfill` | POST | populate from `llm_cache` |
| `/api/health` | GET | status |

## Files

- `telemetry.py` - SQLite recorder + aggregations (tokens/cost/latency).
- `app.py` - HTTP server + endpoints.
- `index.html` - UI (Chart.js via CDN, degrades to tables if offline).
- (instrumented) `../scripts/prototipo.py` - writes telemetry.
- (added) `../scripts/memoria_sqlite.py` - `delete(key)` method.
