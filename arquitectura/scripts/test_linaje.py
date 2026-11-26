#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de linaje.grado(): el grado se COMPUTA del linaje, no se declara (opción D).

Verifica I11/I15: un `ok` solo es 'canonico' si sus raíces incluyen `fuente:*`/`cota:*`.
Sin oráculo, computa 'conforme' aunque `verified=1`. El colapso de una `fuente:*` (stale)
debe degradar el grado.

NO toca el core. Uso:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/test_linaje.py
"""

import os
import sys
import tempfile

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import linaje  # noqa: E402


def _mk():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_lin_")
    os.close(fd)
    return HCMAMemoriaSQLite(path)


PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")
    return cond


# ── 1. grado de un ok SIN oráculo = 'conforme' (aunque verified) ─────────────
m = _mk()
v_spec = m.write("skill:x:spec", {"nombre": "x"}, fuente="ingeniero", verified=True)
v_test = m.write("test:x:aaa:c1", {"pass": True}, fuente="calculadora", verified=True,
                 derived_from={"skill:x:spec": v_spec})
v_ok = m.write("skill:x:ok:aaa", {"impl_hash": "aaa"}, fuente="calculadora", verified=True,
               derived_from={"test:x:aaa:c1": v_test})
g, razon = linaje.grado_razon(m, "skill:x:ok:aaa", v_ok)
check("ok sin oráculo -> grado 'conforme' (aunque verified=1)",
      g == "conforme", f"grado={g} razon={razon}")

# ── 2. grado con fuente:* canónica = 'canonico' ───────────────────────────────
m2 = _mk()
v_fuente = m2.write("fuente:iec60909", {"tipo": "norma", "ref": "IEC 60909"}, fuente="tabla", verified=True)
v_spec2 = m2.write("skill:y:spec", {"nombre": "y"}, fuente="ingeniero", verified=True)
v_test2 = m2.write("test:y:bbb:c1", {"pass": True}, fuente="calculadora", verified=True,
                   derived_from={"skill:y:spec": v_spec2, "fuente:iec60909": v_fuente})
v_ok2 = m2.write("skill:y:ok:bbb", {"impl_hash": "bbb"}, fuente="calculadora", verified=True,
                 derived_from={"test:y:bbb:c1": v_test2})
g2, razon2 = linaje.grado_razon(m2, "skill:y:ok:bbb", v_ok2)
check("ok con fuente:* -> grado 'canonico'", g2 == "canonico", f"grado={g2} razon={razon2}")

# ── 3. colapso de fuente:* degrada el grado ───────────────────────────────────
m2.write("fuente:iec60909", {"tipo": "norma", "ref": "IEC 60909 v2"}, fuente="tabla", verified=True)
g3 = linaje.grado(m2, "skill:y:ok:bbb", v_ok2)
check("colapso de fuente:* degrada el ok (stale propaga y grado cae)",
      g3 != "canonico", f"grado tras colapso={g3}")

# ── 4. cota:* también es raíz canónica ────────────────────────────────────────
m3 = _mk()
v_cota = m3.write("cota:magn:Bmax:lineal", {"min": 0.0, "max": 1.2}, fuente="tabla", verified=True)
v_spec3 = m3.write("skill:z:spec", {"nombre": "z"}, fuente="ingeniero", verified=True)
v_test3 = m3.write("test:z:ccc:c1", {"pass": True}, fuente="calculadora", verified=True,
                   derived_from={"skill:z:spec": v_spec3, "cota:magn:Bmax:lineal": v_cota})
v_ok3 = m3.write("skill:z:ok:ccc", {"impl_hash": "ccc"}, fuente="calculadora", verified=True,
                 derived_from={"test:z:ccc:c1": v_test3})
g4 = linaje.grado(m3, "skill:z:ok:ccc", v_ok3)
check("cota:* es raíz canónica -> grado 'canonico'", g4 == "canonico", f"grado={g4}")

# ── 5. hecho inexistente -> 'desconocido' ─────────────────────────────────────
g5 = linaje.grado(m, "skill:noexiste:ok", 999999)
check("hecho inexistente -> 'desconocido'", g5 == "desconocido", f"grado={g5}")

# ── 6. fuente:* con authority=False (evidencia) NO sube a canónico ───────────
m6 = _mk()
# fuente 'evidencia' via MCP/consulta que NO es autoridad independiente
v_fuente_ev = m6.write("fuente:evidencia", {"tipo": "texto_referencia", "ref": "consulta"},
                       fuente="base", verified=False)  # nace unverified => authority=False
def _set_authority_false(mem, k, v):
    # simulamos un conector firmado como 'evidencia' (authority=False) directamente
    # escribiendo con fuente 'ingeniero' verified pero fuerza authority False vía update
    mem.conn.execute("UPDATE facts SET authority=0 WHERE key=? AND version=?", (k, v))
    mem.conn.commit()
_set_authority_false(m6, "fuente:evidencia", v_fuente_ev)
v_spec6 = m6.write("skill:w:spec", {"nombre": "w"}, fuente="ingeniero", verified=True)
v_test6 = m6.write("test:w:ddd:c1", {"pass": True}, fuente="calculadora", verified=True,
                    derived_from={"skill:w:spec": v_spec6, "fuente:evidencia": v_fuente_ev})
v_ok6 = m6.write("skill:w:ok:ddd", {"impl_hash": "ddd"}, fuente="calculadora", verified=True,
                  derived_from={"test:w:ddd:c1": v_test6})
g6 = linaje.grado(m6, "skill:w:ok:ddd", v_ok6)
check("fuente evidencia (authority=False) NO sube a 'canonico'", g6 == "conforme", f"grado={g6}")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
