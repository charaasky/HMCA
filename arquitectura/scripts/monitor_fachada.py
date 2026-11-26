#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""monitor_fachada.py — Fase 2 (paso 1): fachada in-process de `pedir(op)`.

Dictamen de Fable (fable_fase2_monitor_fable.md): antes del corte a IPC, el runtime debe pasar
a llamar SOLO a una fachada `pedir(op)` en el mismo proceso. Este refactor no da seguridad, pero
cubre el cambio riesgoso con los tests actuales y deja listo el corte único posterior.

La fachada implementa el CONTRATO del protocolo (FASE2_monitor.md):
  - El agente DECLARA intención y parámetros; el monitor DERIVA campos críticos.
  - Las operaciones son TIPADAS (no es un dispatcher genérico pasivo).
  - Entrada JSON validada con tope de tamaño ANTES de parsear (superficie acotada).

NOTA: en este paso la fachada importa los módulos reales (memoria_sqlite, gobernanza_accion,
nonces_monitor, objetivo, epoca) PORQUE aún es in-process. En el paso 2 (corte a IPC), el cliente
del agente dejará de importarlos y solo hablará por FD heredado. Esta separación ya está reflectada
en la frontera de métodos: el agente solo ve `pedir(op)`, nunca las clases internas.

Operaciones soportadas (todas devuelven {ok, ...} o {ok:False, detalle}):
  leer_clave            -> current(key) canónico con procedencia EXPLÍCITA
  propuesta_write       -> el agente propone un hecho; el monitor decide verified/authority
  pedir_autorizacion    -> pide permiso para una acción (riesgo derivado por tool)
  consumir_autorizacion -> consume un nonce (la firma llega del signer, NO del agente)
  registrar_evidencia   -> ata evidencia hasheada a un objetivo (capturada por el orquestador)
  estado                -> state() resumido (solo current, canónico)
