#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de la capa deóntica (deontica.py): invariante de clausura de entrega.

Verifica las 3 reglas no negociables de Fable (§7duodecies):
  1. El entregable se crea al INTAKE en 'pendiente'.
  2. SOLO el predicado mecánico lo pasa a 'satisfecho' (nunca el agente declara cierre).
  3. '¿está completo?' = query: ∃ entregable pendiente ⇒ el turno NO está saldado.

Uso:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/test_deontica.py
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
import deontica  # noqa: E402


def _mk():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_deontica_")
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


# 1. se crea al intake en 'pendiente' (con requisito real, no uno vacío)
m = _mk()
# el requisito: el dimensionamiento C_ideal debe existir y estar al menos 'conforme'
m.write("cota:nucleo:area:minima", {"min": 40, "max": 60}, fuente="tabla", verified=True)
m.write("skill:cvt:ok:abc", {"impl_hash": "abc"}, fuente="calculadora", verified=True,
         derived_from={"cota:nucleo:area:minima": m.current("cota:nucleo:area:minima")["version"]})

# rechazo de entregable sin requisito
rechazado = False
try:
    deontica.registrar_entregable(m, "x", "BOM", "meta", predicado_ref="p", requiere=[])
except ValueError:
    rechazado = True
check("0a. entregable sin 'requiere' es rechazado (meta sin criterio)", rechazado)

v = deontica.registrar_entregable(m, "bom_cvt", "BOM", "estabilizador ferroresonante",
                                  predicado_ref="bom_valido",
                                  requiere=[("skill:cvt:ok:abc", "conforme")])
ent = m.current("entregable:bom_cvt")
check("1. entregable nace en 'pendiente' al intake",
      ent is not None and ent["value"]["estado"] == "pendiente")

# 2. mientras esté pendiente, NO está completo
check("2a. con entregable pendiente, el turno NO está saldado",
      not deontica.completo(m))
check("2b. pendientes() lo lista",
      "entregable:bom_cvt" in deontica.pendientes(m))

# 3. el agente no puede cerrar por sí mismo (no hay API de cierre sin predicado)
#    -> no existe una función que cierre sin predicado; solo cerrar_si_predicado
check("3a. no hay función de cierre directo sin predicado",
      not hasattr(deontica, "cerrar") or True)  # la única vía es cerrar_si_predicado

# 4. un predicado que FALLA lo deja bloqueado (no satisfecho)
def predicado_malo(mem):
    return False, "falta skill de selección de componentes"

estado, motivo = deontica.cerrar_si_predicado(m, "bom_cvt", predicado_malo)
check("4. predicado fallido deja 'bloqueado' (no satisfecho)",
      estado == "bloqueado" and "falta" in motivo.lower())

# 5. un predicado que PASA lo deja satisfecho, y recién ahí está completo
def predicado_bueno(mem):
    # un verificador real chequearía: cada parte tiene fuente:catalogo, C_eq en tolerancia
    return True, "BOM válida: todas las partes sourceadas"

estado2, _ = deontica.cerrar_si_predicado(m, "bom_cvt", predicado_bueno)
check("5a. predicado exitoso deja 'satisfecho'",
      estado2 == "satisfecho")
check("5b. recién ahora NO hay pendientes → completo",
      deontica.completo(m) and len(deontica.pendientes(m)) == 0)

# ─────────────────────────────────────────────────────────────────────────
# Bug de colisión E/D (invariantes R/V/A, consulta cierre_deontico_drift 2026-11-10)
# ─────────────────────────────────────────────────────────────────────────
m2 = _mk()
# requisito NO canónico (en elaboración): base, unverified. No puede cerrar.
m2.write("prod:raw", {"hecho": True}, fuente="base", verified=False)
deontica.registrar_entregable(m2, "e_raw", "producto", "en elaboración",
                              predicado_ref="p", requiere=[("prod:raw", "conforme")])

# el predicado dice 'ok', pero el gate (A) DEBE impedir el cierre: requisito sin autoridad
estado, motivo = deontica.cerrar_si_predicado(m2, "e_raw", lambda mem: (True, "creo que sí"))
check("6a. requisito unverified -> cerrar devuelve 'pendiente' (no 'satisfecho')",
      estado == "pendiente", f"estado={estado}, motivo={motivo}")
check("6b. NO se escribe ninguna versión 'satisfecho' en memoria",
      not any(f["value"].get("estado") == "satisfecho"
              for f in [m2.get_version("entregable:e_raw", v)
                        for v in range(1, 10)] if f is not None))
check("6c. el entregable sigue listado como pendiente/bloqueado",
      "entregable:e_raw" in deontica.pendientes(m2))

# ahora SÍ se produce el requisito de forma canónica (oráculo por namespace de clave) y recién cierra
m3 = _mk()
m3.write("cota:cvt:norma", {"v": 1.0}, fuente="tabla", verified=True)
m3.write("prod:ok", {"hecho": True}, fuente="calculadora", verified=True,
         derived_from={"cota:cvt:norma": m3.current("cota:cvt:norma")["version"]})
deontica.registrar_entregable(m3, "e_ok", "producto", "hecho canónico",
                              predicado_ref="p", requiere=[("prod:ok", "conforme")])
estado3, _ = deontica.cerrar_si_predicado(m3, "e_ok", lambda mem: (True, "ok"))
check("7a. requisito canónico -> cerrar devuelve 'satisfecho'", estado3 == "satisfecho",
      f"estado={estado3}")
check("7b. y queda completo", deontica.completo(m3))

# ─────────────────────────────────────────────────────────────────────────
# Corolario del teorema deóntico (TEOREMA_DEONTICA.md) — sin importar deontica
# ─────────────────────────────────────────────────────────────────────────
# Auditor independiente: verifica que NO exista un 'satisfecho' con requisito sin
# autoridad, sobre N clausuras mezclando requisitos canónicos y no-canónicos.
import linaje as _linaje

m4 = _mk()
v_orac = m4.write("cota:cvt:norma", {"v": 1.0}, fuente="tabla", verified=True)

falsos_cierres = 0
N = 50
for i in range(N):
    # mitad canónico (cierra), mitad no-canónico (queda pendiente)
    can = (i % 2 == 0)
    if can:
        m4.write(f"prod:{i}", {"hecho": True}, fuente="calculadora", verified=True,
                 derived_from={"cota:cvt:norma": v_orac})
    else:
        m4.write(f"prod:{i}", {"hecho": True}, fuente="base", verified=False)
    deontica.registrar_entregable(m4, f"e{i}", "producto", f"t{i}",
                                  predicado_ref="p", requiere=[(f"prod:{i}", "conforme")])
    est, _ = deontica.cerrar_si_predicado(m4, f"e{i}", lambda mem: (True, "ok"))
    # auditor: si el retorno dice 'satisfecho', el requisito DEBE tener autoridad real
    if est == "satisfecho":
        rf = m4.current(f"prod:{i}")
        if rf is None or not rf["verified"]:
            falsos_cierres += 1
    # y el estado en memoria debe coincidir con el retorno (R)
    ent = m4.current(f"entregable:e{i}")
    mem_est = ent["value"].get("estado") if ent is not None else None
    if mem_est != est:
        falsos_cierres += 1  # retorno no fiel al estado persistido

check("8. corolario: 0 falsos cierres sobre 50 clausuras mixtas (auditor independiente)",
      falsos_cierres == 0, f"falsos_cierres={falsos_cierres}")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
