#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""HCMAMemoria persistida en SQLite (índice) respetando la semántica HCMA exacta.

Prototipo: SQLite + FTS5 (índice en disco, durable). Luego migra a una BD real
(Postgres: recursive CTE, índice único parcial, jsonb y triggers son portables).

Misma interfaz que HCMAMemoria: write/current/candidates/state/all_verified/
pending_unverified + buscar() con FTS5.

Diseño (validado por Fable):
- `version` PK global monotónico (= rowid) → habilita linaje por versión exacta.
- Invariante de autoridad en el esquema: índice único parcial (máx 1 `authority=1`
  por key) + CHECK (authority=1 ⇒ verified=1 AND stale=0).
- `lineage` normalizado + invalidación transitiva con recursive CTE (cycle-safe),
  ANTES del insert (para no auto-invalidar el fact nuevo).
- FTS5 sincronizado por triggers (indexa solo hechos vigentes authority=1).
- Durabilidad: WAL + synchronous=FULL + BEGIN IMMEDIATE + busy_timeout + hash chain
  en facts_log (tamper-evidence).
"""
import json, os, re, sqlite3, hashlib, time, unicodedata

# Debe coincidir con prototipo.FUENTE_CONFIABLE (duplicado para evitar import circular).
# `test` NO es autoridad independiente: su confianza se hereda por linaje (calculadora).
FUENTE_CONFIABLE = {"calculadora", "usuario", "ingeniero", "tabla"}

# Versión del payload hasheado en facts_log. v1 = sin linaje/sesión; v2 = incluye df/auth/meta/sid.
PAYLOAD_VER = 2

# Quorum por defecto para el borrado multi-firma: cuántos VALIDADORES DISTINTOS (usuarios)
# deben aprobar una revocación antes de ejecutarla. El proponente nunca cuenta como validador
# de su propia propuesta (nadie se auto-aprueba). Configurable por `set_quorum_revocacion`.
QUORUM_REVOCACION_DEFAULT = 1

# Categorías válidas para justificar un borrado por consenso. El motivo es texto libre; la
# categoría es el eje deontológico (ética/moral/peligroso/error/PII/otro) que se cita al
# rechazar un write futuro con contenido igual o similar al revocado.
CATEGORIAS_REVOCACION = {"etica", "moral", "peligroso", "error", "pii", "otro"}

# Prefijo del REGISTRO DE CLAVES PÚBLICAS de los validadores (P0 A5). Cada `pubkey:<usuario>`
# guarda {username, pubkey_hex, registrada_por, ts}. La identidad de un validador ES su pubkey:
# un voto solo es válido si está FIRMADO con la privada correspondiente a la pubkey registrada.
PREFIJO_PUBKEY = "pubkey:"

# Prefijo de las ACCIONES GOBERNADAS (seguridad-degradantes) pendientes de quorum:
# `accion_gobernada:<hash_propuesta>` -> {tipo_accion, objetivo, propuesta_por, motivo, payload,
# quorum, votos, estado}. Al alcanzar el quorum, el MONITOR ejecuta el dominio (incidentes/
# herramientas) y la marca `ejecutada`. Mismo quorum que el borrado (quorum_revocacion).
PREFIJO_ACCION_GOB = "accion_gobernada:"

# Umbral de similitud (Jaccard sobre character 4-grams) para considerar que un write futuro es
# "similar" a un hecho revocado y debe ser rechazado. Determinista y agnóstico al idioma.
SIMILAR_UMBRAL = 0.75
_SHINGLE_N = 4
_SHINGLE_MAX = 2000


def _normalizar_valor(value):
    """Forma canónica de un valor para hashing/similitud: NFKC + whitespace colapsado."""
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    try:
        value = unicodedata.normalize("NFKC", value)
    except Exception:
        pass
    return " ".join(value.split())


def _value_hash(value):
    """SHA-256 del valor normalizado: identidad 'igual' (insensible a formato/acentos/homoglifos)."""
    return hashlib.sha256(_normalizar_valor(value).encode("utf-8")).hexdigest()


# Valores centinela que indican AUSENCIA de conocimiento, nunca un hecho (P1 Fable). Se rechazan
# en write (claim o verified) y en la promoción humana.
_VALORES_CENTINELA = {
    "desconocido", "desconocida", "no sé", "no se", "no lo sé", "no lo se",
    "n/a", "na", "none", "null", "sin datos", "sin dato", "no sabe",
}


def _es_centinela(value):
    """¿El value es un centinela de ausencia de conocimiento? (P1.4 Fable).

    Normaliza (lower, whitespace, puntuación perimetral) y considera centinela el vacío
    (solo '?', '.', etc.) o un valor de la lista de centinelas."""
    if not isinstance(value, str):
        return False
    v = " ".join(value.lower().split()).strip(".?¿!¡ ")
    return (not v) or (v in _VALORES_CENTINELA)


def _shingles(value, n=_SHINGLE_N, max_shingles=_SHINGLE_MAX):
    """Character n-grams (4) deduplicados y acotados, en minúsculas y SIN acentos (agnóstico a
    tildes y al idioma). El strip de acentos hace que 'fórmula' ≈ 'formula'."""
    s = _quitar_acentos(_normalizar_valor(value)).lower()
    if len(s) < n:
        return [s] if s else []
    sh = sorted({s[i:i + n] for i in range(len(s) - n + 1)})
    if len(sh) > max_shingles:
        step = len(sh) / max_shingles
        sh = [sh[int(i * step)] for i in range(max_shingles)]
    return sh


def _jaccard_similitud(a, b):
    """Jaccard entre dos listas de shingles (0..1). Determinista."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    u = len(sa | sb)
    return len(sa & sb) / u if u else 0.0


def _mensaje_denylist(m):
    """Mensaje de rechazo que cita el hash del hecho revocado y POR QUÉ se ignora el write."""
    cat = m.get("categoria") or "otro"
    h = m.get("hash") or "?"
    motivo = m.get("motivo") or "sin motivo"
    t = m.get("tipo")
    k = m.get("key")
    extra = f", similitud={m['similitud']:.2f}" if m.get("similitud") is not None else ""
    return (f"escritura bloqueada: conocimiento revocado por consenso [{cat}] "
            f"hash_revocado={h} · se ignora. motivo: {motivo} (match {t}, key_revocada={k}{extra})")


_STOP = set("""a an and at by for from in into is it of on or that the this to with two
de del la el los las un una unos unas y o en por para con al se que es son como entre
sobre su sus lo cuando donde""".split())


def _quitar_acentos(s):
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c))


def _norm_slug(s):
    """Slug de surface de alias que PRESERVA Unicode (multi-idioma, P1 Fable): NFKC + casefold +
    colapso de whitespace, luego no-palabra -> '_'. NO usa hash: la surface es clave de lookup
    LEGIBLE y auditable. Antes `[a-z0-9]` tiraba CJK/árabe y hacía colapsar 'PDF 合并' y
    'PDF 拆分' a 'pdf'."""
    import slug_canon
    c = slug_canon.canon(s)
    if not c:
        return ""
    return re.sub(r"[^\w]+", "_", c).strip("_")


def _norm_slug_legacy(s):
    """Forma LEGACY (pre-multi-idioma) de la surface: quita acentos y solo [a-z0-9]. Se usa SOLO
    como fallback de LECTURA para aliases viejos ya persistidos (compat, P1 Fable)."""
    t = _quitar_acentos(s).lower()
    return "_".join(re.findall(r"[a-z0-9]+", t))


