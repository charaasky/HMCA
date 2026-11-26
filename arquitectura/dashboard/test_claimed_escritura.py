#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de regresión P0-A: escritura gobernada (chat → claimed, nunca pisa verified).

Cubre el dictamen de Fable 5.2:
  1) Un write del chat entra como CLAIMED (verified=0, authority=0, fuente='chat:usuario').
  2) Nunca pisa un verified: valor distinto -> claim aparte con meta.conflicts_with.
  3) Valor igual al verified -> no se escribe (dedup, devuelve la versión verified).
  4) Un claim nuevo de la misma key marca stale=1 SOLO a claims previos.
  5) El recall distingue tier: verified (determinista) vs claimed (atribuido, HIT_CLAIMED).
  6) El gate del chat devuelve procedencia 'recall_claimed' sin llamar al LLM.
  7) Defense in depth: INSERT/UPDATE directo de una fila chat a verified/authority ABORTA.
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
import hcma_recall as R  # noqa: E402
import memory_context as MC  # noqa: E402
import llm_gateway as LG  # noqa: E402


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def _silenciar_llm():
    LG._forward_openai = lambda *a, **k: {"choices": [{"message": {"role": "assistant", "content": "RESPUESTA_LLM"}}], "usage": {}}
    LG._forward_anthropic = lambda *a, **k: {"content": [{"type": "text", "text": "RESPUESTA_LLM"}], "usage": {}}
    LG._forward_google = lambda *a, **k: {"candidates": [{"content": {"parts": [{"text": "RESPUESTA_LLM"}]}}], "usageMetadata": {}}
    LG._conn = lambda: ("https://openrouter.ai/api/v1", "test-key", "openrouter")