"""
import json
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# Tope de tamaño ANTES de parsear (anti-DoS en el parseo de input no confiable).
MAX_OP_BYTES = 1 << 20  # 1 MiB


def _slug_skill(s):
    """Slug canónico de un id de skill. DEBE coincidir con router_skills._slug: ambos delegan
    en slug_canon.slug (hoja stdlib). El monitor importa slug_canon, NO el router del agente
    (que vive en dashboard/ y está fuera de su cierre de dependencias)."""
    import slug_canon
    return slug_canon.slug(s)


class OpError(ValueError):
    """Operación inválida, malformada o no tipada (=> denegación)."""


def _validar_op(op):
    """Valida el frame de la operación antes de ejecutar. Devuelve el dict si es válido."""
    if not isinstance(op, dict):
        raise OpError("operación no es dict")
    tipo = op.get("tipo")
    if not isinstance(tipo, str) or not tipo:
        raise OpError("operación sin tipo válido")
    if len(json.dumps(op, ensure_ascii=False)) > MAX_OP_BYTES:
        raise OpError("operación excede tope de tamaño")
    return tipo


class MonitorFachada:
    """Fachada in-process del monitor. El runtime del agente solo habla `pedir(op)`."""

    def __init__(self, memoria, registro_nonces=None, epoca_actual=0):
        self.memoria = memoria
        self.registro_nonces = registro_nonces
        self.epoca_actual = epoca_actual

    # ── dispatcher público ──────────────────────────────────────────────────
    def pedir(self, op, session_id=None):
        """Punto único de entrada. Toda excepción => denegación (fail-closed).

        CONDICIÓN P0 #3 (multiagente, dictamen Fable): `session_id` lo asigna el MONITOR (vive
        en el objeto Peer del event loop), NO lo declara el agente en el op. El agente nunca
        puede setear su propia sesión; el transport lo inyecta. Se propaga a las operaciones
        que requieren ligado de identidad (pedir_egreso).
        """
        try:
            tipo = _validar_op(op)
            handler = getattr(self, "_op_" + tipo, None)
            if handler is None:
                return {"ok": False, "detalle": f"operación desconocida: {tipo}"}
            return handler(op, session_id=session_id)
        except OpError as e:
            return {"ok": False, "detalle": f"operación inválida: {e}"}
        except Exception as e:  # noexcept: toda excepción es denegación
            return {"ok": False, "detalle": f"monitor: {type(e).__name__}: {e}"}

    # ── operaciones ─────────────────────────────────────────────────────────
    def _op_leer_clave(self, op, session_id=None):
        key = op.get("key")
        if not isinstance(key, str) or not key:
            raise OpError("leer_clave requiere key str")
        fact = self.memoria.current(key)
        if fact is None:
            return {"ok": True, "fact": None}
        # procedencia EXPLÍCITA en cada lectura (anti lavado de memoria)
        return {"ok": True, "fact": {k: fact[k] for k in
                                     ("key", "value", "unit", "version", "verified",
                                      "authority", "stale", "fuente", "meta", "derived_from")}}

    def _op_propuesta_write(self, op, session_id=None):
        # el agente DECLARA key/value/unit; el monitor DERIVA verified/authority/version.
        # `propuesta_write` escribe como PROPUESTA (unverified). El agente NO puede declarar
        # verified=True (eso se deriva de FUENTE_CONFIABLE + linaje dentro de write() y de la
        # decisión del monitor). Si el agente intenta pasar verified=True, se ignora.
        key = op.get("key")
        value = op.get("value")
        unit = op.get("unit", "")
        fuente = op.get("fuente", "base")
        if not isinstance(key, str) or not key:
            raise OpError("propuesta_write requiere key str")
        # verified SIEMPRE False desde la frontera (derivado por el monitor, no declarado)
        try:
            version = self.memoria.write(key, value, unit=unit, fuente=fuente, verified=False,
                                         session_id=session_id)
        except Exception as e:
            return {"ok": False, "detalle": f"write rechazado: {e}"}
        # devolver la versión y el fact crudo (unverified), con procedencia real
        cur = self.memoria.current(key)
        return {"ok": True, "version": version, "current": cur,
                "detalle": "propuesta registrada (unverified; la propagación a verified la "
                           "decide el monitor por procedencia)"}

    def _op_escribir_hecho(self, op, session_id=None):
        """HUMANO (chat del dashboard): persiste un hecho declarado por el usuario como CLAIM
        (fuente 'chat:usuario', verified=0, authority=0). El chat NUNCA auto-verifica; la
        promoción a verified es una acción humana aparte (op gobernada, con su propio quorum).
        Solo alcanzable por el socket de gobernanza (allowlist GOBERNANZA_OPS).
        Guarda el usuario concreto en meta.usuario (procedencia de QUIÉN lo cargó)."""
        key = op.get("key")
        value = op.get("value")
        unit = op.get("unit", "")
        usuario = op.get("usuario") or session_id or "humano"
        derived_from = op.get("derived_from") or {}
        if not isinstance(key, str) or not key:
            raise OpError("escribir_hecho requiere key str")
        if value is None:
            raise OpError("escribir_hecho requiere value")
        if not isinstance(unit, str):
            unit = ""
        if not isinstance(usuario, str):
            usuario = str(usuario)
        if not isinstance(derived_from, dict):
            raise OpError("escribir_hecho: derived_from debe ser dict")
        # P0.1 Fable: un write con FORMA de alias se enruta a crear_alias (arista), no a hecho
        # legacy (bare S / S:apodo / E:apodo invertida / S:es_E).
        import alias_backfill as AB
        par = AB.clasificar_alias(key, value)
        if par is not None:
            surface, target = par
            r = self.memoria.crear_alias(surface, target, fuente="chat:usuario", verified=False,
                                         session_id=session_id or "humano")
            if r is None:
                return {"ok": False, "detalle": "crear_alias: surface o target vacío"}
            return {"ok": True, "version": r, "tier": "claimed",
                    "detalle": f"alias:{surface} -> {target} registrada como claim (no auto-verificada)"}
        # P0.1 Fable: rechazar write a X o X:* si ya existe alias:X (no re-fragmentar el namespace).
        prefijo = key.split(":", 1)[0]
        if not key.startswith("alias:") and self.memoria.alias_target(prefijo) is not None:
            return {"ok": False,
                    "detalle": f"write rechazado: '{prefijo}' es una surface de alias (escribí alias:{prefijo})"}
        try:
            # P0-A: el chat NUNCA auto-verifica. Sus hechos entran como CLAIMED (fuente
            # 'chat:usuario', verified=0, authority=0). No pisan un verified (si el valor
            # difiere, se guardan con meta.conflicts_with). `derived_from` liga el claim a su
            # origen (p.ej. un PDF ingerido) para el borrado por herencia. La promoción a
            # verified es una acción humana aparte (op gobernada), no esta vía.
            version = self.memoria.write(key, value, unit=unit, fuente="chat:usuario",
                                         verified=False,
                                         meta={"usuario": usuario, "tipo": "claimed"},
                                         derived_from=derived_from,
                                         session_id=session_id or "humano")
        except Exception as e:
            return {"ok": False, "detalle": f"write rechazado: {e}"}
        cur = self.memoria.current(key)
        # Dedup: si el claim coincidía con el verified vigente, write() devolvió la versión
        # verified y NO se creó ninguna fila nueva.
        if cur is not None and cur["version"] == version:
            return {"ok": True, "version": version, "current": cur, "tier": "verified",
                    "detalle": "ya existía un hecho verified con el mismo valor (sin cambios)"}
        claim = self.memoria.get_version(key, version) if version else None
        return {"ok": True, "version": version, "current": cur,
                "tier": "claimed", "claim": claim,
                "detalle": "hecho persistido como claim (sin auto-verificar; sujeto a "
                           "promoción humana)"}

    def _op_escribir_respuesta(self, op, session_id=None):
        """HUMANO (chat): persiste una RESPUESTA advisory del LLM para REUTILIZARLA (cache).

        Invariantes (dictamen Fable fable_respuesta_cache): clave `resp:<usuario>:<hash>`,
        fuente 'llm:<provider>/<model>', verified=0/authority=0 (NUNCA promovible: lo blinda el
        trigger DB), meta.tipo='respuesta' con ctx_fp/model_fp/ts. `derived_from` liga a los
        hechos que la fundamentaron (borrado por herencia + invalidación por stale).

        Idempotente: si la respuesta vigente tiene el MISMO valor, no genera versión nueva (la
        cola de write-back es at-least-once)."""
        key = op.get("key")
        value = op.get("value")
        usuario = op.get("usuario") or session_id or "humano"
        modelo = op.get("modelo") or "desconocido"
        ctx_fp = op.get("ctx_fp") or ""
        model_fp = op.get("model_fp") or ""
        ts = op.get("ts") or ""
        derived_from = op.get("derived_from") or {}
        if not isinstance(key, str) or not key.startswith("resp:"):
            raise OpError("escribir_respuesta requiere key con prefijo resp:")
        if value is None or not isinstance(value, str) or not value.strip():
            raise OpError("escribir_respuesta requiere value str no vacío")
        if not isinstance(usuario, str):
            usuario = str(usuario)
        if not isinstance(modelo, str):
            modelo = str(modelo)
        if not isinstance(derived_from, dict):
            raise OpError("escribir_respuesta: derived_from debe ser dict")
        for dk in derived_from:
            if str(dk).startswith("resp:"):
                raise OpError("escribir_respuesta: derived_from no puede citar resp:*")
        if len(value.encode("utf-8")) > 16384:
            raise OpError("escribir_respuesta: respuesta excede 16 KB")
        # Idempotencia at-least-once: si la respuesta vigente ya es la MISMA (valor, contexto y
        # modelo), no reescribir. Si cambió el contexto (ctx_fp) o el modelo (model_fp), es una
        # entrada de cache DISTINTA para la misma key => se escribe una versión nueva.
        cur = self.memoria.current_raw(key)
        if (cur is not None and not cur["stale"] and cur["value"] == value
                and (cur["meta"] or {}).get("ctx_fp") == ctx_fp
                and (cur["meta"] or {}).get("model_fp") == model_fp):
            return {"ok": True, "version": cur["version"], "key": key,
                    "detalle": "respuesta cacheada ya vigente (sin cambios)"}
        fuente = "llm:" + modelo.replace(" ", "_").replace("|", "/")
        meta = {"tipo": "respuesta", "usuario": usuario, "modelo": modelo,
                "ctx_fp": ctx_fp, "model_fp": model_fp, "ts": ts}
        try:
            version = self.memoria.write(key, value, unit="", fuente=fuente, verified=False,
                                         meta=meta, derived_from=derived_from,
                                         session_id=session_id or "humano")
        except Exception as e:
            return {"ok": False, "detalle": f"write rechazado: {e}"}
        return {"ok": True, "version": version, "key": key,
                "detalle": "respuesta cacheada (unverified, no promovible)"}

    def _op_escribir_chunk(self, op, session_id=None):
        """HUMANO (ingesta de documento largo): persiste un CHUNK del córtex (libro mapeado).

        `chunk:<sha>:<n>` con fuente 'cortex', verified=0/authority=0, meta.kind='chunk' y linaje
        a la raíz `doc:`. Es conocimiento externo, NO un hecho del usuario (recuperable por
        porción vía buscar_chunks, no contamina el recall del hipocampo)."""
        key = op.get("key")
        value = op.get("value")
        meta = op.get("meta") or {}
        derived_from = op.get("derived_from") or {}
        if not isinstance(key, str) or not key.startswith("chunk:"):
            raise OpError("escribir_chunk requiere key con prefijo chunk:")
        if value is None or not isinstance(value, str) or not value.strip():
            raise OpError("escribir_chunk requiere value str no vacío")
        if not isinstance(meta, dict):
            meta = {}
        if not isinstance(derived_from, dict):
            raise OpError("escribir_chunk: derived_from debe ser dict")
        meta = dict(meta)
        meta["kind"] = "chunk"
        try:
            version = self.memoria.write(key, value, unit="", fuente="cortex", verified=False,
                                         meta=meta, derived_from=derived_from,
                                         session_id=session_id or "humano")
        except Exception as e:
            return {"ok": False, "detalle": f"write rechazado: {e}"}
        return {"ok": True, "version": version, "key": key,
                "detalle": "chunk del córtex persistido (unverified)"}

    def _op_escribir_calculo(self, op, session_id=None):
        """HUMANO (router de cálculo del chat): persiste un resultado DETERMINISTA como fuente
        confiable 'calculadora'. Exige derived_from (linaje) no vacío: un resultado de cálculo sin
        linaje no puede auto-certificarse (regla E1 del paper)."""
        key = op.get("key")
        value = op.get("value")
        unit = op.get("unit", "")
        derived_from = op.get("derived_from") or {}
        if not isinstance(key, str) or not key:
            raise OpError("escribir_calculo requiere key str")
        if value is None:
            raise OpError("escribir_calculo requiere value")
        if not isinstance(unit, str):
            unit = ""
        if not isinstance(derived_from, dict):
            raise OpError("escribir_calculo requiere derived_from dict")
        try:
            version = self.memoria.write(key, value, unit=unit, fuente="calculadora",
                                         verified=True, derived_from=derived_from,
                                         session_id=session_id or "humano")
        except Exception as e:
            return {"ok": False, "detalle": f"write rechazado: {e}"}
        cur = self.memoria.current(key)
        return {"ok": True, "version": version, "current": cur,
                "detalle": "cálculo persistido (fuente calculadora)"}

    def _op_promover_claim(self, op, session_id=None):
        """HUMANO: promueve un claim (verified=0, fuente chat:*) a VERIFIED.

        Crea una versión nueva fuente='promocion:humano' con derived_from=[claim]; la fila del
        chat NUNCA se vuelve verified. Es la única vía para que un hecho del chat se vuelva
        canónico (no hay auto-promoción). Guarda meta.evidence (cita/msg_id)."""
        key = op.get("key")
        claimed_version = op.get("version")
        autor = op.get("autor") or session_id or "humano"
        evidence = op.get("evidence")
        if not isinstance(key, str) or not key:
            raise OpError("promover_claim requiere key str")
        if not isinstance(claimed_version, int) or claimed_version <= 0:
            raise OpError("promover_claim requiere version int positiva")
        try:
            version = self.memoria.promover_claim(key, claimed_version, autor,
                                                  evidence=evidence,
                                                  session_id=session_id or "humano")
        except Exception as e:
            return {"ok": False, "detalle": f"promoción rechazada: {e}"}
        cur = self.memoria.current(key)
        return {"ok": True, "version": version, "current": cur,
                "detalle": "claim promovido a verified (fuente promocion:humano)"}

    def _op_crear_skill(self, op, session_id=None):
        """HUMANO (señal explícita) o AUTO (juez del chat): crea una CANDIDATA de skill
        (autor=modelo, unverified, no ruteable ni ejecutable). Acepta `valor` (spec generada por
        el LLM, dict) y `nombre` (slug explícito); si no, usa el contexto crudo. Escribe DIRECTA:
        el monitor NO importa el router del agente (router_skills vive en dashboard/, fuera de su
        cierre de dependencias). El `valor` dict se serializa a JSON vía m.write (_enc)."""
        contexto = op.get("contexto")
        valor = op.get("valor") or None
        nombre = op.get("nombre") or None
        if not isinstance(contexto, str) or not contexto.strip():
            raise OpError("crear_skill requiere contexto str")
        base = nombre if isinstance(nombre, str) and nombre.strip() else contexto
        sid = _slug_skill(base)
        if not sid:
            return {"ok": False, "detalle": "crear_skill: contexto sin contenido identificable"}
        # P0-2: validar el slug en CÓDIGO (no confiar solo en el juez). slug_canon ya devuelve
        # [a-z0-9_] pero lo validamos explícito como red de seguridad.
        import re as _re
        if not _re.fullmatch(r"[a-z][a-z0-9_]{2,63}", sid):
            return {"ok": False, "detalle": f"crear_skill: slug inválido {sid!r}"}
        import slug_canon
        meta = {"tipo": "skill", "costo": 0,
                "slug_algo": slug_canon.SLUG_ALGO,
                "slug_origen": "hash" if sid.startswith("sk_") else "legacy"}
        try:
            version = self.memoria.write("skill:" + sid, valor if valor else base,
                                         unit="", fuente="modelo", verified=False,
                                         meta=meta, derived_from={})
        except Exception as e:
            return {"ok": False, "detalle": f"crear_skill: {e}"}
        return {"ok": True, "skill_id": sid, "version": version,
                "detalle": "candidata de skill creada (autor=modelo, unverified; requiere ratificación humana)"}

    def _op_crear_alias(self, op, session_id=None):
        """HUMANO / migración: materializa una arista alias tipada `alias:<surface> -> target`
        (P0-B). `fuente` por defecto 'extractor'; para backfill usar 'migration:alias_v2'.
        `verified` define si la arista es canónica (authority)."""
        surface = op.get("surface")
        target = op.get("target")
        fuente = op.get("fuente") or "extractor"
        verified = bool(op.get("verified"))
        if not isinstance(surface, str) or not surface.strip():
            raise OpError("crear_alias requiere surface str")
        if not isinstance(target, str) or not target.strip():
            raise OpError("crear_alias requiere target str")
        r = self.memoria.crear_alias(surface, target, fuente=fuente, verified=verified,
                                    session_id=session_id or "humano")
        if r is None:
            return {"ok": False, "detalle": "crear_alias: surface o target vacío tras normalizar"}
        return {"ok": True, "version": r,
                "detalle": f"arista alias:{surface} -> {target} creada (verified={verified})"}

    def _op_crear_prop(self, op, session_id=None):
        """DEPRECATED (dictamen fable_propiedad_llm_fable.md): la PROPIEDAD es conocimiento
        público (sinónimos como 'llamaba'≈'nombre' que el LLM ya sabe en todos los idiomas) y la
        resuelve el LLM consumidor. NO se materializa como arista `prop:*`.

        Solo el vocabulario IDIOSINCRÁTICO declarado por el usuario (ej. 'el chip' = nro_registro)
        se modela, y va como ALIAS (`crear_alias`), no como prop."""
        raise OpError("crear_prop deprecada: la propiedad es semántica (LLM); usá crear_alias "
                      "para vocabulario idiosincrático")

    def _op_retirar(self, op, session_id=None):
        """MIGRACIÓN (P0.3 Fable): retira un hecho vigente (stale=1) superseded por una arista
        alias. NO es borrado legal: no denylist, solo migración de namespace legacy."""
        key = op.get("key")
        motivo = op.get("motivo") or "migracion:alias_v2"
        if not isinstance(key, str) or not key.strip():
            raise OpError("retirar requiere key str")
        n = self.memoria.retirar(key, motivo=motivo, autor="migracion:alias_v2",
                                session_id=session_id or "humano")
        return {"ok": True, "retirados": n, "detalle": f"hecho {key} retirado (stale)"}

    def _op_pedir_autorizacion(self, op, session_id=None):
        # el monitor DERIVA el riesgo (tabla estática por tool), no lo acepta del agente.
        # riesgo_efectivo = max(estático, argumentos, declarado por el modelo) — ver conectores.py.
        tool_id = op.get("tool_id")
        artifacto = op.get("artifacto", {})
        if not isinstance(tool_id, str) or not tool_id:
            raise OpError("pedir_autorizacion requiere tool_id str")
        import conectores as C
        riesgo = C.riesgo_efectivo(tool_id, artifacto, riesgo_declarado=op.get("riesgo_declarado"))
        # el nonce y la firma NO los genera el agente aquí; se piden al signer vía monitor.
        return {"ok": True, "tool_id": tool_id, "riesgo": riesgo,
                "detalle": "firma y nonce se gestionan en el corte a IPC (signer, no agente)"}

    def _op_consumir_autorizacion(self, op, session_id=None):
        # la firma NO llega del agente (invariante); en la fachada in-process solo validamos
        # que exista un registro de nonces. El corte a IPC moverá la verificación de firma aquí.
        nonce = op.get("nonce")
        tool_id = op.get("tool_id")
        hash_accion = op.get("hash_accion")
        if not isinstance(nonce, str) or not nonce:
            raise OpError("consumir_autorizacion requiere nonce str")
        if self.registro_nonces is None:
            return {"ok": False, "detalle": "registro de nonces no disponible"}
        consumido = self.registro_nonces.consumir(nonce, tool_id, hash_accion)
        return {"ok": consumido, "detalle": "consumido" if consumido else "replay"}

    def _op_registrar_evidencia(self, op, session_id=None):
        # La firma del objetivo y el evidencia_hash NO los declara el agente:
        #   - objetivo+firma vienen del humano/signer (el agente no crea ni firma objetivos).
        #   - evidencia_hash lo CAPTURA el orquestador de la salida real de la herramienta.
        # En la fachada in-process se delega a objetivo.registrar_evidencia, que verifica la
        # firma del objetivo contra la clave humana. El agente solo pasa la REFERENCIA (ids),
        # nunca el contenido firmado ni el hash como testimonio propio.
        objetivo = op.get("objetivo")
        firma = op.get("firma")
        subobjetivo_id = op.get("subobjetivo_id")
        evidencia_hash = op.get("evidencia_hash")
        herramienta = op.get("herramienta", "")
        if not isinstance(objetivo, dict) or not isinstance(firma, str) \
                or not isinstance(subobjetivo_id, str) or not isinstance(evidencia_hash, str):
            raise OpError("registrar_evidencia requiere objetivo(dict) firma(str) "
                          "subobjetivo_id(str) evidencia_hash(str)")
        import objetivo as OBJ
        res = OBJ.registrar_evidencia(self.memoria, objetivo, firma, subobjetivo_id,
                                      evidencia_hash, herramienta=herramienta)
        if res is None:
            return {"ok": False, "detalle": "firma de objetivo inválida o evidencia no atada"}
        return {"ok": True, "clave": res[0], "version": res[1]}

    def _op_estado(self, op, session_id=None):
        st = self.memoria.state()
        return {"ok": True, "estado": st}

    # ── pool de tareas (tareas.py) ─────────────────────────────────────────
    def _op_registrar_tarea(self, op, session_id=None):
        """Registra una tarea PENDIENTE con dependencias. El orquestador la declara; el monitor
        escribe (único escritor). Rechaza id duplicado o ciclo en el DAG."""
        tid = op.get("id")
        descripcion = op.get("descripcion", "")
        depends_on = op.get("depends_on", [])
        if not isinstance(tid, str) or not tid:
            raise OpError("registrar_tarea requiere id str")
        if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
            raise OpError("registrar_tarea requiere depends_on lista de str")
        import tareas as T
        try:
            clave = T.registrar(self.memoria, tid, descripcion=descripcion, depends_on=depends_on,
                                session_id=session_id)
        except ValueError as e:
            return {"ok": False, "detalle": f"registrar_tarea rechazado: {e}"}
        return {"ok": True, "clave": clave}

    def _op_tomar_tarea(self, op, session_id=None):
        """Claim atómico de una tarea READY. `session_id` lo inyecta el MONITOR (transport), NO lo
        declara el agente (P0 #3 multiagente). Un solo ganador por tarea (single-writer)."""
        tid = op.get("id")
        if not isinstance(tid, str) or not tid:
            raise OpError("tomar_tarea requiere id str")
        import tareas as T
        tomada = T.tomar(self.memoria, tid, session_id)
        return {"ok": tomada, "tomada": tomada,
                "detalle": "tomada" if tomada else "no tomable (no ready / en_curso / resuelta)"}

    def _op_resolver_tarea(self, op, session_id=None):
        tid = op.get("id")
        resultado_ref = op.get("resultado_ref")
        evidencia_hash = op.get("evidencia_hash")
        if not isinstance(tid, str) or not tid:
            raise OpError("resolver_tarea requiere id str")
        import tareas as T
        resuelta = T.resolver(self.memoria, tid, session_id, resultado_ref, evidencia_hash)
        return {"ok": True, "resuelta": resuelta,
                "detalle": "resuelta" if resuelta else "no es dueño / no en_curso / sin evidencia"}

    def _op_fallar_tarea(self, op, session_id=None):
        tid = op.get("id")
        motivo = op.get("motivo", "")
        if not isinstance(tid, str) or not tid:
            raise OpError("fallar_tarea requiere id str")
        import tareas as T
        fallida = T.fallar(self.memoria, tid, session_id, motivo=motivo)
        return {"ok": True, "fallida": fallida,
                "detalle": "fallida" if fallida else "no es dueño / no en_curso / inexistente"}

    def _op_tareas_ready(self, op, session_id=None):
        import tareas as T
        return {"ok": True, "ready": T.listas_ready(self.memoria)}

    # ── incidentes de escape (incidentes.py) ──────────────────────────────
    # `revisar`/`parchear`/`rehabilitar` son del HUMANO (el monitor inyecta su session_id;
    # en producción la re-habilitación exige autorización humana, igual que el resto).
    def _op_registrar_incidente(self, op, session_id=None):
        iid = op.get("id")
        modo_escape = op.get("modo_escape")
        vector = op.get("vector", "desconocido")
        evidencia = op.get("evidencia", {})
        if not isinstance(iid, str) or not iid or not isinstance(modo_escape, str) or not modo_escape:
            raise OpError("registrar_incidente requiere id y modo_escape str")
        import incidentes as INC
        try:
            clave = INC.registrar(self.memoria, iid, modo_escape, vector, session_id,
                                  evidencia=evidencia)
        except ValueError as e:
            return {"ok": False, "detalle": f"registrar_incidente rechazado: {e}"}
        return {"ok": True, "clave": clave}

    def _op_bloquear_incidente(self, op, session_id=None):
        iid = op.get("id")
        motivo = op.get("motivo", "")
        if not isinstance(iid, str) or not iid:
            raise OpError("bloquear_incidente requiere id str")
        import incidentes as INC
        return {"ok": INC.bloquear(self.memoria, iid, session_id, motivo=motivo)}

    def _op_revisar_incidente(self, op, session_id=None):
        iid = op.get("id")
        detalle = op.get("detalle", "")
        if not isinstance(iid, str) or not iid:
            raise OpError("revisar_incidente requiere id str")
        import incidentes as INC
        return {"ok": INC.revisar(self.memoria, iid, session_id, detalle=detalle)}

    def _op_parchear_incidente(self, op, session_id=None):
        iid = op.get("id")
        mitigacion = op.get("mitigacion", "")
        if not isinstance(iid, str) or not iid:
            raise OpError("parchear_incidente requiere id str")
        import incidentes as INC
        return {"ok": INC.parchear(self.memoria, iid, session_id, mitigacion)}

    def _op_rehabilitar_incidente(self, op, session_id=None):
        iid = op.get("id")
        if not isinstance(iid, str) or not iid:
            raise OpError("rehabilitar_incidente requiere id str")
        import incidentes as INC
        return {"ok": INC.rehabilitar(self.memoria, iid, session_id)}

    def _op_eliminar_incidente(self, op, session_id=None):
        iid = op.get("id")
        motivo = op.get("motivo", "")
        if not isinstance(iid, str) or not iid:
            raise OpError("eliminar_incidente requiere id str")
        import incidentes as INC
        return {"ok": INC.eliminar(self.memoria, iid, session_id or "humano", motivo=motivo)}

    def _op_cuarentenar_incidente(self, op, session_id=None):
        iid = op.get("id")
        motivo = op.get("motivo", "")
        if not isinstance(iid, str) or not iid:
            raise OpError("cuarentenar_incidente requiere id str")
        import incidentes as INC
        return {"ok": INC.cuarentenar(self.memoria, iid, session_id or "humano", motivo=motivo)}

    # ── acción gobernada (quorum para acciones que degradan la seguridad) ──────
    def _ejecutar_accion_gobernada(self, tipo_accion, objetivo, autor, payload=None):
        """Ejecuta el DOMINIO de una acción gobernada al alcanzar el quorum. Mapea el tipo a la
        transición correspondiente (incidentes/herramientas). Devuelve {ok, ...}."""
        payload = payload or {}
        if tipo_accion == "rehabilitar_incidente":
            import incidentes as INC
            return {"ok": INC.rehabilitar(self.memoria, objetivo, autor)}
        if tipo_accion == "eliminar_incidente":
            import incidentes as INC
            return {"ok": INC.eliminar(self.memoria, objetivo, autor,
                                       motivo=payload.get("motivo", ""))}
        if tipo_accion == "parchear_rehabilitar_incidente":
            import incidentes as INC
            mitigacion = payload.get("mitigacion", "mitigado por quorum")
            r1 = INC.revisar(self.memoria, objetivo, autor, detalle="revisado por quorum")
            r2 = INC.parchear(self.memoria, objetivo, autor, mitigacion) if r1 else False
            r3 = INC.rehabilitar(self.memoria, objetivo, autor) if r2 else False
            return {"ok": bool(r3)}
        if tipo_accion == "aprobar_instalacion_herramienta":
            import herramientas as HR
            return {"ok": HR.aprobar_instalacion(self.memoria, objetivo, autor)}
        if tipo_accion == "aprobar_cableado_herramienta":
            import herramientas as HR
            return {"ok": HR.aprobar_cableado(self.memoria, objetivo, autor)}
        return {"ok": False, "detalle": f"acción gobernada no ejecutable: {tipo_accion}"}

    def _op_proponer_accion_gobernada(self, op, session_id=None):
        tipo_accion = op.get("tipo_accion")
        objetivo = op.get("objetivo")
        autor = op.get("autor") or session_id or "humano"
        motivo = op.get("motivo", "")
        payload = op.get("payload") or {}
        if not isinstance(tipo_accion, str) or not tipo_accion:
            raise OpError("proponer_accion_gobernada requiere tipo_accion str")
        if not isinstance(objetivo, str) or not objetivo:
            raise OpError("proponer_accion_gobernada requiere objetivo str")
        try:
            r = self.memoria.proponer_accion_gobernada(
                tipo_accion, objetivo, autor=autor, motivo=motivo, payload=payload)
        except ValueError as e:
            return {"ok": False, "detalle": str(e)}
        return {"ok": True, **r}

    def _op_votar_accion_gobernada(self, op, session_id=None):
        hash_propuesta = op.get("hash_propuesta")
        autor = op.get("autor") or session_id or "humano"
        firma_hex = op.get("firma_hex")
        ts_exp = op.get("ts_exp")
        if not isinstance(hash_propuesta, str) or not hash_propuesta:
            raise OpError("votar_accion_gobernada requiere hash_propuesta str")
        try:
            r = self.memoria.votar_accion_gobernada(
                hash_propuesta, autor=autor, firma_hex=firma_hex, ts_exp=ts_exp)
        except ValueError as e:
            return {"ok": False, "detalle": str(e)}
        if r.get("ejecutable"):
            resultado = self._ejecutar_accion_gobernada(
                r.get("tipo_accion"), r.get("objetivo"), autor, r.get("payload") or {})
            self.memoria.marcar_accion_gobernada_ejecutada(
                hash_propuesta, autor=autor, resultado=resultado)
            r["ejecutada"] = True
            r["resultado"] = resultado
        return {"ok": True, **r}

    def _op_listar_acciones_gobernadas(self, op, session_id=None):
        return {"ok": True, "pendientes": self.memoria.acciones_gobernadas_pendientes()}

    # ── borrado/restauración auditada de hechos (memoria) ────────────────────
    # El borrado es una ACCIÓN HUMANA multi-firma (gobernanza): un usuario PROPONE (motivo
    # obligatorio), N validadores DISTINTOS aprueban, y al quorum se ejecuta el tombstone.
    # El autor SIEMPRE sale de la sesión autenticada del dashboard (no del body). Todo queda
    # inmutable en la hash chain (propuesta, voto, ejecución).
    def _op_proponer_revocacion_hecho(self, op, session_id=None):
        key = op.get("key")
        autor = op.get("autor") or session_id or "humano"
        motivo = op.get("motivo", "")
        categoria = op.get("categoria", "otro")
        if not isinstance(key, str) or not key:
            raise OpError("proponer_revocacion_hecho requiere key str")
        if not isinstance(motivo, str) or not motivo.strip():
            raise OpError("proponer_revocacion_hecho requiere motivo (por qué se borra)")
        try:
            r = self.memoria.proponer_revocacion(key, autor=autor, motivo=motivo,
                                                 categoria=categoria)
        except ValueError as e:
            return {"ok": False, "detalle": str(e)}
        return {"ok": True, **r}

    def _op_votar_revocacion_hecho(self, op, session_id=None):
        key = op.get("key")
        autor = op.get("autor") or session_id or "humano"
        firma_hex = op.get("firma_hex")
        ts_exp = op.get("ts_exp")
        if not isinstance(key, str) or not key:
            raise OpError("votar_revocacion_hecho requiere key str")
        try:
            r = self.memoria.votar_revocacion(key, autor=autor, firma_hex=firma_hex,
                                              ts_exp=ts_exp)
        except ValueError as e:
            return {"ok": False, "detalle": str(e)}
        return {"ok": True, **r}

    def _op_registrar_pubkey_hecho(self, op, session_id=None):
        """HUMANO (admin): registra la clave pública Ed25519 de un validador (P0 A5)."""
        usuario = op.get("usuario")
        pubkey_hex = op.get("pubkey_hex")
        autor = op.get("autor") or session_id or "admin"
        if not isinstance(usuario, str) or not usuario:
            raise OpError("registrar_pubkey_hecho requiere usuario str")
        if not isinstance(pubkey_hex, str) or not pubkey_hex:
            raise OpError("registrar_pubkey_hecho requiere pubkey_hex str")
        try:
            version = self.memoria.registrar_pubkey(usuario, pubkey_hex, autor=autor)
        except ValueError as e:
            return {"ok": False, "detalle": str(e)}
        return {"ok": True, "usuario": usuario, "version": version}

    def _op_listar_pubkeys_hecho(self, op, session_id=None):
        return {"ok": True, "pubkeys": self.memoria.pubkeys_registradas()}

    def _op_listar_revocaciones_hecho(self, op, session_id=None):
        return {"ok": True, "pendientes": self.memoria.revocaciones_pendientes()}

    def _op_set_quorum_revocacion_hecho(self, op, session_id=None):
        n = op.get("quorum")
        autor = op.get("autor") or session_id or "humano"
        try:
            n = self.memoria.set_quorum_revocacion(n, autor=autor)
        except ValueError as e:
            return {"ok": False, "detalle": str(e)}
        return {"ok": True, "quorum": n}

    def _op_restaurar_hecho(self, op, session_id=None):
        key = op.get("key")
        autor = op.get("autor") or session_id or "humano"
        motivo = op.get("motivo", "")
        if not isinstance(key, str) or not key:
            raise OpError("restaurar_hecho requiere key str")
        return {"ok": self.memoria.restaurar(key, autor=autor, motivo=motivo)}

    def _op_incidentes_activos(self, op, session_id=None):
        import incidentes as INC
        return {"ok": True, "incidentes": INC.incidentes_activos(self.memoria)}

    # ── vigilancia gVisor (vigilancia_gvisor.py) ───────────────────────────
    def _op_procesar_log_gvisor(self, op, session_id=None):
        agent_id = op.get("agent_id")
        lineas = op.get("lineas")
        if not isinstance(lineas, list):
            raise OpError("procesar_log_gvisor requiere lineas lista")
        import vigilancia_gvisor as VG
        return {"ok": True, "incidentes": VG.procesar_log(self.memoria, session_id,
                                                          agent_id, lineas)}

    # ── señal fuera de banda auditd (observador_audit.py) ───────────────────
    def _op_procesar_log_audit(self, op, session_id=None):
        """Procesa líneas de auditd (avc: denied / syscall success=no) → incidentes.

        Canal del watcher del HOST (observador_kernel --clasificador audit): la señal llega de
        una fuente FUERA del sandbox (auditd), independiente del Sentry/VMM. Igual que el log de
        gVisor, el agente puede intentar inyectar líneas falsas, pero el incidente resultante es
        create-only e idempotente (auto-bloqueo, nunca una acción privilegiada)."""
        agent_id = op.get("agent_id")
        lineas = op.get("lineas")
        if not isinstance(lineas, list):
            raise OpError("procesar_log_audit requiere lineas lista")
        import observador_audit as OA
        return {"ok": True, "incidentes": OA.procesar_audit(self.memoria, session_id,
                                                            agent_id, lineas)}

    # ── ciclo de vida de HERRAMIENTAS (herramientas.py) ─────────────────────
    # `solicitar_herramienta` es del AGENTE; aprobar/cablear/rechazar son del HUMANO (socket de
    # gobernanza). Solo `cableada` es consumible (el gate se aplica en conectores.derivar).
    def _op_solicitar_herramienta(self, op, session_id=None):
        tool_id = op.get("tool_id")
        origen = op.get("origen", "repo_privado")
        motivo = op.get("motivo", "")
        if not isinstance(tool_id, str) or not tool_id:
            raise OpError("solicitar_herramienta requiere tool_id str")
        import herramientas as HR
        try:
            clave = HR.solicitar(self.memoria, tool_id, origen=origen, motivo=motivo,
                                 session_id=session_id)
        except ValueError as e:
            return {"ok": False, "detalle": f"solicitar_herramienta rechazado: {e}"}
        return {"ok": True, "clave": clave}

    def _op_aprobar_instalacion_herramienta(self, op, session_id=None):
        tool_id = op.get("tool_id")
        if not isinstance(tool_id, str) or not tool_id:
            raise OpError("aprobar_instalacion_herramienta requiere tool_id str")
        import herramientas as HR
        return {"ok": HR.aprobar_instalacion(self.memoria, tool_id, session_id or "humano")}

    def _op_marcar_instalada_herramienta(self, op, session_id=None):
        tool_id = op.get("tool_id")
        origen = op.get("origen", "repo_privado")
        hash_scan = op.get("hash_scan", "")
        if not isinstance(tool_id, str) or not tool_id:
            raise OpError("marcar_instalada_herramienta requiere tool_id str")
        import herramientas as HR
        return {"ok": HR.marcar_instalada(self.memoria, tool_id, origen, hash_scan)}

    def _op_escanear_instalar_herramienta(self, op, session_id=None):
        """HUMANO: escanea el artefacto (hash + AV) y, si limpio, marca INSTALADA.

        El hash se COMPUTA deterministamente (escaneo_seguridad), no se declara. Rechaza malware."""
        tool_id = op.get("tool_id")
        path = op.get("path")
        origen = op.get("origen", "repo_privado")
        if not isinstance(tool_id, str) or not tool_id or not isinstance(path, str) or not path:
            raise OpError("escanear_instalar_herramienta requiere tool_id y path str")
        import herramientas as HR
        return HR.escanear_y_marcar_instalada(self.memoria, tool_id, path, origen)

    def _op_aprobar_cableado_herramienta(self, op, session_id=None):
        tool_id = op.get("tool_id")
        if not isinstance(tool_id, str) or not tool_id:
            raise OpError("aprobar_cableado_herramienta requiere tool_id str")
        import herramientas as HR
        return {"ok": HR.aprobar_cableado(self.memoria, tool_id, session_id or "humano")}

    def _op_rechazar_herramienta(self, op, session_id=None):
        tool_id = op.get("tool_id")
        motivo = op.get("motivo", "")
        if not isinstance(tool_id, str) or not tool_id:
            raise OpError("rechazar_herramienta requiere tool_id str")
        import herramientas as HR
        return {"ok": HR.rechazar(self.memoria, tool_id, session_id or "humano", motivo)}

    def _op_listar_herramientas(self, op, session_id=None):
        import herramientas as HR
        return {"ok": True, "herramientas": HR.listar(self.memoria)}

    def _op_reportar_exit_agente(self, op, session_id=None):
        agent_id = op.get("agent_id")
        returncode = op.get("returncode")
        if not isinstance(returncode, int):
            raise OpError("reportar_exit_agente requiere returncode int")
        import vigilancia_gvisor as VG
        return {"ok": True, "incidentes": VG.procesar_exit(self.memoria, session_id,
                                                           agent_id, returncode)}

    def _op_pedir_egreso(self, op, session_id=None):
        # Diseño A (DISENO_RED_MCP.md): el MONITOR decide y reenvía el intent al proxy-red.
        # GATE EN LÍNEA (dictamen Fable hito 2 P0-1): el camino al cable NO puede operar sin
        # autorización/allowlist/presupuesto al mismo tiempo que la decisión. Acá:
        #   - El discriminador es el campo `tipo` del intent (ejecutivo).
        #   - v1: SOLO http entra al proxy; mcp se rechaza (hito propio, POST+body).
        #   - riesgo>=1 => exige autorización humana VIGENTE atada al hash del intent canónico,
        #     consumida ATÓMICAMENTE (nonce+CAS) ANTES de reenviar. Sin autorización => bloqueado.
        #   - v1 NO hay plantillas a riesgo 0 (no hay catálogo de plantillas construido): TODO
        #     egreso exige firma. Es lo más seguro y honesto.
        intent = op.get("intent")
        if not isinstance(intent, dict):
            raise OpError("pedir_egreso requiere intent dict")
        import intent_canonico as IC
        import conectores as C
        import gobernanza_accion as GA

        tipo = intent.get("tipo")
        if tipo == "mcp":
            return {"ok": False, "bloqueado": True,
                    "detalle": "mcp no cableado en el proxy (v1); hito propio pendiente"}
        if tipo != "http":
            return {"ok": False, "bloqueado": True, "detalle": "tipo de intent no soportado"}

        # validar canónico (rechaza campos descriptivos y url/destino) — objeto ÚNICO para
        # todo el camino: validar, puntuar riesgo y firmar operan sobre ESTE dict canónico.
        try:
            intent = IC.validar_http(intent)
        except IC.IntentInvalido as e:
            return {"ok": False, "bloqueado": True, "detalle": f"intent inválido: {e}"}

        riesgo = C.riesgo_efectivo("http", intent)
        if riesgo <= 0:
            # v1: no hay plantillas a riesgo 0; defensa en profundidad: igual exigir gate.
            riesgo = 1

        # GATE EN LÍNEA: para riesgo>=1, autorización humana vigente atada al hash del intent.
        if self.memoria is None:
            return {"ok": False, "bloqueado": True,
                    "detalle": "monitor sin memoria gobernada: egreso no autorizable"}
        # Época FRESCA en cada verificación (Fable hito 3 P1: la de __init__ puede quedar vieja
        # si se rota con el daemon vivo). epoca.epoca_actual() lee del archivo cada vez.
        try:
            import epoca as EP
            epoca_vigente = EP.epoca_actual()
        except EP.EpocaError:
            epoca_vigente = None
            # sin época vigente leyible => autorización con binding de época se rechaza (default-deny)
        autorizada = GA.autorizacion_vigente(self.memoria, "http", intent,
                                              epoca_vigente=epoca_vigente,
                                              session_id=session_id)
        if autorizada is None:
            return {"ok": False, "bloqueado": True, "riesgo": riesgo,
                    "detalle": (f"BLOQUEADO: egreso http requiere autorización humana vigente "
                                f"(riesgo {riesgo}). Sin permiso atado al intent.")}
        # consumir ANTES de reenviar (CAS atómico; un solo uso, no ventana de doble gasto).
        # La sesión entra como PREDICADO del mismo CAS (Fable: intento desde sesión ajena no consume).
        consumida = GA.marcar_usada(self.memoria, autorizada, session_id=session_id)
        if not consumida:
            return {"ok": False, "bloqueado": True,
                    "detalle": "la autorización ya fue consumida (replay rechazado)"}

        # reenviar al proxy (cliente del monitor, no del agente). Se le pasan nonce/época/hash de
        # la autorización consumida (campos SEPARADOS del intent) para que el proxy registre el
        # egreso REAL en su ledger externo (P0 observabilidad).
        _auth = self.memoria.current(autorizada)
        _val = (_auth.get("value") or {}) if _auth else {}
        import proxy_cliente as PC
        return PC.pedir_egreso(intent, nonce=_val.get("nonce"),
                               epoca=_val.get("epoca"),
                               hash_accion=_val.get("hash_accion"))


def pedir(memoria, op, registro_nonces=None, epoca_actual=0):
    """Helper funcional: crea la fachada y despacha una única operación."""
    return MonitorFachada(memoria, registro_nonces, epoca_actual).pedir(op)


if __name__ == "__main__":
    print("monitor_fachada.py cargado (fachada in-process de pedir(op), Fase 2 paso 1)")
