# Dashboard HCMA

Panel autocontenido (solo stdlib de Python: `http.server` + `sqlite3` + `json`, sin
frameworks ni dependencias nuevas) para operar la arquitectura HCMA.

## Qué muestra

- **Telemetría** (tokens in/out, costo en USD, latencia p50/p95/p99, llamadas, cache hits).
- **Cuellos de botella** (llamadas más lentas por modelo/tipo).
- **Flujo de datos** (tokens de entrada vs salida + timeline).
- **Memoria (hipocampo)**: hechos vigentes/stale/unverified, búsqueda, y **borrado
  gobernado** (marca `stale` + quita autoridad, queda en la hash chain con `op=delete`).
- **Log de auditoría** (hash chain `facts_log`).

## Uso

```bash
cd arquitectura/dashboard

# 1) opcional: poblar telemetría desde el llm_cache histórico (una vez)
HCMA_DASH_LLM_CACHE_DIR=../scripts/llm_cache python3 -c \
  "from telemetry import Telemetry; print('backfill:', Telemetry('telemetry.db').backfill_llm_cache('../scripts/llm_cache'))"

# 2) levantar el dashboard
HCMA_DASH_TELEMETRY_DB=telemetry.db \
HCMA_DASH_MEMORY_DB=/ruta/a/tu/memoria.db \
python3 app.py
# → http://localhost:8787
```

## Configuración

| Variable | Default | Qué es |
|----------|---------|--------|
| `HCMA_DASH_PORT` | 8787 | Puerto HTTP |
| `HCMA_DASH_TELEMETRY_DB` | `./telemetry.db` | Telemetría SQLite |
| `HCMA_DASH_MEMORY_DB` | (vacío) | Memoria HCMA SQLite (la de `ARQ_MEMORIA_DB`) |
| `HCMA_DASH_LLM_CACHE_DIR` | `../scripts/llm_cache` | Cache histórico para backfill |

## Instrumentación del orquestador

`prototipo.py` ya registra automáticamente en telemetría (`call_llm`, `call_vision`,
`busqueda_web`): modelo, tokens, latencia, costo y cache hit. Para que el orquestador y el
dashboard compartan la misma BD:

```bash
ARQ_MEMORIA_DB=/ruta/memoria.db \
ARQ_TELEMETRY_DB=arquitectura/dashboard/telemetry.db \
python3 arquitectura/scripts/prototipo.py
```

La telemetría es **opcional y a prueba de fallos**: si no existe la BD o falla el registro,
no interrumpe la corrida (se ignora silenciosamente).

## Endpoints

| Ruta | Método | Devuelve |
|------|--------|----------|
| `/` | GET | UI |
| `/api/overview` | GET | telemetría + memoria + timeline |
| `/api/models` | GET | tokens/costo por modelo y por tipo |
| `/api/latency` | GET | percentiles + llamadas más lentas |
| `/api/memory` | GET | hechos (filtro `?q=`) + stats + logs |
| `/api/memory/delete` | POST `{"key":"..."}` | borrado gobernado |
| `/api/backfill` | POST | poblar desde `llm_cache` |
| `/api/health` | GET | estado |

## Archivos

- `telemetry.py` — recorder SQLite + agregaciones (tokens/costo/latencia).
- `app.py` — servidor HTTP + endpoints.
- `index.html` — UI (Chart.js via CDN, degrada a tablas si no hay red).
- (se instrumenta) `../scripts/prototipo.py` — escribe telemetría.
- (se añadió) `../scripts/memoria_sqlite.py` — método `delete(key)`.