def _n_rows(m, key):
    return m.conn.execute("SELECT COUNT(*) FROM facts WHERE key=?", (key,)).fetchone()[0]


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    MC.MEMORY_DB = db
    _silenciar_llm()

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    # seed verified
    v_negra = m.write("perra:color", "negra", fuente="usuario", verified=True)

    # 1) claim sobre verified con valor DISTINTO -> no pisa, conflicts_with
    v_claim = m.write("perra:color", "blanca", fuente="chat:usuario", verified=False,
                      meta={"usuario": "admin", "tipo": "claimed"})
    cur = m.current("perra:color")
    check("claim distinto no pisa el verified", cur["value"] == "negra" and cur["verified"],
          f"current={cur['value']}")
    claim = m.get_version("perra:color", v_claim)
    check("claim queda 0/0 con conflicts_with", claim["verified"] is False and claim["authority"] is False
          and claim["meta"].get("conflicts_with") == v_negra and claim["fuente"] == "chat:usuario",
          f"meta={claim['meta']}")

    # 2) claim con MISMO valor que el verified -> no escribe (dedup)
    filas_antes = _n_rows(m, "perra:color")
    v_dup = m.write("perra:color", "negra", fuente="chat:usuario", verified=False,
                    meta={"usuario": "admin", "tipo": "claimed"})
    check("claim igual al verified no escribe (dedup)", v_dup == v_negra and _n_rows(m, "perra:color") == filas_antes,
          f"v={v_dup} filas={_n_rows(m, 'perra:color')}")

    # 3) claim nuevo de misma key marca stale=1 solo a claims previos (no al verified)
    v_claim2 = m.write("perra:color", "gris", fuente="chat:usuario", verified=False,
                       meta={"usuario": "admin", "tipo": "claimed"})
    claim1 = m.get_version("perra:color", v_claim)
    cur = m.current("perra:color")
    check("claim previo queda stale; verified intacto",
          claim1["stale"] is True and cur["value"] == "negra" and cur["stale"] is False,
          f"claim1.stale={claim1['stale']} cur={cur['value']}")

    # 4) recall tier: claim sin verified -> HIT_CLAIMED (atribuido, no determinista)
    v_auto_claim = m.write("auto:color", "rojo", fuente="chat:usuario", verified=False,
                           meta={"usuario": "admin", "tipo": "claimed"})
    dec = R.resolver("color de mi auto", m, top_k=5)
    check("claim -> HIT_CLAIMED tier=claimed",
          dec["tipo"] == "HIT_CLAIMED" and dec["tier"] == "claimed",
          f"tipo={dec['tipo']} tier={dec['tier']}")

    # 5) verified gana sobre claim de la misma key
    dec_v = R.resolver("color de mi perra", m, top_k=5)
    check("verified gana sobre claim", dec_v["tipo"] == "RECALL_PURO" and dec_v["tier"] == "verified",
          f"tipo={dec_v['tipo']} tier={dec_v['tier']}")

    # 6) gate del chat -> recall_claimed, sin LLM
    r = LG.chat_completions({"model": "test-model",
                             "messages": [{"role": "user", "content": "color de mi auto"}],
                             "hcma_memory": True})
    check("gate chat -> recall_claimed (sin LLM)",
          r.get("procedencia") == "recall_claimed" and r.get("llm_llamado") is False,
          f"proc={r.get('procedencia')}")

    # 7) trigger guard: INSERT directo de fila chat verified=1 aborta
    try:
        m.conn.execute(
            "INSERT INTO facts(version,key,value,unit,verified,authority,stale,fuente,meta,derived_from) "
            "VALUES(999999,'x','\"y\"','',1,1,0,'chat:x','{}','{}')")
        m.conn.commit()
        check("trigger: INSERT chat verified=1 aborta", False)
    except sqlite3.IntegrityError:
        m.conn.rollback()
        check("trigger: INSERT chat verified=1 aborta", True)
    except Exception as e:
        m.conn.rollback()
        check("trigger: INSERT chat verified=1 aborta", False, f"ex={e}")

    # 8) trigger guard: UPDATE directo de fila chat a verified aborta
    m.write("otro:hecho", "z", fuente="chat:usuario", verified=False,
            meta={"usuario": "admin", "tipo": "claimed"})
    row = m.conn.execute("SELECT version FROM facts WHERE key='otro:hecho' AND verified=0").fetchone()
    try:
        m.conn.execute("UPDATE facts SET verified=1, authority=1 WHERE version=?", (row["version"],))
        m.conn.commit()
        check("trigger: UPDATE chat -> verified aborta", False)
    except sqlite3.IntegrityError:
        m.conn.rollback()
        check("trigger: UPDATE chat -> verified aborta", True)
    except Exception as e:
        m.conn.rollback()
        check("trigger: UPDATE chat -> verified aborta", False, f"ex={e}")

    # 9) promoción humana (A2): claim -> verified nuevo, la fila claim NO se toca
    v_prom = m.promover_claim("auto:color", v_auto_claim, "admin", evidence="revisado por admin",
                              session_id="humano")
    cur_auto = m.current("auto:color")
    prom = m.get_version("auto:color", v_prom)
    claim_row = m.get_version("auto:color", v_auto_claim)
    check("promoción crea verified fuente promocion:humano",
          cur_auto["verified"] and cur_auto["fuente"] == "promocion:humano"
          and prom["derived_from"] == {"auto:color": v_auto_claim}
          and claim_row["verified"] is False and claim_row["fuente"] == "chat:usuario",
          f"fuente={cur_auto['fuente']} df={prom['derived_from']}")

    # 10) promover algo que no es claim (verified) se rechaza
    try:
        m.promover_claim("perra:color", v_negra, "admin")
        check("promover un verified se rechaza", False)
    except ValueError:
        check("promover un verified se rechaza", True)

    # 11) migración A2: chat-verified (fuente usuario + meta.usuario) -> claimed
    m.write("hija:nombre", "Rocio", fuente="usuario", verified=True, meta={"usuario": "admin"})
    m.write("fisica:constante", 9.81, unit="m/s2", fuente="usuario", verified=True)  # sin meta.usuario
    n = m.migrar_chat_verified_a_claimed()
    cur_hija = m.current("hija:nombre")
    cur_fis = m.current("fisica:constante")
    claims = m.buscar_claimed("nombre de mi hija", top_k=5)
    check("migración degrada chat-verified a claimed",
          n == 1 and cur_hija is None and cur_fis is not None and cur_fis["verified"]
          and any(f["key"] == "hija:nombre" and f.get("tier") == "claimed" for f in claims),
          f"n={n} cur_hija={cur_hija} claims={[f['key'] for f in claims]}")
    # idempotente: segunda pasada no degrada nada nuevo
    n2 = m.migrar_chat_verified_a_claimed()
    check("migración idempotente", n2 == 0, f"n2={n2}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ P0-A (escritura gobernada + promoción A2) OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