def _enc(v):
    """JSON canónico; allow_nan=False para que NaN/Infinity revienten (no se pierden tipos)."""
    return json.dumps(v, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _dec(s):
    return json.loads(s)


class HCMAMemoriaSQLite:
    def __init__(self, path, seq_start=0, crear_esquema=True, read_only=False, ancla_path=None):
        self.path = path
        # Ancla externa (P0 A1): publica (seq, head_hash) fuera del store. Por env si no se pasa.
        self.ancla_path = ancla_path if ancla_path is not None else os.environ.get("HCMA_ANCLA_PATH", "")
        if read_only:
            # READ-ONLY (para el dashboard/auditoría): abre con mode=ro y NO ejecuta PRAGMAs
            # de escritura ni crea esquema. Cualquier intento de write lanza "readonly database".
            self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            self.conn.row_factory = sqlite3.Row
            return
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        if crear_esquema:
            self._crear_esquema()
        # marcador de corte de payload (idempotente): deja constancia en cadena de que desde acá
        # los writes llevan payload versionado (pv=2 con procedencia). verify() lo usa anti-downgrade.
        self.marcar_payload_ver()
        if seq_start:
            self.conn.execute(
                "INSERT INTO hcma_meta(k,v) VALUES('seq_floor',?) "
                "ON CONFLICT(k) DO UPDATE SET v=MAX(CAST(v AS INTEGER), excluded.v)",
                (str(seq_start),))
            self.conn.commit()

    def _crear_esquema(self):
        c = self.conn
        c.executescript("""
        CREATE TABLE IF NOT EXISTS facts (
            version      INTEGER PRIMARY KEY,
            key          TEXT NOT NULL,
            value        TEXT NOT NULL CHECK(json_valid(value)),
            unit         TEXT NOT NULL DEFAULT '',
            verified     INTEGER NOT NULL CHECK(verified IN (0,1)),
            authority    INTEGER NOT NULL CHECK(authority IN (0,1)),
            stale        INTEGER NOT NULL DEFAULT 0 CHECK(stale IN (0,1)),
            fuente       TEXT NOT NULL DEFAULT 'base',
            meta         TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(meta)),
            derived_from TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(derived_from)),
            CHECK (authority=0 OR (verified=1 AND stale=0))
        );
        CREATE INDEX IF NOT EXISTS ix_facts_key ON facts(key, version);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_auth ON facts(key) WHERE authority=1;
        CREATE TABLE IF NOT EXISTS lineage (
            child_version INTEGER NOT NULL REFERENCES facts(version),
            dep_key       TEXT NOT NULL,
            dep_version   INTEGER NOT NULL,
            PRIMARY KEY (child_version, dep_key)
        );
        CREATE INDEX IF NOT EXISTS ix_lineage_dep ON lineage(dep_version);
        CREATE TABLE IF NOT EXISTS hcma_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
        -- NONCES CONSUMIDOS: registro de un solo uso, ÚNICO y MONOTÓNICO, FUERA del estado
        -- versionable (no es un `fact`). Cierra el replay por re-inserción (P0 de Fable): aunque
        -- un blob firmado reaparezca con usada=0 bajo otra key/restore/instancia, su nonce ya
        -- está acá y se rechaza.
        CREATE TABLE IF NOT EXISTS nonces_consumidos (
            nonce      TEXT PRIMARY KEY,
            seq        INTEGER NOT NULL,
            ts         TEXT NOT NULL,
            tool_id    TEXT NOT NULL,
            hash_accion TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS facts_log (
            seq     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT NOT NULL,
            op      TEXT NOT NULL,
            key     TEXT NOT NULL,
            version INTEGER NOT NULL,
            payload TEXT NOT NULL,
            hash    TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
            key, texto, tokenize='unicode61 remove_diacritics 2'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts_claimed USING fts5(
            key, texto, tokenize='unicode61 remove_diacritics 2'
        );
        """)
        c.executescript("""
        CREATE TRIGGER IF NOT EXISTS facts_fts_ai AFTER INSERT ON facts WHEN NEW.authority=1 BEGIN
            INSERT INTO facts_fts(rowid, key, texto) VALUES (
                NEW.version, NEW.key,
                NEW.key || ' ' || COALESCE(json_extract(NEW.value,'$'),'') || ' ' ||
                NEW.unit || ' ' || NEW.fuente || ' ' || COALESCE(json_extract(NEW.meta,'$.desc'),'')
            );
        END;
        CREATE TRIGGER IF NOT EXISTS facts_fts_au AFTER UPDATE OF authority ON facts
        WHEN OLD.authority=1 AND NEW.authority=0 BEGIN
            DELETE FROM facts_fts WHERE rowid = OLD.version;
        END;
        -- Índice de claims (verified=0, authority=0, stale=0): recuperables como contexto
        -- atribuido, NUNCA como determinista.
        CREATE TRIGGER IF NOT EXISTS facts_fts_claimed_ai AFTER INSERT ON facts
        WHEN NEW.verified=0 AND NEW.authority=0 AND NEW.stale=0 BEGIN
            INSERT INTO facts_fts_claimed(rowid, key, texto) VALUES (
                NEW.version, NEW.key,
                NEW.key || ' ' || COALESCE(json_extract(NEW.value,'$'),'') || ' ' ||
                NEW.unit || ' ' || NEW.fuente || ' ' || COALESCE(json_extract(NEW.meta,'$.desc'),'') || ' ' ||
                COALESCE(json_extract(NEW.meta,'$.target'),'')
            );
        END;
        CREATE TRIGGER IF NOT EXISTS facts_fts_claimed_au AFTER UPDATE OF verified, authority, stale ON facts
        WHEN (OLD.verified=0 AND OLD.authority=0 AND OLD.stale=0)
         AND (NEW.verified=1 OR NEW.authority=1 OR NEW.stale=1) BEGIN
            DELETE FROM facts_fts_claimed WHERE rowid = OLD.version;
        END;
        CREATE TRIGGER IF NOT EXISTS facts_fts_claimed_ad AFTER DELETE ON facts BEGIN
            DELETE FROM facts_fts_claimed WHERE rowid = OLD.version;
        END;
        -- Defense in depth: un hecho del chat jamás puede ser verified ni authority.
        CREATE TRIGGER IF NOT EXISTS facts_chat_claimed_bi BEFORE INSERT ON facts
        WHEN NEW.fuente LIKE 'chat:%' AND (NEW.verified=1 OR NEW.authority=1) BEGIN
            SELECT RAISE(ABORT, 'chat fact must stay claimed (verified=0, authority=0)');
        END;
        CREATE TRIGGER IF NOT EXISTS facts_chat_claimed_bu BEFORE UPDATE OF verified, authority ON facts
        WHEN NEW.fuente LIKE 'chat:%' AND (NEW.verified=1 OR NEW.authority=1) BEGIN
            SELECT RAISE(ABORT, 'chat fact cannot become verified/authority');
        END;
        -- Defense in depth (dictamen Fable respuesta-cache): una respuesta advisory del LLM
        -- (`resp:*`) jamás puede ser verified ni authority: es un replay del advisory UNVERIFIED,
        -- no un hecho canónico. Si un humano la valida, se escribe un hecho NUEVO con fuente humana.
        CREATE TRIGGER IF NOT EXISTS facts_resp_claimed_bi BEFORE INSERT ON facts
        WHEN NEW.key LIKE 'resp:%' AND (NEW.verified=1 OR NEW.authority=1) BEGIN
            SELECT RAISE(ABORT, 'resp fact must stay claimed (verified=0, authority=0)');
        END;
        CREATE TRIGGER IF NOT EXISTS facts_resp_claimed_bu BEFORE UPDATE OF verified, authority ON facts
        WHEN NEW.key LIKE 'resp:%' AND (NEW.verified=1 OR NEW.authority=1) BEGIN
            SELECT RAISE(ABORT, 'resp fact cannot become verified/authority');
        END;
        """)
        # Backfill idempotente del índice de claims (para DBs ya existentes).
        c.execute("INSERT INTO facts_fts_claimed(rowid, key, texto) "
                  "SELECT version, key, key || ' ' || COALESCE(json_extract(value,'$'),'') || ' ' || unit || ' ' || fuente || ' ' || "
                  "COALESCE(json_extract(meta,'$.desc'),'') || ' ' || COALESCE(json_extract(meta,'$.target'),'') "
                  "FROM facts WHERE verified=0 AND authority=0 AND stale=0 "
                  "AND version NOT IN (SELECT rowid FROM facts_fts_claimed)")
        c.commit()

    # ── helpers ────────────────────────────────────────────────────────────────
    def _fact(self, row):
        return {"key": row["key"], "value": _dec(row["value"]), "unit": row["unit"],
                "version": row["version"], "verified": bool(row["verified"]),
                "authority": bool(row["authority"]), "stale": bool(row["stale"]),
                "fuente": row["fuente"], "meta": _dec(row["meta"]),
                "derived_from": _dec(row["derived_from"])}

    def _linaje_ok(self, derived_from):
        for k, v in (derived_from or {}).items():
            f = self.current(k)
            if f is None or not f["verified"] or f["stale"] or f["version"] != v:
                return False
        return True

    def _auto_dependencia(self, key, derived_from):
        """True si `key` es alcanzable transitivamente desde las deps (auto-dependencia/ciclo)."""
        visitados = set()
        pila = list((derived_from or {}).keys())
        while pila:
            k = pila.pop()
            if k == key:
                return True
            if k in visitados:
                continue
            visitados.add(k)
            f = self.current(k)
            if f is not None:
                pila.extend((f.get("derived_from") or {}).keys())
        return False

    def _append_log(self, op, key, version, payload):
        """Append-only a la hash chain: hash encadenado sobre el payload canónico. Devuelve el hash."""
        prev = self.conn.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
        prev_h = prev[0] if prev else ""
        canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha256((prev_h + "|" + canon).encode()).hexdigest()
        self.conn.execute(
            "INSERT INTO facts_log(ts,op,key,version,payload,hash) VALUES(?,?,?,?,?,?)",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), op, key, version, canon, h))
        self.conn.execute(
            "INSERT INTO hcma_meta(k,v) VALUES('last_log_hash',?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (h,))
        return h

    def _log(self, version, key, value, unit, verified, fuente, derived_from=None,
             authority=None, meta=None, session_id=None):
        """Registra un write con PAYLOAD COMPLETO (linaje, autoridad, meta, sesión) en la cadena.

        `df` (derived_from) queda HASHEADO: la procedencia es evidencia tamper-evident, no solo
        vista. `pv` (payload version) distingue eventos viejos sin cobertura de linaje/sesión."""
        authority = int(verified) if authority is None else int(authority)
        return self._append_log("write", key, version, {
            "pv": PAYLOAD_VER, "v": version, "k": key, "val": value, "u": unit,
            "ver": bool(verified), "auth": bool(authority), "f": fuente,
            "df": derived_from or {}, "meta": meta or {}, "sid": session_id,
        })

    def _invalidate_descendants(self, key, cause_key=None, cause_version=None):
        """Marca stale (transitivo) los descendientes de las versiones viejas de `key`, y REGISTRA
        el evento op=stale (con causa y afectados) en la hash chain. Recursive CTE cycle-safe."""
        c = self.conn
        rows = c.execute("""
            WITH RECURSIVE st(version) AS (
                SELECT version FROM facts WHERE key=? AND verified=1 AND stale=0
                UNION
                SELECT l.child_version FROM lineage l JOIN st ON l.dep_version = st.version
            )
            SELECT DISTINCT s.version FROM st s
            JOIN facts f ON f.version = s.version
            WHERE f.stale=0
              AND s.version NOT IN (SELECT version FROM facts WHERE key=? AND verified=1 AND stale=0)
        """, (key, key)).fetchall()
        affected = [r["version"] for r in rows]
        if affected:
            ph = ",".join("?" * len(affected))
            c.execute(f"UPDATE facts SET stale=1, authority=0 WHERE version IN ({ph})", affected)
            self._append_log("stale", key, -1, {
                "pv": PAYLOAD_VER, "op": "stale", "cause_key": cause_key,
                "cause_version": cause_version, "affected": affected,
            })
        return affected

    def _descendientes_transitivos(self, key):
        """Clausura transitiva de DESCENDIENTES de `key` vía `lineage`, SIN filtrar por
        verified/authority. Devuelve [{key, version, verified, authority, stale}] de TODA la
        cadena derivada (incluye claims y hechos de otros usuarios que consumieron el origen).

        Es la base del borrado por herencia aislado: solo toca lo que deriva de `key`."""
        c = self.conn
        pila = [r["version"] for r in c.execute(
            "SELECT version FROM facts WHERE key=?", (key,)).fetchall()]
        seen = set()
        while pila:
            v = pila.pop()
            if v in seen:
                continue
            seen.add(v)
            for r in c.execute("SELECT child_version FROM lineage WHERE dep_version=?", (v,)).fetchall():
                if r["child_version"] not in seen:
                    pila.append(r["child_version"])
        out = []
        for v in seen:
            r = c.execute("SELECT key, version, verified, authority, stale FROM facts WHERE version=?",
                          (v,)).fetchone()
            if r is not None:
                out.append({"key": r["key"], "version": r["version"],
                            "verified": bool(r["verified"]), "authority": bool(r["authority"]),
                            "stale": bool(r["stale"])})
        return out

    def arbol_documento(self, key):
        """ÁRBOL COMPLETO de un origen (p.ej. `doc:pdf:<hash>`): nodo raíz + descendientes
        transitivos con sus dependencias. Para visualizar qué tocaría un borrado legal."""
        desc = self._descendientes_transitivos(key)
        c = self.conn
        nodes = []
        edges = []
        vers = {d["version"] for d in desc}
        for d in desc:
            f = self.get_version(d["key"], d["version"])
            nodes.append(f)
            for dep_key, dep_ver in (f.get("derived_from") or {}).items():
                edges.append({"de": d["version"], "a": dep_ver, "dep_key": dep_key})
        return {"raiz": key, "nodes": nodes, "edges": edges,
                "n_descendientes": len(desc)}

    def consumir_flag(self, key, campo, pred=0):
        """CONSUMO ATÓMICO (CAS append-only): setea value.$campo = 1 SOLO si hoy vale `pred`.

        Devuelve True si EXACTAMENTE una fila paso de pred a 1 (consumo exitoso, una sola vez).
        Devuelve False si ya estaba consumido, ausente, o no vigente (evita REPLAY/doble gasto,
        P1 de Fable sobre el destino de uso de un solo uso). Es atómico: BEGIN IMMEDIATE +
        UPDATE condicional + registro en la hash chain, todo en una transacción. No usa read-then-
        write (no hay ventana entre verificar y consumir).

        `pred` se pasa como string por comodidad; internamente se compara contra el valor JSONEAL
        real (json_extract devuelve entero/real/texto según el tipo). Para booleanos JSON usa 0.
        """
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            # json_extract devuelve el TIPO real (0/1 enteros para false/true). Comparamos sin
            # comillas para que 0==0 (int) y no 0=='0' (string).
            cur = c.execute(
                "UPDATE facts SET value = json_set(value, '$.'||?, 1) "
                "WHERE key=? AND authority=1 "
                "AND json_extract(value, '$.'||?) = ?",
                (campo, key, campo, pred)).rowcount
            if cur != 1:
                c.execute("ROLLBACK")
                return False
            row = c.execute("SELECT version, value FROM facts WHERE key=? AND authority=1",
                            (key,)).fetchone()
            version = row[0]
            # registrar en la hash chain (append-only) para tamper-evidence del consumo
            prev = c.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
            prev_h = prev[0] if prev else ""
            canon = json.dumps({"op": "consumir", "k": key, "campo": campo, "v": version},
                               sort_keys=True, ensure_ascii=False)
            h = hashlib.sha256((prev_h + "|" + canon).encode()).hexdigest()
            c.execute("INSERT INTO facts_log(ts,op,key,version,payload,hash) VALUES(?,?,?,?,?,?)",
                      (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "consumir", key, version,
                       canon, h))
            c.execute("INSERT INTO hcma_meta(k,v) VALUES('last_log_hash',?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (h,))
            c.execute("COMMIT")
            return True
        except Exception:
            c.execute("ROLLBACK")
            raise

    # ── NONCE anti-replay (P0 Fable): fuera del estado versionable ──────────────
    def nonce_consumido(self, nonce):
        """¿Este nonce ya se consumió? Pura lectura. UNIQUE por PRIMARY KEY."""
        row = self.conn.execute(
            "SELECT 1 FROM nonces_consumidos WHERE nonce=?", (nonce,)).fetchone()
        return row is not None

    def consumir_nonce(self, nonce, tool_id, hash_accion):
        """Consume un nonce EXACTAMENTE una vez (CAS atómico por UNIQUE).

        INSERT OR IGNORE: si el nonce ya está, no se inserta y devolvemos False (replay).
        Si se inserta (rowcount==1), True. Es monotónico y fuera del estado versionable:
        sobrevive a restore/re-inserción del hecho bajo otra key. Registra en hash chain.
        """
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            cur = c.execute(
                "INSERT OR IGNORE INTO nonces_consumidos(nonce, seq, ts, tool_id, hash_accion) "
                "VALUES(?, COALESCE((SELECT MAX(seq)+1 FROM nonces_consumidos), 1), ?, ?, ?)",
                (nonce, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), tool_id, hash_accion)
            ).rowcount
            if cur != 1:
                c.execute("ROLLBACK")
                return False
            prev = c.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
            prev_h = prev[0] if prev else ""
            canon = json.dumps({"op": "nonce", "nonce": nonce, "tool_id": tool_id,
                                "hash_accion": hash_accion}, sort_keys=True, ensure_ascii=False)
            h = hashlib.sha256((prev_h + "|" + canon).encode()).hexdigest()
            c.execute("INSERT INTO facts_log(ts,op,key,version,payload,hash) VALUES(?,?,?,?,?,?)",
                      (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "nonce", "nonce:" + nonce,
                       -1, canon, h))
            c.execute("INSERT INTO hcma_meta(k,v) VALUES('last_log_hash',?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (h,))
            c.execute("COMMIT")
            return True
        except Exception:
            c.execute("ROLLBACK")
            raise

    def consumir_autorizacion_atomica(self, key, nonce, tool_id, hash_accion, session_id=None):
        """Consume FLAG `usada` Y NONCE en UNA sola transacción (atómico entre sí, P de Fable).

        Fable señaló que el flag y el nonce debían ser atómicos (no un read-then-write ni dos
        transacciones separadas con ventana). Como SON la misma conexión SQLite, un único
        `BEGIN IMMEDIATE` los hace indivisibles: o ambos quedan consumidos, o ninguno.

        DICTAMEN Fable (sesión/autorización): si `session_id` no es None, la sesión es un
        PREDICADO del MISMO CAS (no un chequeo previo): el UPDATE condicional del flag exige
        además que la sesión de la autorización coincida. Así no hay ventana entre "verificar
        sesión" y "consumir" (un intento desde la sesión equivocada NO consume nada).

        Devuelve True solo si TODOS pasan en la misma transacción (flag 0→1, nonce nuevo, y si
        aplica, sesión coincidente). Si algo falla, ROLLBACK total (no se quema el permiso).

        ABLACIÓN: si CAS_ATOMICO está apagado, se usa un consumo NO atómico.
        """
        import ablacion as AB
        if not AB.activo(AB.CAS_ATOMICO):
            # consumo NO atómico (read-then-write): ventana para doble gasto
            row = self.conn.execute(
                "SELECT json_extract(value,'$.usada') FROM facts WHERE key=? AND authority=1",
                (key,)).fetchone()
            if row is None or row[0] not in (0, None):
                return False
            self.conn.execute(
                "UPDATE facts SET value = json_set(value,'$.usada',1) "
                "WHERE key=? AND authority=1", (key,))
            self.conn.commit()
            return True
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            # 1) CAS sobre el flag usada (si ya estaba 1, no hay nada que consumir).
            #    Si session_id no es None, la sesión entra como PREDICADO del mismo CAS.
            if session_id is not None:
                cur_flag = c.execute(
                    "UPDATE facts SET value = json_set(value, '$.usada', 1) "
                    "WHERE key=? AND authority=1 AND json_extract(value, '$.usada') = 0 "
                    "AND json_extract(value, '$.session_id') = ?",
                    (key, session_id)).rowcount
            else:
                cur_flag = c.execute(
                    "UPDATE facts SET value = json_set(value, '$.usada', 1) "
                    "WHERE key=? AND authority=1 AND json_extract(value, '$.usada') = 0",
                    (key,)).rowcount
            if cur_flag != 1:
                c.execute("ROLLBACK")
                return False
            # 2) consumir el nonce (UNIQUE): si ya estaba, revierte TODO (incluye el flag)
            cur_nonce = c.execute(
                "INSERT OR IGNORE INTO nonces_consumidos(nonce, seq, ts, tool_id, hash_accion) "
                "VALUES(?, COALESCE((SELECT MAX(seq)+1 FROM nonces_consumidos), 1), ?, ?, ?)",
                (nonce, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), tool_id, hash_accion)
            ).rowcount
            if cur_nonce != 1:
                c.execute("ROLLBACK")
                return False
            # 3) hash chain (append-only) en la MISMA transacción
            prev = c.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
            prev_h = prev[0] if prev else ""
            canon = json.dumps({"op": "consumir_autorizacion", "k": key, "nonce": nonce,
                                "tool_id": tool_id, "hash_accion": hash_accion},
                               sort_keys=True, ensure_ascii=False)
            h = hashlib.sha256((prev_h + "|" + canon).encode()).hexdigest()
            c.execute("INSERT INTO facts_log(ts,op,key,version,payload,hash) VALUES(?,?,?,?,?,?)",
                      (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "consumir_autorizacion",
                       key, -1, canon, h))
            c.execute("INSERT INTO hcma_meta(k,v) VALUES('last_log_hash',?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (h,))
            c.execute("COMMIT")
            return True
        except Exception:
            c.execute("ROLLBACK")
            raise

    # ── interfaz HCMA (drop-in de HCMAMemoria) ────────────────────────────────
    def write(self, key, value, unit="", fuente="base", verified=False, meta=None,
              derived_from=None, session_id=None):
        derived_from = derived_from or {}
        meta = meta or {}
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            # Gate de centinelas (P1 Fable): la ausencia de conocimiento es ausencia de fila,
            # no un hecho. "desconocido"/"no sé"/"n/a" jamás se escribe como value (ni claim).
            if _es_centinela(value):
                raise ValueError("valor centinela rechazado (no es un hecho): %r" % value)
            # Denylist por consenso: una key revocada, o un VALOR igual/similar a uno revocado,
            # NO puede re-aprenderse. La negativa cita el hash del hecho revocado y la razón.
            m = self._denylist_match(key, value)
            if m is not None:
                raise ValueError(_mensaje_denylist(m))
            # ── Escritura gobernada (P0-A): hechos del chat son CLAIMED (0/0), nunca verified. ──
            # No pisan un verified: si el verified coincide, no se escribe; si difiere, se guarda
            # un claim aparte con meta.conflicts_with=<version verified>. Un claim nuevo de la
            # misma key marca stale=1 SOLO a claims previos (jamás al verified).
            if fuente.startswith("chat:"):
                cur = self.current(key)
                if cur is not None and cur["value"] == value and cur["unit"] == unit:
                    c.execute("ROLLBACK")
                    return cur["version"]
                c.execute(
                    "UPDATE facts SET stale=1 WHERE key=? AND verified=0 AND authority=0 AND stale=0",
                    (key,))
                meta = dict(meta or {})
                meta["tipo"] = "claimed"
                if cur is not None:
                    meta["conflicts_with"] = cur["version"]
                # P0-derecho-al-olvido: un claim PUEDE tener linaje (p.ej. derivado de un PDF
                # ingerido). Lo guardamos para que el borrado por herencia alcance el árbol completo.
                version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM facts").fetchone()[0]
                c.execute(
                    "INSERT INTO facts(version,key,value,unit,verified,authority,stale,fuente,meta,derived_from) "
                    "VALUES(?,?,?,?,0,0,0,?,?,?)",
                    (version, key, _enc(value), unit, fuente, _enc(meta), _enc(derived_from)))
                for dk, dv in derived_from.items():
                    c.execute("INSERT INTO lineage(child_version,dep_key,dep_version) VALUES(?,?,?)",
                              (version, dk, dv))
                self._log(version, key, value, unit, False, fuente,
                          derived_from=derived_from, authority=0, meta=meta, session_id=session_id)
                c.execute("COMMIT")
                self._anclar()
                return version
            cur = self.current(key)
            if (cur is not None and verified and cur["value"] == value
                    and cur["unit"] == unit and cur["fuente"] == fuente
                    and cur["derived_from"] == derived_from):
                c.execute("ROLLBACK")
                return cur["version"]
            if fuente == "calculadora" and not derived_from:
                verified = False
            elif self._auto_dependencia(key, derived_from):
                # auto-dependencia (directa o transitiva): ciclo de linaje. No auto-verificar.
                verified = False
            else:
                verified = bool(verified) and fuente in FUENTE_CONFIABLE and self._linaje_ok(derived_from)
            version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM facts").fetchone()[0]
            if verified:
                self._invalidate_descendants(key, cause_key=key, cause_version=version)
                c.execute("UPDATE facts SET authority=0 WHERE key=? AND authority=1", (key,))
            c.execute(
                "INSERT INTO facts(version,key,value,unit,verified,authority,stale,fuente,meta,derived_from) "
                "VALUES(?,?,?,?,?,?,0,?,?,?)",
                (version, key, _enc(value), unit, int(verified), int(verified), fuente,
                 _enc(meta), _enc(derived_from)))
            for dk, dv in derived_from.items():
                c.execute("INSERT INTO lineage(child_version,dep_key,dep_version) VALUES(?,?,?)",
                          (version, dk, dv))
            self._log(version, key, value, unit, verified, fuente,
                      derived_from=derived_from, authority=int(verified), meta=meta,
                      session_id=session_id)
            c.execute("COMMIT")
            self._anclar()
            return version
        except Exception:
            c.execute("ROLLBACK")
            raise

    def promover_claim(self, key, claimed_version, autor, evidence=None, session_id=None):
        """Promueve un CLAIM (verified=0, fuente 'chat:*') a VERIFIED por acción humana (A2).

        Crea una VERSIÓN NUEVA (fuente 'promocion:humano', derived_from=[claim]). La fila del
        claim NUNCA se vuelve verified: queda como evidencia en lineage, y el hecho nuevo es el
        canónico. Guarda meta.evidence (cita literal / msg_id) para que se vea QUÉ se aprobó.
        No hay auto-promoción ni quorum acá (el quorum es gobernanza de nivel superior)."""
        claim = self.get_version(key, claimed_version)
        if claim is None:
            raise ValueError("claim inexistente")
        if claim["verified"] or claim["authority"] or not (claim.get("fuente") or "").startswith("chat:"):
            raise ValueError("solo se puede promover un claim (verified=0, fuente chat:*)")
        if _es_centinela(claim["value"]):
            raise ValueError("no se puede promover un claim con valor centinela (no es un hecho): %r"
                             % claim["value"])
        m = self._denylist_match(key, claim["value"])
        if m is not None:
            raise ValueError(_mensaje_denylist(m))
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            cur = self.current(key)
            if cur is not None and cur["value"] == claim["value"] and cur["unit"] == claim["unit"]:
                c.execute("ROLLBACK")
                return cur["version"]
            derived_from = {key: claimed_version}
            meta = dict(claim.get("meta") or {})
            meta.update({"tipo": "promocion", "promovido_por": autor,
                         "promovido_desde": claimed_version, "evidence": evidence})
            version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM facts").fetchone()[0]
            self._invalidate_descendants(key, cause_key=key, cause_version=version)
            c.execute("UPDATE facts SET authority=0 WHERE key=? AND authority=1", (key,))
            c.execute(
                "INSERT INTO facts(version,key,value,unit,verified,authority,stale,fuente,meta,derived_from) "
                "VALUES(?,?,?,?,1,1,0,?,?,?)",
                (version, key, _enc(claim["value"]), claim["unit"], "promocion:humano",
                 _enc(meta), _enc(derived_from)))
            c.execute("INSERT INTO lineage(child_version,dep_key,dep_version) VALUES(?,?,?)",
                      (version, key, claimed_version))
            self._log(version, key, claim["value"], claim["unit"], True, "promocion:humano",
                      derived_from=derived_from, authority=1, meta=meta, session_id=session_id)
            c.execute("COMMIT")
            self._anclar()
            return version
        except Exception:
            c.execute("ROLLBACK")
            raise

    def retirar(self, key, motivo, autor="migracion:alias_v2", session_id=None):
        """Retira un hecho vigente (migración, P0.3 Fable): marca stale=1 y authority=0, y
        registra el evento en la hash chain (op=retirar). NO es un borrado legal (no denylist):
        es una migración que retira un namespace legacy superseded por una arista alias.
        Devuelve el nº de filas retiradas."""
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            n = c.execute(
                "UPDATE facts SET stale=1, authority=0 WHERE key=? AND stale=0", (key,)).rowcount
            prev = c.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
            prev_h = prev[0] if prev else ""
            canon = json.dumps({"op": "retirar", "key": key, "motivo": motivo,
                                "autor": autor, "session_id": session_id},
                               sort_keys=True, ensure_ascii=False)
            h = hashlib.sha256((prev_h + "|" + canon).encode()).hexdigest()
            c.execute("INSERT INTO facts_log(ts,op,key,version,payload,hash) VALUES(?,?,?,?,?,?)",
                      (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "retirar", key, -1, canon, h))
            c.execute("INSERT INTO hcma_meta(k,v) VALUES('last_log_hash',?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (h,))
            c.execute("COMMIT")
            self._anclar()
            return n
        except Exception:
            c.execute("ROLLBACK")
            raise

    def migrar_chat_verified_a_claimed(self, session_id="migracion_a2"):
        """Migración A2: degrada hechos que el chat VIEJO escribió como verified a claimed.

        Candidato: fuente='usuario' AND verified=1 AND authority=1 AND stale=0 AND meta.usuario
        no vacío (la marca que dejaba el write-back del chat). Para cada uno crea una fila CLAIMED
        (fuente 'chat:usuario', 0/0) con meta.migrado_de=<version> y marca la fila verified vieja
        stale=1/authority=0. Todo queda en la hash chain como evento append (op=degradar_chat).

        Idempotente: solo degrada la fila que sigue siendo vigente (authority=1). Devuelve el nº
        de hechos degradados. NO reescribe la cadena: las filas viejas quedan como evidencia."""
        c = self.conn
        rows = c.execute(
            "SELECT version, key, value, unit, meta FROM facts "
            "WHERE fuente='usuario' AND verified=1 AND authority=1 AND stale=0 "
            "AND json_extract(meta,'$.usuario') IS NOT NULL "
            "AND json_extract(meta,'$.usuario') != ''"
        ).fetchall()
        n = 0
        for r in rows:
            key = r["key"]
            value = _dec(r["value"])
            unit = r["unit"]
            meta_old = _dec(r["meta"]) or {}
            c.execute("BEGIN IMMEDIATE")
            try:
                cur = self.current(key)
                # Solo migrar si esta fila sigue siendo la vigente (evita doble migración).
                if cur is None or cur["version"] != r["version"]:
                    c.execute("ROLLBACK")
                    continue
                version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM facts").fetchone()[0]
                meta_new = {"usuario": meta_old.get("usuario") or "",
                            "tipo": "claimed", "migrado_de": r["version"]}
                c.execute(
                    "INSERT INTO facts(version,key,value,unit,verified,authority,stale,fuente,meta,derived_from) "
                    "VALUES(?,?,?,?,0,0,0,'chat:usuario',?,?)",
                    (version, key, _enc(value), unit, _enc(meta_new), _enc({})))
                c.execute("UPDATE facts SET stale=1, authority=0 WHERE version=?", (r["version"],))
                self._append_log("degradar_chat", key, r["version"], {
                    "pv": PAYLOAD_VER, "op": "degradar_chat",
                    "old_version": r["version"], "new_version": version,
                    "motivo": "migracion A2: el chat no auto-verifica",
                })
                c.execute("COMMIT")
                self._anclar()
                n += 1
            except Exception:
                c.execute("ROLLBACK")
                raise
        return n

    def write_cas(self, key, value, expected_version, unit="", fuente="base", verified=False,
                  meta=None, derived_from=None, session_id=None):
        """CAS por VERSIÓN: escribe una versión nueva SOLO si la última versión de `key` es
        `expected_version`. Devuelve la nueva versión, o None si perdió la carrera (otro escritor
        avanzó la versión).

        Convierte la atomicidad por "single-writer" (CONVENCIÓN) en MECANISMO (dictamen Fable,
        pool de tareas): el write de la transición falla si la versión cambió entre read y write.
        Barato, y sobrevive a que alguien rompa el invariante de single-writer."""
        derived_from = derived_from or {}
        meta = meta or {}
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            cur_max_key = c.execute("SELECT COALESCE(MAX(version),0) FROM facts WHERE key=?",
                                    (key,)).fetchone()[0]
            if cur_max_key != expected_version:
                c.execute("ROLLBACK")
                return None
            # el NUEVO número de versión es GLOBAL (facts.version es UNIQUE global), igual que write()
            version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM facts").fetchone()[0]
            if verified:
                self._invalidate_descendants(key, cause_key=key, cause_version=version)
                c.execute("UPDATE facts SET authority=0 WHERE key=? AND authority=1", (key,))
            c.execute(
                "INSERT INTO facts(version,key,value,unit,verified,authority,stale,fuente,meta,derived_from) "
                "VALUES(?,?,?,?,?,?,0,?,?,?)",
                (version, key, _enc(value), unit, int(verified), int(verified), fuente,
                 _enc(meta), _enc(derived_from)))
            for dk, dv in derived_from.items():
                c.execute("INSERT INTO lineage(child_version,dep_key,dep_version) VALUES(?,?,?)",
                          (version, dk, dv))
            self._log(version, key, value, unit, verified, fuente,
                      derived_from=derived_from, authority=int(verified), meta=meta,
                      session_id=session_id)
            c.execute("COMMIT")
            return version
        except Exception:
            c.execute("ROLLBACK")
            raise

    def log_op(self, op, key="", payload=None, session_id=None):
        """Log de una OP (incluidos intentos RECHAZADOS) al `facts_log` con cadena de hash.

        Cierra el hueco de auditabilidad de Fable (paso 2): un `tomar` perdido o un `registrar`
        con ciclo no escriben un fact, así que la competencia/denegación era invisible. Acá cada
        op —exitosa o no— queda en el journal append-only con `seq` global (AUTOINCREMENT) y hash
        encadenado. Devuelve el `seq`."""
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            prev = c.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
            prev_h = prev[0] if prev else ""
            canon = json.dumps({"op": op, "session_id": session_id, "key": key,
                                "payload": payload or {}}, sort_keys=True, ensure_ascii=False)
            h = hashlib.sha256((prev_h + "|" + canon).encode()).hexdigest()
            c.execute("INSERT INTO facts_log(ts,op,key,version,payload,hash) VALUES(?,?,?,?,?,?)",
                      (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), op, key, -1, canon, h))
            c.execute("INSERT INTO hcma_meta(k,v) VALUES('last_log_hash',?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (h,))
            c.execute("COMMIT")
            return c.execute("SELECT last_insert_rowid()").fetchone()[0]
        except Exception:
            c.execute("ROLLBACK")
            raise

    def current(self, key):
        row = self.conn.execute(
            "SELECT * FROM facts WHERE key=? AND authority=1 LIMIT 1", (key,)).fetchone()
        return self._fact(row) if row else None

    def _alias_row(self, surface):
        """Lookup de una arista alias por surface exacta: (target, tier) o None."""
        f = self.current("alias:" + surface)
        if f is not None:
            return (f["value"], "verified")
        row = self.conn.execute(
            "SELECT value FROM facts WHERE key=? AND verified=0 AND authority=0 AND stale=0 "
            "ORDER BY version DESC LIMIT 1", ("alias:" + surface,)).fetchone()
        if row is not None:
            return (_dec(row["value"]), "claimed")
        return None

    def alias_target(self, surface):
        """Devuelve (target, tier) para una surface de alias tipada, o None (P0-B).

        La arista alias es un hecho `alias:<surface>` cuyo value es la ENTIDAD canónica. tier
        ∈ {'verified','claimed'} según el estado de la arista. Un salto de alias, no recursivo.
        P1 multi-idioma: intenta la surface nueva (Unicode) y luego la LEGACY (sin acentos/ASCII)
        para no romper aliases persistidos antes del fix."""
        r = self._alias_row(surface)
        if r is not None:
            return r
        legacy = _norm_slug_legacy(surface)
        if legacy and legacy != surface:
            return self._alias_row(legacy)
        return None

    def crear_alias(self, surface, target, fuente="extractor", verified=False,
                    session_id=None):
        """Escribe una arista alias tipada `alias:<surface>` -> target (P0-B).

        Se usa en la INGESTA (el extractor propone surface/target) y en el backfill. La surface
        se normaliza (minúsculas, sin acentos, slug); el target es una entidad existente. Un
        target que es otra alias se aplana (no se escribe alias->alias).

        La DETECCIÓN de la relación de alias NO se hace acá (sería dependiente de idioma): la
        propone el LLM en escritura (agnóstico de idioma) y queda claimed hasta confirmación."""
        surface = _norm_slug(surface)
        if not surface or not target:
            return None
        existing = self.alias_target(target)
        while existing is not None:
            target = existing[0]
            existing = self.alias_target(target)
        return self.write("alias:" + surface, target, fuente=fuente,
                         verified=verified, meta={"tipo": "alias", "target": target},
                         session_id=session_id)

    def crear_prop(self, surface, propiedad, fuente="extractor", verified=False,
                   session_id=None):
        """Escribe una arista de PROPIEDAD `prop:<surface>` -> propiedad (dictamen Fable: salto 2
        de alias AGNÓSTICO de idioma). Multi-destino: una surface puede apuntar a varias
        propiedades (p.ej. 'pelo' -> color y pelaje). Se usa en la INGESTA y el backfill de
        identidad. El idioma vive en los DATOS, no en el código (no hay diccionario de sinónimos)."""
        surface = _norm_slug(surface)
        if not surface or not propiedad:
            return None
        return self.write("prop:" + surface, propiedad, fuente=fuente, verified=verified,
                         meta={"tipo": "prop"}, session_id=session_id)

    def prop_targets(self, surface):
        """Devuelve la lista de propiedades (destinos) de `prop:<surface>` (no-stale, multi-destino)."""
        out = []
        for r in self.conn.execute(
                "SELECT value FROM facts WHERE key=? AND stale=0", ("prop:" + surface,)).fetchall():
            try:
                out.append(_dec(r["value"]))
            except Exception:
                pass
        return out

    def props_entidad(self, entidad):
        """Devuelve el set de propiedades (sufijos tras '<entidad>:') de los hechos vigentes."""
        out = set()
        pref = entidad + ":"
        for r in self.conn.execute(
                "SELECT key FROM facts WHERE key LIKE ? AND authority=1 AND stale=0",
                (pref + "%",)).fetchall():
            out.add(r["key"][len(pref):])
        return out

    def facts_entidad(self, entidad):
        """Devuelve TODOS los hechos vigentes (verified, authority=1, stale=0) de una ENTIDAD
        `<entidad>:*`, ordenados por key. Cada hecho lleva `prop` (sufijo tras '<entidad>:') para
        poder ordenar por hit literal sin hardcodear idioma.

        Se usa en el salto 2 de alias (bloque de mundo cerrado, dictamen
        fable_propiedad_llm_fable.md): la PROPIEDAD es semántica y la selecciona el LLM, pero la
        evidencia inyectada es determinista (conjunto + orden)."""
        out = []
        pref = entidad + ":"
        for r in self.conn.execute(
                "SELECT * FROM facts WHERE key LIKE ? AND authority=1 AND stale=0 "
                "ORDER BY key", (pref + "%",)).fetchall():
            f = self._fact(r)
            f["prop"] = f["key"][len(pref):]
            out.append(f)
        return out

    def get_version(self, key, version):
        """LECTURA PURA de un hecho por VERSIÓN EXACTA, sin filtro de verified/authority.

        NO cambia estado: es un SELECT. Devuelve el fact completo (con sus flags), o None
        si no existe esa versión de esa key.

        Por qué existe (y por qué NO hay `latest`): el runtime necesita leer la
        implementación de un skill (`skill:<id>:impl`), que es SIEMPRE unverified (I1).
        `current()` devuelve None para unverified, y `candidates()` solo devuelve versiones
        POSTERIORES a una versión vigente. La única lectura cruda aceptable es por VERSIÓN
        PINNEADA desde un hecho verified (el certificado `ok:<impl_hash>`), nunca "la
        más reciente" (que sería el vector "leer y creer").

        Es API INTERNA del runtime/dashboard. NO debe exponerse como tool del LLM: si el
        modelo pudiera leer candidatos unverified, rompería el guardrail de que
        `state()` no le expone lo no confiable.
        """
        row = self.conn.execute(
            "SELECT * FROM facts WHERE key=? AND version=?", (key, version)).fetchone()
        return self._fact(row) if row else None

    def keys(self, prefix):
        """LECTURA PURA: lista de keys VIGENTES (authority=1) con un prefijo dado.

        NO cambia estado (SELECT). Es la primitiva que le faltaba al runtime/dashboard para
        DESCUBRIR qué hechos existen (p.ej. qué certificados `skill:<id>:ok:<impl_hash>` hay,
        o qué impls `skill:<id>:impl` se propusieron), sin asumir una key:

          - `current()` exige saber la key exacta de antemano.
          - `get_version()` exige saber también la versión.

        Devuelve SOLO keys con autoridad vigente (no expone candidatos/unverified), así que no
        rompe el guardrail de que el runtime no entregue lo no-confiable como canónico. Para
        auditar impls candidatas (unverified) existe `get_version` con versión pinneada desde
        un hecho verificado.

        API interna del runtime/dashboard. NO exponer como tool del LLM.
        """
        rows = self.conn.execute(
            "SELECT key FROM facts WHERE key LIKE ? ESCAPE '\\' AND authority=1 "
            "GROUP BY key ORDER BY key",
            (prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)
        ).fetchall()
        return [r["key"] for r in rows]

    def keys_crudas(self, prefix):
        """LECTURA PURA (auditoría interna): lista de TODAS las keys con un prefijo, SIN filtrar
        authority/verified. Para el GATE interno de necesidades/gobernanza, que necesita LISTAR
        propuestas unverified (ej. `necesidad:*` abiertas) para derivar 'bloqueado'.

        NO exponer como tool del LLM: expondría candidatos unverified (puro leak de no-confiable).
        """
        rows = self.conn.execute(
            "SELECT DISTINCT key FROM facts WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
            (prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)
        ).fetchall()
        return [r["key"] for r in rows]

    def current_raw(self, key):
        """LECTURA PURA (auditoría interna): la ÚLTIMA versión de `key` SIN filtrar authority/
        verified/stale. Para registros contables internos (evidencia, nonces) que son unverified
        pero gobernados por su propio namespace y firmados por el objetivo.

        NO exponer como tool del LLM (expondría candidatos unverified).
        """
        row = self.conn.execute(
            "SELECT * FROM facts WHERE key=? ORDER BY version DESC LIMIT 1", (key,)).fetchone()
        return self._fact(row) if row else None

    def candidates(self, key):
        cur = self.current(key)
        cv = cur["version"] if cur else -1
        rows = self.conn.execute(
            "SELECT * FROM facts WHERE key=? AND version>? AND (verified=0 OR stale=1) "
            "ORDER BY version", (key, cv)).fetchall()
        return [self._fact(r) for r in rows]

    def state(self):
        rows = self.conn.execute("SELECT * FROM facts WHERE authority=1").fetchall()
        return {r["key"]: {"value": _dec(r["value"]), "unit": r["unit"], "fuente": r["fuente"]}
                for r in rows}

    def all_verified(self, keys):
        keys = list(keys)
        if not keys:
            return False
        q = ",".join("?" for _ in keys)
        n = self.conn.execute(
            f"SELECT COUNT(*) FROM facts WHERE authority=1 AND key IN ({q})", keys).fetchone()[0]
        return n == len(set(keys))

    def pending_unverified(self):
        rows = self.conn.execute("SELECT * FROM facts").fetchall()
        by_key = {}
        for r in rows:
            by_key.setdefault(r["key"], []).append(self._fact(r))
        out = {}
        for k, v in by_key.items():
            cur = self.current(k)
            if cur is None:
                pend = [f for f in v if not f["verified"] or f["stale"]]
            else:
                pend = [f for f in v if f["version"] > cur["version"]
                        and (not f["verified"] or f["stale"])]
            if pend:
                out[k] = pend
        return out

    def delete(self, key, autor=None, motivo=None, categoria="otro"):
        """Borrado gobernado AUDITADO: saca `key` del estado canónico y del índice, y deja un
        registro INMUTABLE (append-only en la hash chain) con `autor` (quién) y `motivo` (por qué).

        Además escribe un tombstone `revocado:<key>` (denylist canónica, fuente humana) con el HASH
        del contenido borrado (`value_hash`) y sus shingles, para que un write FUTURO con contenido
        igual o similar sea rechazado citando ese hash y la razón (`categoria` + `motivo`).
        El historial NO se borra físicamente: queda forense y tamper-evident."""
        if categoria not in CATEGORIAS_REVOCACION:
            categoria = "otro"
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            # capturar el valor vigente ANTES de invalidarlo: deja hash + shingles en el tombstone.
            # Si la key es un origen CLAIMED (p.ej. un PDF ingerido), se usa su última versión.
            cur = self.current(key)
            value_for_hash = cur["value"] if cur is not None else None
            if value_for_hash is None:
                r = c.execute("SELECT value FROM facts WHERE key=? AND stale=0 ORDER BY version DESC LIMIT 1",
                              (key,)).fetchone()
                value_for_hash = _dec(r["value"]) if r is not None else None
            value_hash = _value_hash(value_for_hash) if value_for_hash is not None else None
            shingles = _shingles(value_for_hash) if value_for_hash is not None else []
            # BORRADO POR HERENCIA: invalidar TODA la clausura transitiva (verified Y claimed),
            # incluyendo hechos de OTROS usuarios derivados del origen. Solo toca ese árbol.
            desc = self._descendientes_transitivos(key)
            affected = [d["version"] for d in desc if not d["stale"]]
            if affected:
                ph = ",".join("?" * len(affected))
                c.execute(f"UPDATE facts SET stale=1, authority=0 WHERE version IN ({ph})", affected)
                self._append_log("stale", key, -1, {"pv": PAYLOAD_VER, "op": "stale",
                                                     "cause_key": key, "affected": affected})
            c.execute("UPDATE facts SET stale=1, authority=0 WHERE key=? AND stale=0", (key,))
            prev = c.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
            prev_h = prev[0] if prev else ""
            canon = json.dumps({"op": "delete", "key": key, "autor": autor, "motivo": motivo,
                                "categoria": categoria},
                               sort_keys=True, ensure_ascii=False)
            h = hashlib.sha256((prev_h + "|" + canon).encode()).hexdigest()
            c.execute("INSERT INTO facts_log(ts,op,key,version,payload,hash) VALUES(?,?,?,?,?,?)",
                      (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "delete", key, -1, canon, h))
            c.execute("INSERT INTO hcma_meta(k,v) VALUES('last_log_hash',?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (h,))
            # tombstone denylist por CADA key del árbol (raíz + descendientes): bloquea
            # re-escritura por key y por valor igual/similar, citando el hash revocado y el origen.
            afectados = {}
            for d in desc:
                if d["key"] in afectados:
                    continue
                f = self.get_version(d["key"], d["version"])
                val = f["value"] if f is not None else None
                if val is None:
                    continue
                afectados[d["key"]] = {"value_hash": _value_hash(val), "shingles": _shingles(val)}
            if key not in afectados and value_for_hash is not None:
                afectados[key] = {"value_hash": value_hash, "shingles": shingles}
            for k, a in sorted(afectados.items()):
                valor_rev = {"key": k, "autor": autor, "motivo": motivo, "categoria": categoria,
                             "value_hash": a["value_hash"], "shingles": a["shingles"],
                             "origen": key,
                             "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM facts").fetchone()[0]
                c.execute("UPDATE facts SET authority=0 WHERE key=? AND authority=1", ("revocado:" + k,))
                c.execute("INSERT INTO facts(version,key,value,unit,verified,authority,stale,fuente,meta,derived_from) "
                          "VALUES(?,?,?,?,1,1,0,'usuario',?,?)",
                          (version, "revocado:" + k, _enc(valor_rev), "",
                           _enc({"tipo": "revocacion"}), _enc({})))
                self._log(version, "revocado:" + k, valor_rev, "", True, "usuario",
                          meta={"tipo": "revocacion"}, session_id=autor)
            c.execute("COMMIT")
            self._anclar()
            return True
        except Exception:
            c.execute("ROLLBACK")
            raise

    def restaurar(self, key, autor=None, motivo=None):
        """HUMANO: quita la denylist (`revocado:<key>` → stale), permitiendo re-aprender la key.
        La restauración también queda auditada (op=restore, inmutable)."""
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("UPDATE facts SET stale=1, authority=0 WHERE key=? AND authority=1",
                      ("revocado:" + key,))
            prev = c.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
            prev_h = prev[0] if prev else ""
            canon = json.dumps({"op": "restore", "key": key, "autor": autor, "motivo": motivo},
                               sort_keys=True, ensure_ascii=False)
            h = hashlib.sha256((prev_h + "|" + canon).encode()).hexdigest()
            c.execute("INSERT INTO facts_log(ts,op,key,version,payload,hash) VALUES(?,?,?,?,?,?)",
                      (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "restore", key, -1, canon, h))
            c.execute("INSERT INTO hcma_meta(k,v) VALUES('last_log_hash',?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (h,))
            c.execute("COMMIT")
            return True
        except Exception:
            c.execute("ROLLBACK")
            raise

    def esta_revocada(self, key):
        """DERIVADO: ¿la key está en la denylist de revocación?"""
        return self.current("revocado:" + key) is not None

    def _revocaciones_activas(self):
        """Lista los tombstones `revocado:*` VIGENTES (authority=1), con su value decodificado."""
        rows = self.conn.execute(
            "SELECT key, value FROM facts WHERE key LIKE 'revocado:%' AND authority=1").fetchall()
        return [{"key": r["key"], "value": _dec(r["value"])} for r in rows]

    def _denylist_match(self, key, value):
        """Devuelve None si el write es aceptable, o un dict de rechazo si choca contra la
        denylist de revocación (por key exacta, por valor EXACTO o por valor SIMILAR).

        El dict de rechazo cita el hash del hecho revocado, el motivo, la categoría y el tipo
        de match (key|valor|similar), para que la negativa sea accionable y auditable."""
        # 1) key exacta (comportamiento histórico)
        if not key.startswith("revocado:"):
            t = self.current("revocado:" + key)
            if t is not None:
                v = t.get("value") or {}
                return {"tipo": "key", "hash": v.get("value_hash"), "motivo": v.get("motivo"),
                        "categoria": v.get("categoria"), "key": key}
        # 2) valor EXACTO o SIMILAR contra TODOS los tombstones vigentes (independiente de la key)
        vh = _value_hash(value)
        sh_new = _shingles(value)
        for r in self._revocaciones_activas():
            rv = r.get("value") or {}
            if rv.get("value_hash") and rv["value_hash"] == vh:
                return {"tipo": "valor", "hash": rv["value_hash"], "motivo": rv.get("motivo"),
                        "categoria": rv.get("categoria"),
                        "key": r["key"][len("revocado:"):]}
            sim = _jaccard_similitud(sh_new, rv.get("shingles") or [])
            if sim >= SIMILAR_UMBRAL:
                return {"tipo": "similar", "hash": rv.get("value_hash"), "motivo": rv.get("motivo"),
                        "categoria": rv.get("categoria"),
                        "key": r["key"][len("revocado:"):], "similitud": sim}
        return None

    # ── borrado multi-firma (quorum de validadores) ───────────────────────────
    # Un borrado NO se ejecuta con un solo humano: primero se PROPONE (motivo + quorum),
    # luego N validadores DISTINTOS votan. El proponente no puede votar su propia propuesta.
    # Al alcanzar el quorum, se ejecuta `delete()` (tombstone + denylist) en el mismo flujo.
    # Cada paso (propuesta, voto, ejecución, set de quorum) queda en la hash chain append-only.

    def quorum_revocacion(self):
        """Cuántos validadores distintos se exigen para ejecutar un borrado (>= 1)."""
        row = self.conn.execute("SELECT v FROM hcma_meta WHERE k='quorum_revocacion'").fetchone()
        if row is None:
            return QUORUM_REVOCACION_DEFAULT
        try:
            return max(1, int(row[0]))
        except (ValueError, TypeError):
            return QUORUM_REVOCACION_DEFAULT

    def set_quorum_revocacion(self, n, autor=None):
        """BOOTSTRAP: setea el quorum inicial (>=1) SOLO si aún no fue seteado. Para cambiarlo
        después, usar proponer_cambio_quorum/votar_cambio_quorum (exige el quorum vigente)."""
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError("quorum_revocacion debe ser entero >= 1")
        # check ANTES de abrir la transacción (evita doble ROLLBACK si ya fue seteado)
        if self.conn.execute("SELECT 1 FROM hcma_meta WHERE k='quorum_revocacion'").fetchone():
            raise ValueError("el quorum ya fue seteado; usá proponer_cambio_quorum para cambiarlo")
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            self._append_log("set_quorum_revocacion", "", -1, {
                "pv": PAYLOAD_VER, "op": "set_quorum_revocacion",
                "autor": autor, "quorum": n,
            })
            c.execute("INSERT INTO hcma_meta(k,v) VALUES('quorum_revocacion',?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(n),))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        return n

    # ── registro de claves públicas de validadores (P0 A5, multi-firma real) ──
    # La identidad de un validador ES su pubkey Ed25519. Un voto solo es válido si está FIRMADO
    # con la privada correspondiente. El registro es un hecho gobernado (bootstrap por admin,
    # igual que set_quorum_revocacion) y queda en la hash chain (tamper-evident + ancla externa).
    # Rotación de clave = nueva versión del mismo hecho (auditada); el quorum de rotación es un
    # follow-up (acá el admin es autoridad de bootstrap de identidad).
    def registrar_pubkey(self, usuario, pubkey_hex, autor="admin"):
        """Registra la clave pública Ed25519 de un validador (bootstrap por admin).

        Valida el formato de la pubkey y la persiste como hecho gobernado `pubkey:<usuario>`.
        Devuelve la versión del hecho. Re-registrar rota la clave (nueva versión, auditada)."""
        import firmas_multifirma as FM
        if not isinstance(usuario, str) or not usuario.strip():
            raise ValueError("usuario requerido para registrar pubkey")
        if not FM.pubkey_valida(pubkey_hex):
            raise ValueError("pubkey_hex inválida (debe ser una Ed25519 pública de 64 hex chars)")
        usuario = usuario.strip()
        existente = self.current(PREFIJO_PUBKEY + usuario)
        if existente is not None and (existente.get("value") or {}).get("pubkey_hex") == pubkey_hex:
            return existente["version"]  # idempotente: misma clave ya registrada
        valor = {
            "username": usuario,
            "pubkey_hex": pubkey_hex,
            "registrada_por": autor or "admin",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        version = self.write(PREFIJO_PUBKEY + usuario, valor, unit="", fuente="usuario",
                            verified=True, meta={"tipo": "pubkey_validator"})
        self.log_op("pubkey.registrada", key=PREFIJO_PUBKEY + usuario, session_id=autor or "admin",
                    payload={"usuario": usuario, "version": version})
        return version

    def pubkey_de(self, usuario):
        """Clave pública Ed25519 registrada para `usuario`, o None si no tiene."""
        f = self.current(PREFIJO_PUBKEY + (usuario or ""))
        if f is None:
            return None
        v = f.get("value") or {}
        return v.get("pubkey_hex")

    def pubkeys_registradas(self):
        """Lista las claves públicas de validadores registradas (para el dashboard)."""
        out = []
        for k in self.keys(PREFIJO_PUBKEY):
            f = self.current(k)
            if f is None:
                continue
            v = f.get("value") or {}
            out.append({"usuario": v.get("username"), "pubkey_hex": v.get("pubkey_hex"),
                        "registrada_por": v.get("registrada_por"), "ts": v.get("ts")})
        out.sort(key=lambda x: x.get("usuario", ""))
        return out

    # ── verificación de votos firmados (P0 A5) ─────────────────────────────────
    def _hash_propuesta_revocacion(self, valor):
        import firmas_multifirma as FM
        return FM.hash_propuesta_revocacion(
            valor.get("key"), valor.get("motivo"), valor.get("categoria"),
            int(valor.get("quorum") or 1), valor.get("propuesta_por"), valor.get("ts_propuesta"))

    def _hash_propuesta_cambio_quorum(self, valor):
        import firmas_multifirma as FM
        return FM.hash_propuesta_cambio_quorum(
            int(valor.get("n") or 1), valor.get("propuesta_por"),
            int(valor.get("quorum_actual") or 1), valor.get("ts_propuesta"))

    def _verificar_voto(self, dominio, autor, hash_propuesta, firma_hex, ts_exp):
        """FAIL-CLOSED: un voto es válido solo si el validador tiene pubkey registrada Y la firma
        es válida (atada al hash de la propuesta) Y no expiró. Lanza ValueError si no."""
        import firmas_multifirma as FM
        pub = self.pubkey_de(autor)
        if pub is None:
            raise ValueError(
                f"validador '{autor}' sin clave pública registrada (multi-firma real requiere Ed25519)")
        if not isinstance(firma_hex, str) or not firma_hex:
            raise ValueError("voto sin firma Ed25519")
        try:
            ts_exp = int(ts_exp)
        except (TypeError, ValueError):
            raise ValueError("voto sin expiración válida (ts_exp)")
        if ts_exp <= int(time.time()):
            raise ValueError("voto expirado")
        if not FM.verificar_voto(dominio, hash_propuesta, autor, ts_exp, firma_hex, pub):
            raise ValueError("firma inválida para esta propuesta/validador")
        return True

    def proponer_cambio_quorum(self, n, autor=None):
        """Propone cambiar el quorum vigente a `n` (>=1). Exige que el quorum ya esté seteado
        (bootstrap hecho). Crea `quorum_cambio_pendiente`; para ejecutarse requiere el quorum VIGENTE."""
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError("quorum debe ser entero >= 1")
        if self.conn.execute("SELECT 1 FROM hcma_meta WHERE k='quorum_revocacion'").fetchone() is None:
            raise ValueError("el quorum no fue seteado aún; usá set_quorum_revocacion (bootstrap)")
        if self.current_raw("quorum_cambio_pendiente") is not None:
            raise ValueError("ya hay un cambio de quorum pendiente")
        autor = autor or "humano"
        quorum_actual = self.quorum_revocacion()
        ts_propuesta = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        valor = {"tipo": "cambio_quorum", "n": n, "propuesta_por": autor,
                 "quorum_actual": quorum_actual, "votos": {}, "estado": "pendiente",
                 "ts_propuesta": ts_propuesta}
        valor["hash_propuesta"] = self._hash_propuesta_cambio_quorum(valor)
        version = self.write("quorum_cambio_pendiente", valor, unit="", fuente="usuario",
                             verified=False, meta={"tipo": "cambio_quorum"})
        self.log_op("quorum_cambio.propuesta", key="quorum", session_id=autor,
                    payload={"n": n, "quorum_actual": quorum_actual, "version": version})
        return {"n": n, "quorum_actual": quorum_actual, "propuesta_por": autor,
                "n_votos": 0, "estado": "pendiente"}

    def votar_cambio_quorum(self, autor=None, firma_hex=None, ts_exp=None):
        """Un validador DISTINTO del proponente aprueba el cambio (voto FIRMADO Ed25519, P0 A5).
        Al alcanzar el quorum VIGENTE, aplica el nuevo quorum. El proponente no puede votar su
        propia propuesta."""
        autor = autor or "humano"
        pend = self.current_raw("quorum_cambio_pendiente")
        if pend is None:
            raise ValueError("no hay cambio de quorum pendiente")
        valor = dict(pend.get("value") or {})
        propuesta_por = valor.get("propuesta_por")
        quorum_actual = int(valor.get("quorum_actual") or 1)
        n = int(valor.get("n"))
        if propuesta_por == autor:
            raise ValueError("el proponente no puede votar su propia propuesta")
        hash_propuesta = valor.get("hash_propuesta") or self._hash_propuesta_cambio_quorum(valor)
        self._verificar_voto("voto_cambio_quorum", autor, hash_propuesta, firma_hex, ts_exp)
        votos = dict(valor.get("votos") or {})
        if autor in votos:
            raise ValueError("este usuario ya votó")
        votos[autor] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "firma": firma_hex, "ts_exp": int(ts_exp),
                        "hash_propuesta": hash_propuesta}
        nuevo = dict(valor)
        nuevo["votos"] = votos
        alcanza = len(votos) >= quorum_actual
        if alcanza:
            nuevo["estado"] = "aprobada"
            nuevo["ts_aprobada"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.write("quorum_cambio_pendiente", nuevo, unit="", fuente="usuario",
                   verified=False, meta={"tipo": "cambio_quorum"})
        self.log_op("quorum_cambio.voto", key="quorum", session_id=autor,
                    payload={"n": n, "n_votos": len(votos), "quorum_actual": quorum_actual,
                             "alcanza_quorum": alcanza})
        if alcanza:
            c = self.conn
            c.execute("BEGIN IMMEDIATE")
            try:
                self._append_log("set_quorum_revocacion", "", -1, {
                    "pv": PAYLOAD_VER, "op": "set_quorum_revocacion",
                    "autor": autor, "quorum": n,
                })
                c.execute("INSERT INTO hcma_meta(k,v) VALUES('quorum_revocacion',?) "
                          "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(n),))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            self.log_op("quorum_cambio.ejecutado", key="quorum", session_id=autor,
                        payload={"n": n, "votos": votos})
            return {"ejecutado": True, "n": n, "n_votos": len(votos),
                    "quorum_actual": quorum_actual}
        return {"ejecutado": False, "n": n, "n_votos": len(votos),
                "quorum_actual": quorum_actual}

    def _revocacion_pendiente(self, key):
        return self.current_raw("revocacion_pendiente:" + key)

    def proponer_revocacion(self, key, autor=None, motivo=None, quorum=None, categoria="otro"):
        """PASO 1: propone borrar `key` con un motivo y una categoría (etica/moral/peligroso/...).
        Crea un hecho interno `revocacion_pendiente:<key>` (unverified, bookkeeping)."""
        if not isinstance(key, str) or not key:
            raise ValueError("key requerida")
        if not isinstance(motivo, str) or not motivo.strip():
            raise ValueError("motivo requerido (por qué se borra)")
        if categoria not in CATEGORIAS_REVOCACION:
            categoria = "otro"
        autor = autor or "humano"
        q = self.quorum_revocacion() if quorum is None else quorum
        if isinstance(q, bool) or not isinstance(q, int) or q < 1:
            raise ValueError("quorum debe ser entero >= 1")
        if self.esta_revocada(key):
            raise ValueError("la key ya está revocada")
        if self._revocacion_pendiente(key) is not None:
            raise ValueError("ya existe una revocación pendiente para esta key")
        ts_propuesta = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        valor = {
            "tipo": "revocacion_pendiente",
            "key": key,
            "propuesta_por": autor,
            "motivo": motivo.strip(),
            "categoria": categoria,
            "quorum": q,
            "votos": {},
            "estado": "pendiente",
            "ts_propuesta": ts_propuesta,
        }
        valor["hash_propuesta"] = self._hash_propuesta_revocacion(valor)
        version = self.write("revocacion_pendiente:" + key, valor, unit="", fuente="usuario",
                             verified=False, meta={"tipo": "revocacion_pendiente"})
        self.log_op("revocacion.propuesta", key=key, session_id=autor,
                    payload={"quorum": q, "version": version})
        return {"key": key, "quorum": q, "propuesta_por": autor,
                "n_votos": 0, "estado": "pendiente", "version": version}

    def votar_revocacion(self, key, autor=None, firma_hex=None, ts_exp=None):
        """PASO 2: un validador DISTINTO del proponente aprueba el borrado con voto FIRMADO
        Ed25519 (P0 A5). Al alcanzar el quorum ejecuta `delete()` (tombstone + denylist)
        automáticamente. Idempotente/recoverable: si el quorum ya se alcanzó pero no se
        ejecutó, re-ejecuta el borrado."""
        if not isinstance(key, str) or not key:
            raise ValueError("key requerida")
        autor = autor or "humano"
        pend = self._revocacion_pendiente(key)
        if pend is None:
            raise ValueError("no hay revocación pendiente para esta key")
        valor = dict(pend.get("value") or {})
        propuesta_por = valor.get("propuesta_por")
        quorum = int(valor.get("quorum") or 1)
        motivo = valor.get("motivo", "")
        categoria = valor.get("categoria", "otro")
        if categoria not in CATEGORIAS_REVOCACION:
            categoria = "otro"
        if propuesta_por == autor:
            raise ValueError("el proponente no puede votar su propia propuesta")
        hash_propuesta = valor.get("hash_propuesta") or self._hash_propuesta_revocacion(valor)
        self._verificar_voto("voto_revocacion", autor, hash_propuesta, firma_hex, ts_exp)
        if self.esta_revocada(key):
            return {"key": key, "ejecutada": True, "ya_revocada": True,
                    "n_votos": len(valor.get("votos") or {}), "quorum": quorum,
                    "estado": "ejecutada"}
        votos = dict(valor.get("votos") or {})
        if autor in votos:
            # ya votó: si ya se alcanzó el quorum pero falta ejecutar, re-intenta el borrado
            if len(votos) >= quorum:
                self.delete(key, autor=propuesta_por, motivo=motivo, categoria=categoria)
                self.log_op("revocacion.ejecutada", key=key, session_id=autor,
                            payload={"quorum": quorum, "votos": votos, "reintento": True})
                return {"key": key, "ejecutada": True, "n_votos": len(votos),
                        "quorum": quorum, "estado": "ejecutada"}
            raise ValueError("este usuario ya votó")
        votos[autor] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "firma": firma_hex, "ts_exp": int(ts_exp),
                        "hash_propuesta": hash_propuesta}
        nuevo = dict(valor)
        nuevo["votos"] = votos
        alcanza = len(votos) >= quorum
        if alcanza:
            nuevo["estado"] = "aprobada"
            nuevo["ts_aprobada"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        version = self.write("revocacion_pendiente:" + key, nuevo, unit="", fuente="usuario",
                             verified=False, meta={"tipo": "revocacion_pendiente"})
        self.log_op("revocacion.voto", key=key, session_id=autor,
                    payload={"version": version, "n_votos": len(votos), "quorum": quorum,
                             "alcanza_quorum": alcanza})
        if alcanza:
            self.delete(key, autor=propuesta_por, motivo=motivo, categoria=categoria)
            self.log_op("revocacion.ejecutada", key=key, session_id=autor,
                        payload={"quorum": quorum, "votos": votos})
            return {"key": key, "ejecutada": True, "n_votos": len(votos),
                    "quorum": quorum, "estado": "ejecutada"}
        return {"key": key, "ejecutada": False, "n_votos": len(votos),
                "quorum": quorum, "estado": "pendiente"}

    def revocaciones_pendientes(self):
        """Lista las revocaciones propuestas (pendientes/aprobadas, no ejecutadas) para el dashboard."""
        out = []
        for k in self.keys_crudas("revocacion_pendiente:"):
            f = self.current_raw(k)
            if f is None:
                continue
            v = f.get("value") or {}
            if v.get("estado") == "ejecutada":
                continue
            # ya ejecutada (tombstone/denylist presente): no es pendiente de atención
            if v.get("key") and self.esta_revocada(v.get("key")):
                continue
            out.append({
                "key": v.get("key"),
                "propuesta_por": v.get("propuesta_por"),
                "motivo": v.get("motivo"),
                "categoria": v.get("categoria"),
                "quorum": v.get("quorum"),
                "hash_propuesta": v.get("hash_propuesta"),
                "votos": v.get("votos") or {},
                "n_votos": len(v.get("votos") or {}),
                "estado": v.get("estado"),
                "ts_propuesta": v.get("ts_propuesta"),
            })
        out.sort(key=lambda x: x.get("ts_propuesta") or "")
        return out

    # ── acción gobernada (quorum para acciones que degradan la seguridad) ──────
    # Misma maquinaria que el borrado multi-firma, pero genérica: propone EJECUTAR una acción
    # de dominio (rehabilitar/eliminar incidente, aprobar instalación/cableado de herramienta).
    # El MONITOR ejecuta el dominio al alcanzar el quorum (la memoria solo guarda el bookkeeping).
    def _hash_propuesta_accion_gobernada(self, valor):
        import firmas_multifirma as FM
        return FM.hash_propuesta_accion_gobernada(
            valor.get("tipo_accion"), valor.get("objetivo"), valor.get("motivo"),
            int(valor.get("quorum") or 1), valor.get("propuesta_por"),
            valor.get("ts_propuesta"))

    def proponer_accion_gobernada(self, tipo_accion, objetivo, autor=None, motivo=None,
                                  payload=None, quorum=None):
        """PASO 1: propone ejecutar una ACCIÓN GOBERNADA sobre `objetivo`. Crea el hecho interno
        `accion_gobernada:<hash>` (unverified, bookkeeping). NO ejecuta el dominio: espera N
        validadores distintos (el proponente no cuenta). Devuelve {hash_propuesta, quorum, ...}."""
        if not isinstance(tipo_accion, str) or not tipo_accion:
            raise ValueError("tipo_accion requerido")
        if not isinstance(objetivo, str) or not objetivo:
            raise ValueError("objetivo requerido")
        if not isinstance(motivo, str) or not motivo.strip():
            raise ValueError("motivo requerido (por qué se ejecuta la acción)")
        autor = autor or "humano"
        q = self.quorum_revocacion() if quorum is None else quorum
        if isinstance(q, bool) or not isinstance(q, int) or q < 1:
            raise ValueError("quorum debe ser entero >= 1")
        ts_propuesta = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        valor = {
            "tipo": "accion_gobernada",
            "tipo_accion": tipo_accion,
            "objetivo": objetivo,
            "propuesta_por": autor,
            "motivo": motivo.strip(),
            "payload": payload or {},
            "quorum": q,
            "votos": {},
            "estado": "pendiente",
            "ts_propuesta": ts_propuesta,
        }
        valor["hash_propuesta"] = self._hash_propuesta_accion_gobernada(valor)
        version = self.write(PREFIJO_ACCION_GOB + valor["hash_propuesta"], valor, unit="",
                            fuente="usuario", verified=False,
                            meta={"tipo": "accion_gobernada"})
        self.log_op("accion_gobernada.propuesta", key=objetivo, session_id=autor,
                    payload={"tipo_accion": tipo_accion, "quorum": q, "version": version})
        return {"hash_propuesta": valor["hash_propuesta"], "quorum": q,
                "propuesta_por": autor, "n_votos": 0, "estado": "pendiente",
                "tipo_accion": tipo_accion, "objetivo": objetivo}

    def _accion_gobernada(self, hash_propuesta):
        return self.current_raw(PREFIJO_ACCION_GOB + hash_propuesta)

    def votar_accion_gobernada(self, hash_propuesta, autor=None, firma_hex=None, ts_exp=None):
        """PASO 2: un validador DISTINTO del proponente aprueba con voto FIRMADO Ed25519.
        Al alcanzar el quorum marca `aprobada` y devuelve `ejecutable=True` (el MONITOR ejecuta
        el dominio y luego llama `marcar_accion_gobernada_ejecutada`). Idempotente para recuperar
        si quedó aprobada sin ejecutar."""
        if not isinstance(hash_propuesta, str) or not hash_propuesta:
            raise ValueError("hash_propuesta requerido")
        autor = autor or "humano"
        pend = self._accion_gobernada(hash_propuesta)
        if pend is None:
            raise ValueError("no hay acción gobernada pendiente con ese hash")
        valor = dict(pend.get("value") or {})
        propuesta_por = valor.get("propuesta_por")
        quorum = int(valor.get("quorum") or 1)
        tipo_accion = valor.get("tipo_accion")
        objetivo = valor.get("objetivo")
        payload = valor.get("payload") or {}
        estado = valor.get("estado")
        if propuesta_por == autor:
            raise ValueError("el proponente no puede votar su propia propuesta")
        if estado == "ejecutada":
            return {"hash_propuesta": hash_propuesta, "ejecutable": False, "ya_ejecutada": True,
                    "tipo_accion": tipo_accion, "objetivo": objetivo, "quorum": quorum}
        hash_calc = valor.get("hash_propuesta") or self._hash_propuesta_accion_gobernada(valor)
        self._verificar_voto("voto_accion_gobernada", autor, hash_calc, firma_hex, ts_exp)
        votos = dict(valor.get("votos") or {})
        if autor in votos:
            if len(votos) >= quorum:
                # quorum ya alcanzado pero sin ejecutar (recuperación)
                return {"hash_propuesta": hash_propuesta, "ejecutable": True,
                        "n_votos": len(votos), "quorum": quorum, "estado": estado,
                        "tipo_accion": tipo_accion, "objetivo": objetivo, "payload": payload,
                        "propuesta_por": propuesta_por, "reintento": True}
            raise ValueError("este usuario ya votó")
        votos[autor] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "firma": firma_hex, "ts_exp": int(ts_exp),
                        "hash_propuesta": hash_calc}
        nuevo = dict(valor)
        nuevo["votos"] = votos
        alcanza = len(votos) >= quorum
        if alcanza:
            nuevo["estado"] = "aprobada"
            nuevo["ts_aprobada"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.write(PREFIJO_ACCION_GOB + hash_propuesta, nuevo, unit="", fuente="usuario",
                   verified=False, meta={"tipo": "accion_gobernada"})
        self.log_op("accion_gobernada.voto", key=objetivo, session_id=autor,
                    payload={"tipo_accion": tipo_accion, "n_votos": len(votos),
                             "quorum": quorum, "alcanza_quorum": alcanza})
        return {"hash_propuesta": hash_propuesta, "ejecutable": alcanza,
                "n_votos": len(votos), "quorum": quorum, "estado": nuevo["estado"],
                "tipo_accion": tipo_accion, "objetivo": objetivo, "payload": payload,
                "propuesta_por": propuesta_por}

    def marcar_accion_gobernada_ejecutada(self, hash_propuesta, autor=None, resultado=None):
        """El MONITOR, tras ejecutar el dominio, marca la acción `aprobada` → `ejecutada`.
        Queda en la hash chain (imputable)."""
        pend = self._accion_gobernada(hash_propuesta)
        if pend is None:
            return False
        valor = dict(pend.get("value") or {})
        nuevo = dict(valor)
        nuevo["estado"] = "ejecutada"
        nuevo["resultado"] = resultado or {}
        nuevo["ejecutada_por"] = autor or "monitor"
        nuevo["ts_ejecutada"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.write_cas(PREFIJO_ACCION_GOB + hash_propuesta, nuevo, pend["version"],
                       unit="", fuente="monitor", verified=False,
                       meta={"tipo": "accion_gobernada"})
        self.log_op("accion_gobernada.ejecutada", key=valor.get("objetivo", ""),
                    session_id=autor or "monitor",
                    payload={"tipo_accion": valor.get("tipo_accion"), "objetivo": valor.get("objetivo")})
        return True

    def acciones_gobernadas_pendientes(self):
        """Lista las acciones gobernadas pendientes/aprobadas (no ejecutadas) para el dashboard."""
        out = []
        for k in self.keys_crudas(PREFIJO_ACCION_GOB):
            f = self.current_raw(k)
            if f is None:
                continue
            v = f.get("value") or {}
            if v.get("estado") == "ejecutada":
                continue
            out.append({
                "hash_propuesta": v.get("hash_propuesta"),
                "tipo_accion": v.get("tipo_accion"),
                "objetivo": v.get("objetivo"),
                "propuesta_por": v.get("propuesta_por"),
                "motivo": v.get("motivo"),
                "quorum": v.get("quorum"),
                "votos": v.get("votos") or {},
                "n_votos": len(v.get("votos") or {}),
                "estado": v.get("estado"),
                "ts_propuesta": v.get("ts_propuesta"),
            })
        out.sort(key=lambda x: x.get("ts_propuesta") or "")
        return out

    # ── búsqueda (índice FTS5) ────────────────────────────────────────────────
    def _fts_query(self, query):
        terms = [t for t in re.findall(r"[a-z0-9]+", _quitar_acentos(query).lower())
                 if t not in _STOP]
        return " OR ".join(f'"{t}"' for t in terms) if terms else ""

    def buscar(self, query, top_k=5, min_score=None):
        q = self._fts_query(query)
        if not q:
            return []
        try:
            rows = self.conn.execute(
                "SELECT f.key, f.value, f.unit, f.fuente, f.version, bm25(facts_fts) AS r "
                "FROM facts_fts JOIN facts f ON f.version = facts_fts.rowid "
                "WHERE facts_fts MATCH ? AND f.authority=1 AND f.key NOT LIKE 'resp:%' "
                "AND f.key NOT LIKE 'chunk:%' ORDER BY r LIMIT ?",
                (q, top_k)).fetchall()
        except Exception:
            return []
        # bm25() de FTS5 devuelve valores NEGATIVOS (más negativo = mejor match) que crecen en
        # magnitud con el tamaño del corpus, por eso NO aplicamos un umbral absoluto por defecto:
        # un umbral fijo (ej. -4.0) descartaba matches legítimos a medida que la memoria crecía.
        # Se ordena por r ASC (mejor match primero) y se devuelve top_k; el LLM decide relevancia.
        out = []
        for r in rows:
            score = None if r["r"] is None else round(float(r["r"]), 4)
            if min_score is not None and (score is None or score < min_score):
                continue
            out.append({"key": r["key"], "value": _dec(r["value"]), "unit": r["unit"],
                        "fuente": r["fuente"], "version": r["version"], "score": score})
        return out

    def buscar_claimed(self, query, top_k=5):
        """Igual que `buscar` pero sobre el índice de CLAIMS (verified=0, authority=0, stale=0).

        Devuelve hechos con `tier='claimed'` (no verificados). El recall los usa como contexto
        ATRIBUIDO ('me dijiste que…'), nunca como respuesta determinista ni como input de
        calculadora/skills (eso exige verified)."""
        q = self._fts_query(query)
        if not q:
            return []
        try:
            rows = self.conn.execute(
                "SELECT f.key, f.value, f.unit, f.fuente, f.version, f.meta, bm25(facts_fts_claimed) AS r "
                "FROM facts_fts_claimed JOIN facts f ON f.version = facts_fts_claimed.rowid "
                "WHERE facts_fts_claimed MATCH ? AND f.verified=0 AND f.authority=0 AND f.stale=0 "
                "AND f.key NOT LIKE 'resp:%' AND f.key NOT LIKE 'chunk:%' ORDER BY r LIMIT ?",
                (q, top_k)).fetchall()
        except Exception:
            return []
        out = []
        for r in rows:
            score = None if r["r"] is None else round(float(r["r"]), 4)
            out.append({"key": r["key"], "value": _dec(r["value"]), "unit": r["unit"],
                        "fuente": r["fuente"], "version": r["version"], "score": score,
                        "tier": "claimed", "meta": _dec(r["meta"])})
        return out

    def buscar_chunks(self, query, top_k=5):
        """Recupera CHUNKS del córtex (libros/documentos largos mapeados), no hechos del usuario.

        Busca solo `chunk:*` (fuente 'cortex', unverified) por FTS y devuelve la PORCIÓN relevante
        (p.ej. un versículo), nunca el documento entero. El índice FTS usa el texto crudo
        (json_extract(value,'$')). """
        q = self._fts_query(query)
        if not q:
            return []
        try:
            rows = self.conn.execute(
                "SELECT f.key, f.value, f.meta, f.fuente, f.version, bm25(facts_fts_claimed) AS r "
                "FROM facts_fts_claimed JOIN facts f ON f.version = facts_fts_claimed.rowid "
                "WHERE facts_fts_claimed MATCH ? AND f.key LIKE 'chunk:%' AND f.stale=0 "
                "ORDER BY r LIMIT ?",
                (q, top_k)).fetchall()
        except Exception:
            return []
        out = []
        for r in rows:
            score = None if r["r"] is None else round(float(r["r"]), 4)
            out.append({"key": r["key"], "value": _dec(r["value"]), "meta": _dec(r["meta"]),
                        "fuente": r["fuente"], "version": r["version"], "score": score})
        return out

    def buscar_chunk_posicion(self, page=None, para=None, doc_sha=None):
        """Lookup POSICIONAL de un chunk por coordenadas (page, para): devuelve la PORCIÓN exacta
        ('tercer párrafo de la página 50'), no una búsqueda semántica. `doc_sha` (primeros 16 del
        hash de la raíz) opcional para acotar a UN documento."""
        conds = ["key LIKE 'chunk:%'", "stale=0"]
        args = []
        if page is not None:
            conds.append("json_extract(meta, '$.page') = ?")
            args.append(page)
        if para is not None:
            conds.append("json_extract(meta, '$.para') = ?")
            args.append(para)
        if doc_sha:
            conds.append("key LIKE ?")
            args.append("chunk:" + doc_sha + ":%")
        sql = ("SELECT key, value, meta, fuente, version FROM facts WHERE " +
               " AND ".join(conds) + " ORDER BY version DESC LIMIT 1")
        row = self.conn.execute(sql, args).fetchone()
        if row is None:
            return None
        return {"key": row["key"], "value": _dec(row["value"]), "meta": _dec(row["meta"]),
                "fuente": row["fuente"], "version": row["version"]}

    def buscar_chunk_por_ref(self, ref, doc_sha=None):
        """Lookup de un chunk por su REFERENCIA ('Juan 3:16'): devuelve la PORCIÓN exacta, no una
        búsqueda semántica. `doc_sha` opcional para acotar a UN documento."""
        conds = ["key LIKE 'chunk:%'", "stale=0", "json_extract(meta, '$.ref') = ?"]
        args = [ref]
        if doc_sha:
            conds.append("key LIKE ?")
            args.append("chunk:" + doc_sha + ":%")
        sql = ("SELECT key, value, meta, fuente, version FROM facts WHERE " +
               " AND ".join(conds) + " ORDER BY version DESC LIMIT 1")
        row = self.conn.execute(sql, args).fetchone()
        if row is None:
            return None
        return {"key": row["key"], "value": _dec(row["value"]), "meta": _dec(row["meta"]),
                "fuente": row["fuente"], "version": row["version"]}

    def buscar_chunk_por_cap_vers(self, cap_vers, doc_sha=None):
        """Lookup de un chunk por el NÚCLEO 'Cap:Vers' (p.ej. '3:16'), ignorando el libro.

        Resuelve el desajuste entre lo que dice el usuario ('Juan 3:16') y el `meta.ref` guardado
        ('3:16' o 'Juan 3:16'). Match por sufijo exacto ('... 3:16' o '3:16'), NO por subcadena
        (así '23:16' no matchea '3:16')."""
        conds = ["key LIKE 'chunk:%'", "stale=0", "json_extract(meta, '$.ref') IS NOT NULL"]
        args = []
        if doc_sha:
            conds.append("key LIKE ?")
            args.append("chunk:" + doc_sha + ":%")
        rows = self.conn.execute(
            "SELECT key, value, meta, fuente, version FROM facts WHERE " +
            " AND ".join(conds) + " ORDER BY version DESC", args).fetchall()
        for r in rows:
            meta = _dec(r["meta"])
            ref = str(meta.get("ref") or "")
            if ref == cap_vers or ref.endswith(" " + cap_vers):
                return {"key": r["key"], "value": _dec(r["value"]), "meta": meta,
                        "fuente": r["fuente"], "version": r["version"]}
        return None

    # ── migración desde la versión en RAM ──────────────────────────────────────
    def migrar_desde_ram(self, ram):
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            for key, facts in ram.facts.items():
                for f in facts:
                    c.execute(
                        "INSERT INTO facts(version,key,value,unit,verified,authority,stale,fuente,meta,derived_from) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (f["version"], f["key"], _enc(f["value"]), f["unit"],
                         int(f["verified"]), int(f["authority"]), int(f["stale"]),
                         f["fuente"], _enc(f.get("meta", {})), _enc(f.get("derived_from", {}))))
                    for dk, dv in (f.get("derived_from") or {}).items():
                        c.execute("INSERT INTO lineage(child_version,dep_key,dep_version) VALUES(?,?,?)",
                                  (f["version"], dk, dv))
            c.execute("DELETE FROM facts_fts")
            c.execute("INSERT INTO facts_fts(rowid,key,texto) "
                      "SELECT version, key, key || ' ' || COALESCE(json_extract(value,'$'),'') || ' ' || unit || ' ' || fuente "
                      "FROM facts WHERE authority=1")
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise

    # ── integridad de la cadena (marcador de payload + verify) ─────────────────
    def marcar_payload_ver(self):
        """Emite UNA vez un marcador de corte de payload en la hash chain (op=payload_ver, pv=N).

        El marcador deja constancia del punto a partir del cual los writes DEBEN llevar payload
        versionado (pv=2 con procedencia). verify() lo usa para detectar downgrade (escribir pv=1
        después del corte). Idempotente (flag en hcma_meta)."""
        if self.conn.execute("SELECT 1 FROM hcma_meta WHERE k='payload_ver_marked'").fetchone():
            return
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            self._append_log("payload_ver", "", -1, {"pv": PAYLOAD_VER, "op": "payload_ver"})
            c.execute("INSERT INTO hcma_meta(k,v) VALUES('payload_ver_marked',?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(PAYLOAD_VER),))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise

    def verify(self):
        """Reproduce la hash chain y reconcilia facts_log ↔ facts. LECTURA PURA.

        Detecta: cadena rota (hash no encadena), last_log_hash desincronizado, downgrade de
        payload (write pv≠PAYLOAD_VER después del marcador), hechos sin evento de write, eventos
        write sin hecho, y payload divergente (fact.value ≠ log.val). Devuelve dict de diagnóstico."""
        problemas = []
        c = self.conn
        # Snapshot consistente (WAL): el monitor escribe en paralelo; sin transacción las
        # SELECT de facts_log/hcma_meta/facts podían straddlear un write y dar falso 'ROTA'
        # o falsos fact_sin_evento / evento_sin_fact.
        c.execute("BEGIN")
        # 1) reproducir la cadena de hash
        prev_h = ""
        head = ""
        seq_total = 0
        rota = False
        for r in c.execute("SELECT seq, payload, hash FROM facts_log ORDER BY seq").fetchall():
            seq_total += 1
            h = hashlib.sha256((prev_h + "|" + r["payload"]).encode()).hexdigest()
            if not rota and h != r["hash"]:
                problemas.append({"tipo": "cadena_rota", "seq": r["seq"]})
                rota = True
            prev_h = r["hash"]
            head = r["hash"]
        meta = c.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
        if meta is None or meta[0] != head:
            problemas.append({"tipo": "last_log_hash_desincronizado",
                              "esperado": head, "actual": meta[0] if meta else None})
        # 2) downgrade de payload después del marcador de corte
        mark = c.execute("SELECT MIN(seq) FROM facts_log WHERE op='payload_ver'").fetchone()[0]
        if mark:
            for r in c.execute("SELECT seq, payload FROM facts_log WHERE op='write' AND seq>?",
                               (mark,)).fetchall():
                try:
                    pl = json.loads(r["payload"])
                except Exception:
                    problemas.append({"tipo": "payload_invalido", "seq": r["seq"]})
                    continue
                if pl.get("pv") != PAYLOAD_VER:
                    problemas.append({"tipo": "downgrade_payload", "seq": r["seq"],
                                      "pv": pl.get("pv")})
        # 3) reconciliar facts ↔ eventos write
        for r in c.execute("SELECT version, key, value FROM facts").fetchall():
            ev = c.execute("SELECT payload FROM facts_log WHERE op='write' AND version=?",
                           (r["version"],)).fetchone()
            if ev is None:
                problemas.append({"tipo": "fact_sin_evento", "version": r["version"],
                                  "key": r["key"]})
                continue
            try:
                pl = json.loads(ev[0])
            except Exception:
                problemas.append({"tipo": "payload_invalido", "version": r["version"]})
                continue
            try:
                val_dec = _dec(r["value"])
            except Exception:
                problemas.append({"tipo": "fact_value_invalido", "version": r["version"]})
                continue
            if (pl.get("k") != r["key"] or
                    json.dumps(pl.get("val"), sort_keys=True, ensure_ascii=False) !=
                    json.dumps(val_dec, sort_keys=True, ensure_ascii=False)):
                problemas.append({"tipo": "fact_payload_divergente",
                                  "version": r["version"], "key": r["key"]})
        for r in c.execute("SELECT seq, version FROM facts_log WHERE op='write' AND version>0").fetchall():
            if c.execute("SELECT 1 FROM facts WHERE version=?", (r["version"],)).fetchone() is None:
                problemas.append({"tipo": "evento_sin_fact", "seq": r["seq"], "version": r["version"]})
        ancla = self._ancla_estado()
        if ancla and ancla.get("truncada"):
            problemas.append({"tipo": "ancla_truncada",
                              "ancla_seq": ancla.get("ancla_seq"),
                              "store_seq": ancla.get("store_seq")})
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
        return {"ok": len(problemas) == 0, "problemas": problemas,
                "seq_total": seq_total, "head_hash": head, "ancla": ancla}

    # ── ancla externa (P0 A1) ──────────────────────────────────────────────────
    def _anclar(self):
        """Publica (seq, head_hash) en el ancla externa (si está configurada). Best-effort: no
        rompe la operación principal si el ancla no es escribible (pero lo reporta a stderr)."""
        if not self.ancla_path:
            return False
        try:
            from ancla_publica import AnclaPublica
            seq = self.conn.execute("SELECT COALESCE(MAX(seq),0) FROM facts_log").fetchone()[0]
            head = self.conn.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
            head = head[0] if head else ""
            AnclaPublica(self.ancla_path).anclar(seq, head)
            return True
        except Exception as e:
            import sys
            print(f"⚠️ ancla externa no escrita ({self.ancla_path}): {e}", file=sys.stderr)
            return False

    def _ancla_estado(self):
        """Estado del ancla externa vs el store (para verify). None si no hay ancla configurada."""
        if not self.ancla_path:
            return None
        try:
            from ancla_publica import AnclaPublica
            a = AnclaPublica(self.ancla_path)
            _entradas, integra = a.leer()
            ultimo = a.ultimo()
            seq_max = self.conn.execute("SELECT COALESCE(MAX(seq),0) FROM facts_log").fetchone()[0]
            head = self.conn.execute("SELECT v FROM hcma_meta WHERE k='last_log_hash'").fetchone()
            head = head[0] if head else ""
            return {"integra": integra, "ancla_seq": ultimo.get("seq") if ultimo else None,
                    "ancla_head": ultimo.get("head_hash") if ultimo else None,
                    "store_seq": seq_max, "store_head": head,
                    "truncada": bool(ultimo and seq_max < int(ultimo.get("seq", 0)))}
        except Exception as e:
            return {"error": str(e)}

    def close(self):
        self._anclar()
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        self.conn.close()
