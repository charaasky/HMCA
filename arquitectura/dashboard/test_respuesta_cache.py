#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de regresión: cache de respuestas advisory del chat (reutilizar, no re-consultar al LLM).

Dictamen Fable 5.2 (fable_respuesta_cache_fable.md):
  1) normalizar/key_for deterministas; alcance POR USUARIO.
  2) ctx_fp/model_fp: huella de contexto y modelo (miss si cambia).
  3) elegible: solo turnos autocontenidos (sin historial/adjuntos/comandos), respuestas
     terminadas, no negativas, sin deixis, con tope de tamaño.
  4) `escribir_respuesta` (op del monitor): resp:* queda CLAIMED 0/0, fuente llm:..., meta
     respuesta, idempotente, rechaza derived_from que cite resp:*.
  5) buscar(): hit solo con ctx_fp y model_fp iguales; stale => miss.
  6) resp:* NO aparece en buscar/buscar_claimed (exclusión del recall).
  7) trigger DB: resp:* nunca puede ser verified/authority.
  8) gateway: hit reutiliza (llm_llamado=False, procedencia llm_unverified_cached); miss emite
     cache_candidate para encolar.
"""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import monitor_fachada as MF  # noqa: E402
import hcma_respcache as RC  # noqa: E402
import memory_context as MC  # noqa: E402
import llm_gateway as LG  # noqa: E402


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    fachada = MF.MonitorFachada(m)
    MC.MEMORY_DB = db
    # Aislar el gateway: no tocar el panel real ni la red.
    LG._conn = lambda: ("https://openrouter.ai/api/v1", "test-key", "openrouter")
    LG._forward_openai = lambda *a, **k: {"choices": [{"message": {"role": "assistant", "content": "RESPUESTA_LLM"}}], "usage": {}}
    LG._forward_anthropic = lambda *a, **k: {"content": [{"type": "text", "text": "RESPUESTA_LLM"}], "usage": {}}
    LG._forward_google = lambda *a, **k: {"candidates": [{"content": {"parts": [{"text": "RESPUESTA_LLM"}]}}], "usageMetadata": {}}

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    # 1) normalización + key determinista (NFKC preserva acentos: 'qué' != 'que')
    a = RC.normalizar("¿Qué color era mi perra?")
    b = RC.normalizar("¿QUÉ color   era mi perra!!")
    check("normalizar colapsa puntuación/case/espacios (preserva acentos)",
          a == b and a == "qué color era mi perra", f"a={a!r}")
    k1 = RC.key_for("admin", a)
    k2 = RC.key_for("admin", b)
    k3 = RC.key_for("otro", a)
    check("key_for determinista y por-usuario",
          k1 == k2 and k1.startswith("resp:admin:") and k1 != k3, f"k1={k1}")

    # 2) ctx_fp y model_fp
    hechos = [{"key": "perra:color", "version": 7}, {"key": "perra:nombre", "version": 3}]
    hechos_rev = [{"key": "perra:nombre", "version": 3}, {"key": "perra:color", "version": 7}]
    check("ctx_fp orden-independiente y no vacío",
          RC.ctx_fp(hechos) == RC.ctx_fp(hechos_rev) and RC.ctx_fp(hechos) != "" and RC.ctx_fp([]) == "")
    check("model_fp depende de provider+modelo",
          RC.model_fp("openrouter", "a") == RC.model_fp("openrouter", "a")
          and RC.model_fp("openrouter", "a") != RC.model_fp("openrouter", "b")
          and RC.model_fp("openrouter", "a") != RC.model_fp("google", "a"))

    # 3) elegible
    base_body = {"messages": [{"role": "user", "content": "que es la entropia"}]}
    resp_ok = {"choices": [{"message": {"role": "assistant", "content": "la entropia mide el desorden"},
                            "finish_reason": "stop"}]}
    e, r = RC.elegible(base_body, resp_ok, "que es la entropia")
    check("elegible: turno autocontenido OK", e, r)
    e2, _ = RC.elegible({"messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"},
                                      {"role": "user", "content": "z"}]}, resp_ok, "z")
    check("elegible: rechaza historial multi-turno", not e2)
    e3, _ = RC.elegible({"messages": [{"role": "user", "content": "que es x"}],
                         "attachments": [{"filename": "a.pdf", "text": "..."}]}, resp_ok, "que es x")
    check("elegible: rechaza adjuntos", not e3)
    resp_neg = {"choices": [{"message": {"role": "assistant", "content": "No sé, no lo tengo"},
                             "finish_reason": "stop"}]}
    e4, _ = RC.elegible(base_body, resp_neg, "que es la entropia")
    check("elegible: rechaza respuesta negativa", not e4)
    e5, _ = RC.elegible(base_body, resp_ok, "cuanto vale hoy")
    check("elegible: rechaza deixis temporal", not e5)

    # 4) op del monitor: resp:* queda claimed 0/0, idempotente
    norm_q = RC.normalizar("que es la entropia")
    key = RC.key_for("admin", norm_q)
    r_op = fachada.pedir({"tipo": "escribir_respuesta", "key": key, "value": "la entropia mide el desorden",
                          "usuario": "admin", "modelo": "test-model", "ctx_fp": "fp1", "model_fp": "mfp1",
                          "ts": "2026-11-24T00:00:00Z", "derived_from": {}})
    fact = m.current_raw(key)
    check("escribir_respuesta escribe claim 0/0 llm:*",
          r_op.get("ok") and fact["verified"] is False and fact["authority"] is False
          and fact["fuente"].startswith("llm:") and fact["meta"].get("tipo") == "respuesta",
          f"ok={r_op.get('ok')} fuente={fact['fuente']} meta={fact['meta']}")
    filas = m.conn.execute("SELECT COUNT(*) FROM facts WHERE key=?", (key,)).fetchone()[0]
    r_op2 = fachada.pedir({"tipo": "escribir_respuesta", "key": key, "value": "la entropia mide el desorden",
                           "usuario": "admin", "modelo": "test-model", "ctx_fp": "fp1", "model_fp": "mfp1",
                           "ts": "2026-11-24T00:00:00Z", "derived_from": {}})
    filas2 = m.conn.execute("SELECT COUNT(*) FROM facts WHERE key=?", (key,)).fetchone()[0]
    check("escribir_respuesta idempotente (mismo valor no reescribe)",
          r_op2.get("ok") and r_op2.get("version") == r_op.get("version") and filas2 == filas,
          f"v1={r_op.get('version')} v2={r_op2.get('version')} filas={filas}->{filas2}")
    r_bad = fachada.pedir({"tipo": "escribir_respuesta", "key": key, "value": "x", "usuario": "admin",
                           "modelo": "m", "ctx_fp": "", "model_fp": "", "ts": "",
                           "derived_from": {"resp:otro:x": 1}})
    check("escribir_respuesta rechaza derived_from que cite resp:*", r_bad.get("ok") is False)

    # 5) buscar(): hit con ctx_fp/model_fp, miss si difieren o stale
    hit = RC.buscar("admin", norm_q, m, "fp1", "mfp1")
    check("buscar hit", hit is not None and hit["value"] == "la entropia mide el desorden",
          f"hit={hit['value'] if hit else None}")
    check("buscar miss por ctx_fp", RC.buscar("admin", norm_q, m, "fp2", "mfp1") is None)
    check("buscar miss por model_fp", RC.buscar("admin", norm_q, m, "fp1", "mfp2") is None)
    check("buscar miss por otro usuario", RC.buscar("otro", norm_q, m, "fp1", "mfp1") is None)

    # 6) exclusión de resp:* en recall
    m.write("resp:admin:zzz", "contenido con palabra perra", fuente="llm:test-model", verified=False,
            meta={"tipo": "respuesta", "ctx_fp": "", "model_fp": ""})
    claims = m.buscar_claimed("palabra perra", top_k=10)
    buscados = m.buscar("palabra perra", top_k=10)
    check("resp:* excluido de buscar_claimed", all(f["key"] != "resp:admin:zzz" for f in claims))
    check("resp:* excluido de buscar", all(f["key"] != "resp:admin:zzz" for f in buscados))

    # 7) trigger: resp:* nunca verified/authority
    try:
        m.conn.execute(
            "INSERT INTO facts(version,key,value,unit,verified,authority,stale,fuente,meta,derived_from) "
            "VALUES(999998,'resp:admin:bad','\"x\"','',1,1,0,'llm:x','{}','{}')")
        m.conn.commit()
        check("trigger: INSERT resp:* verified=1 aborta", False)
    except sqlite3.IntegrityError:
        m.conn.rollback()
        check("trigger: INSERT resp:* verified=1 aborta", True)

    # 8) gateway: cache hit reutiliza (sin LLM). Query fresca para no chocar con la key de (4).
    q = "que es la gravedad"
    m_fp = RC.model_fp("openrouter", "test-model")
    fachada.pedir({"tipo": "escribir_respuesta", "key": RC.key_for("admin", RC.normalizar(q)),
                   "value": "la gravedad es la curvatura del espaciotiempo", "usuario": "admin",
                   "modelo": "test-model", "ctx_fp": RC.ctx_fp([]), "model_fp": m_fp,
                   "ts": "2026-11-24T00:00:00Z", "derived_from": {}})
    r_hit = LG.chat_completions({"model": "test-model", "usuario": "admin",
                                 "messages": [{"role": "user", "content": q}], "hcma_memory": True})
    content = ((r_hit.get("choices") or [{}])[0].get("message") or {}).get("content")
    check("gateway cache hit: reutiliza sin LLM",
          r_hit.get("procedencia") == "llm_unverified_cached" and r_hit.get("llm_llamado") is False
          and content == "la gravedad es la curvatura del espaciotiempo"
          and r_hit.get("hcma_cache", {}).get("reutilizada") is True,
          f"proc={r_hit.get('procedencia')} content={content!r}")

    # 9) gateway: miss emite cache_candidate (para encolar), y llama al LLM
    r_miss = LG.chat_completions({"model": "test-model", "usuario": "admin",
                                  "messages": [{"role": "user", "content": "que es el magnetismo"}],
                                  "hcma_memory": True})
    cand = r_miss.get("cache_candidate")
    check("gateway miss: emite cache_candidate",
          r_miss.get("procedencia") == "llm_unverified" and cand is not None
          and cand["key"].startswith("resp:admin:") and cand["value"] == "RESPUESTA_LLM",
          f"proc={r_miss.get('procedencia')} key={cand and cand.get('key')}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ respuesta-cache OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
