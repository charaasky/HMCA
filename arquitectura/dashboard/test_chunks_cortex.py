#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test del córtex mapeado en chunks (libros/documentos largos) y del flujo 'traeme Juan 3:16'.

Cubre los TRES modos de recuperación de una PORCIÓN (no el libro entero):
  1) por CONTENIDO: buscar_chunks(query) por FTS.
  2) por POSICIÓN: buscar_chunk_posicion(page, para).
  3) por REFERENCIA: buscar_chunk_por_ref / buscar_chunk_por_cap_vers + cap_vers_en_query.
Y que el chat devuelve el versículo determinista (cortex_ref) sin llamar al LLM.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import memory_context as MC  # noqa: E402
import llm_gateway as LG  # noqa: E402


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)
    MC.MEMORY_DB = db
    LG._conn = lambda: ("https://openrouter.ai/api/v1", "test-key", "openrouter")
    LG._forward_openai = lambda *a, **k: {"choices": [{"message": {"role": "assistant", "content": "LLM"}}], "usage": {}}

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    # chunks del córtex (como los escribe _op_escribir_chunk: fuente cortex, unverified, meta.kind)
    m.write("chunk:abc123:0", "La gravedad es la curvatura del espaciotiempo",
            fuente="cortex", verified=False, meta={"kind": "chunk", "n": 0, "page": 1, "para": 1})
    m.write("chunk:abc123:1", "El tiempo de carga es capacidad dividido corriente",
            fuente="cortex", verified=False, meta={"kind": "chunk", "n": 1, "page": 1, "para": 2})
    m.write("chunk:abc123:2", "La ley de Ohm relaciona voltaje corriente y resistencia",
            fuente="cortex", verified=False, meta={"kind": "chunk", "n": 2, "page": 2, "para": 1})
    m.write("chunk:abc123:3", "Porque de tal manera amó Dios al mundo que dio a su Hijo unigénito",
            fuente="cortex", verified=False,
            meta={"kind": "chunk", "n": 3, "page": 3, "para": 1, "ref": "Juan 3:16"})
    m.write("perra:color", "negra", fuente="usuario", verified=True)

    # 1) recuperación por CONTENIDO
    chunks = m.buscar_chunks("gravedad curvatura", top_k=3)
    check("buscar_chunks por contenido (gravedad)",
          chunks and chunks[0]["key"] == "chunk:abc123:0",
          f"top={chunks[0]['key'] if chunks else None}")

    # 2) recuperación por POSICIÓN (página + párrafo)
    c = m.buscar_chunk_posicion(page=2, para=1)
    check("buscar_chunk_posicion (página 2, párrafo 1)",
          c is not None and c["key"] == "chunk:abc123:2" and "Ohm" in c["value"],
          f"key={c['key'] if c else None}")

    # 3) recuperación por REFERENCIA
    cr = m.buscar_chunk_por_ref("Juan 3:16")
    check("buscar_chunk_por_ref (Juan 3:16)",
          cr is not None and cr["key"] == "chunk:abc123:3" and "Hijo" in cr["value"],
          f"key={cr['key'] if cr else None}")
    check("cap_vers_en_query extrae 3:16 de 'traeme Juan 3:16'",
          MC.cap_vers_en_query("traeme Juan 3:16") == "3:16",
          repr(MC.cap_vers_en_query("traeme Juan 3:16")))
    check("cap_vers_en_query: '23:16' no confunde con '3:16'",
          MC.cap_vers_en_query("traeme 23:16") == "23:16",
          repr(MC.cap_vers_en_query("traeme 23:16")))
    cvc = m.buscar_chunk_por_cap_vers("3:16")
    check("buscar_chunk_por_cap_vers (3:16) resuelve Juan 3:16",
          cvc is not None and cvc["key"] == "chunk:abc123:3", f"key={cvc['key'] if cvc else None}")
    check("buscar_chunk_por_cap_vers (23:16) miss", m.buscar_chunk_por_cap_vers("23:16") is None)

    # 4) chunks NO contaminan el recall del hipocampo
    claims = m.buscar_claimed("gravedad", top_k=5)
    check("chunk excluido de buscar_claimed", all(f["key"] != "chunk:abc123:0" for f in claims),
          f"keys={[f['key'] for f in claims]}")

    # 5) chat: flujo 'traeme Juan 3:16' -> cortex_ref determinista (sin LLM)
    r = LG.chat_completions({"model": "test-model", "usuario": "admin",
                             "messages": [{"role": "user", "content": "traeme Juan 3:16"}],
                             "hcma_memory": True})
    content = ((r.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    check("chat: cortex_ref determinista (sin LLM)",
          r.get("procedencia") == "cortex_ref" and r.get("llm_llamado") is False
          and "Hijo" in content and "Juan 3:16" in content,
          f"proc={r.get('procedencia')} content={content!r}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ chunks córtex + flujo ref OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
