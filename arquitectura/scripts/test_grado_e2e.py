#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de integración del grado de confianza end-to-end (cierra el fallo CVT).

Demuestra el agujero que encontramos: un test dorado SIN fuente canónica solo produce
`ok` de grado 'conforme' (verifica conformidad, NO validez). Solo con una `fuente:<fid>`
independiente el `ok` pasa a 'canonico'. Esto es I11/I15 en acción.

Uso:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps \
      python3 arquitectura/scripts/test_grado_e2e.py
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
import skill_lib  # noqa: E402


def _mk():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_grado_")
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


CODIGO = "def calcular(inputs):\n    return {'y': inputs['x'] * 2}\n"


# ── spec con test dorado SIN fuente (el caso del CVT) ────────────────────────
SPEC_SIN_FUENTE = {
    "nombre": "duplicar",
    "inputs": [{"nombre": "x", "unidad": "1"}],
    "outputs": [{"nombre": "y", "unidad": "1", "tolerancia": {"tipo": "abs", "valor": 1e-6}}],
    "tests_dorados": [
        {"id": "c1", "inputs": {"x": 3}, "esperado": {"y": 6}},  # esperado SIN fuente
    ],
}

m = _mk()
skill_lib.registrar_spec(m, "doblar", SPEC_SIN_FUENTE, fuente="ingeniero")
v_impl, h = skill_lib.proponer_impl(m, "doblar", CODIGO)
res = skill_lib.evaluar(m, "doblar", v_impl)
g = skill_lib.grado_skill(m, "doblar", h)
check("ok sin fuente -> grado 'conforme' (NO canonico)", g == "conforme", f"grado={g}")
check("pero evaluar sigue reportando todos_ok (conformidad ok)", res["todos_ok"] is True)

# ── ahora con fuente canónica independiente ─────────────────────────────────
spec_con_fuente = dict(SPEC_SIN_FUENTE)
spec_con_fuente["tests_dorados"] = [
    {"id": "c1", "inputs": {"x": 3}, "esperado": {"y": 6}, "fuente": ["fuente:tabla_dup"]},
]
m2 = _mk()
skill_lib.registrar_fuente(m2, "tabla_dup", "tabla_fabricante", "catálogo X, tabla de duplicación")
skill_lib.registrar_spec(m2, "doblar", spec_con_fuente, fuente="ingeniero")
v2, h2 = skill_lib.proponer_impl(m2, "doblar", CODIGO)
skill_lib.evaluar(m2, "doblar", v2)
g2 = skill_lib.grado_skill(m2, "doblar", h2)
check("ok con fuente:* -> grado 'canonico'", g2 == "canonico", f"grado={g2}")

# ── colapso de la fuente degrada el ok ─────────────────────────────────────
skill_lib.registrar_fuente(m2, "tabla_dup", "tabla_fabricante", "catálogo X v2 (corregido)")
g3 = skill_lib.grado_skill(m2, "doblar", h2)
check("colapso de fuente:* degrada ok de 'canonico' a 'conforme'", g3 != "canonico", f"grado={g3}")

# ── ejecutables() particiona por grado computado ────────────────────────────
ej = skill_lib.ejecutables(m)
check("ejecutables() expone grado 'conforme' computado",
      ej.get("doblar", {}).get(h, {}).get("grado") == "conforme", str(ej))

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
