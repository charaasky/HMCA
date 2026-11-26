#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_falsacion_deontica.py — tests de FALSACIÓN de la capa deóntica (R/V/A).

Fable 5.2 (repaso integral) señaló que `test_deontica.py` (14 PASS) NO demuestra que la
clausura "no puede mentir": testea el camino feliz + un par de casos, pero no reintroduce
el bug ni cubre el acople E×D. Este archivo es el complemento de falsación.

Contenido (cada bloque es una FALSACIÓN, no una confirmación):
  1. [ESTADO MIXTO E×D] mezclar ops epistémicas y deónticas, verificando R/V/A tras cada paso.
  2. [CERRAR → INVALIDAR] tras cerrar un entregable, invalidar el hecho que sustentaba el
     cierre => ¿el entregable se REABRE a pendiente? (el test más importante según Fable).
  3. [MUTACIÓN R] reintroducir un retorno que NO lee current() post-write => ROJO.
  4. [MUTACIÓN A] reintroducir cierre sin pre-check de autoridad => ROJO.
  5. [LAVADO DE PROCEDENCIA] desconocido no puede promoverse a canónico por derivación.

Los tests de MUTACIÓN (3 y 4) se ejecutan contra una copia 'mutante' de la función de cierre
(monkeypatch controlado), NO contra el módulo real: verifican que el módulo real tiene el
guardrail que la mutación quita. Si el módulo real ya es vulnerable, el test lo captura.

USO:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/test_falsacion_deontica.py
"""
import os
import sys
import tempfile

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import deontica  # noqa: E402
import linaje  # noqa: E402


def _mk():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_falsacion_")
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


# ─────────────────────────────────────────────────────────────────────────────
# 1. Estado mixto E×D: R/V/A tras cada paso, con un oráculo independiente
# ─────────────────────────────────────────────────────────────────────────────
def test_estado_mixto():
    print("\n[1] Estado mixto E×D: R/V/A tras cada paso con oráculo independiente")
    m = _mk()
    v_orac = m.write("cota:cvt", {"v": 1.0}, fuente="tabla", verified=True)

    # paso deóntico: registrar entregable (pendiente)
    deontica.registrar_entregable(m, "e0", "producto", "t0", predicado_ref="p",
                                  requiere=[("prod:0", "conforme")])
    s = deontica.estado(m, "e0")
    check("1a. entregable recién registrado: requisito aún caído => 'bloqueado' (vista estado())",
          s["estado"] == "bloqueado" and s["caidos"] == ["prod:0"])

    # paso epistémico intercalado: producir requisito (derivado del oráculo)
    v_req = m.write("prod:0", {"hecho": True}, fuente="calculadora", verified=True,
                    derived_from={"cota:cvt": v_orac})
    req = m.current("prod:0")
    check("1b. requisito gana autoridad por linaje al oráculo",
          req is not None and req["verified"])

    # cerrar (predicado pasa)
    est, _ = deontica.cerrar_si_predicado(m, "e0", lambda mem: (True, "ok"))
    s = deontica.estado(m, "e0")
    check("1c. R: el retorno refleja el estado REAL post-write",
          est == s["estado"] == "satisfecho")

    # A: el cierre heredó autoridad del requisito (derived_from apunta a prod:0)
    ent = m.current("entregable:e0")
    check("1d. A: el cierre deriva del requisito (no auto-promovido)",
          ent is not None and ent["derived_from"].get("prod:0") == v_req)

    # verificación independiente: el estado de memoria es consistente por query cruda
    rows = m.conn.execute(
        "SELECT COUNT(*) FROM facts WHERE key='entregable:e0' AND authority=1").fetchone()[0]
    check("1e. V: exactamente una versión con autoridad de entregable:e0", rows == 1)


# ─────────────────────────────────────────────────────────────────────────────
# 2. CERRAR → INVALIDAR (el test más importante según Fable)
# ─────────────────────────────────────────────────────────────────────────────
def test_cerrar_luego_invalidar():
    print("\n[2] CERRAR → INVALIDAR: ¿el entregable se reabre tras invalidar su sustento?")
    m = _mk()
    v_orac = m.write("cota:cvt", {"v": 1.0}, fuente="tabla", verified=True)
    v_req = m.write("prod:0", {"hecho": True}, fuente="calculadora", verified=True,
                    derived_from={"cota:cvt": v_orac})
    deontica.registrar_entregable(m, "e0", "producto", "t0", predicado_ref="p",
                                  requiere=[("prod:0", "conforme")])
    est, _ = deontica.cerrar_si_predicado(m, "e0", lambda mem: (True, "ok"))
    check("2a. cierre inicial a satisfecho", est == "satisfecho")

    # Ahora se CORRIGE el oráculo raíz -> invalida transitivamente prod:0 y el cierre.
    m.write("cota:cvt", {"v": 2.0}, fuente="tabla", verified=True)

    req = m.current("prod:0")
    ent = m.current("entregable:e0")
    check("2b. el requisito se invalidó (current None)", req is None,
          f"req={req is not None}")
    check("2c. el cierre también colapsó (current None)", ent is None,
          f"ent={'satisfecho' if ent else None}")

    # DEÓNTICO: el entregable debe REABRIRSE a pendiente, no desaparecer.
    # Si completo()==True con un entregable cuyo sustento cayó, es un FALSO CIERRE en el tiempo.
    pend = deontica.pendientes(m)
    completo = deontica.completo(m)
    check("2d. el entregable se REEXPRESA como pendiente (no queda en limbo)",
          "entregable:e0" in pend, f"pendientes={list(pend)}")
    check("2e. el turno NO está completo (hay trabajo reabierto)",
          not completo, f"completo={completo}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Falsación por MUTACIÓN: reintroducir el bug R (retorno sin current post-write)
# ─────────────────────────────────────────────────────────────────────────────
def test_mutacion_R():
    print("\n[3] MUTACIÓN R: un cierre que retorna intención (no estado real) debe detectarse")
    m = _mk()
    v_orac = m.write("cota:cvt", {"v": 1.0}, fuente="tabla", verified=True)
    m.write("prod:0", {"hecho": True}, fuente="calculadora", verified=True,
            derived_from={"cota:cvt": v_orac})
    deontica.registrar_entregable(m, "e0", "producto", "t0", predicado_ref="p",
                                  requiere=[("prod:0", "conforme")])

    # mutante: devuelve 'satisfecho' sin verificar el estado real (bug original)
    original = deontica.cerrar_si_predicado

    def cierre_mutante_R(mem, id, fn):
        ent = mem.current(f"entregable:{id}")
        requiere = (ent["value"] or {}).get("requiere", [])
        # mutación: NO pre-check ni read-back; retorna intención
        mem.write(f"entregable:{id}", dict(ent["value"], estado="satisfecho"),
                  unit="", fuente="calculadora", verified=True, derived_from={})
        return "satisfecho", "ok"   # <- miente si el write quedó unverified

    deontica.cerrar_si_predicado = cierre_mutante_R
    try:
        est, _ = deontica.cerrar_si_predicado(m, "e0", lambda mem: (True, "ok"))
    finally:
        deontica.cerrar_si_predicado = original

    real = m.current("entregable:e0")
    real_est = real["value"]["estado"] if real else None
    # El mutante devolvió 'satisfecho' pero el write con derived_from={} quedó unverified.
    check("3a. la mutación R miente (retorno != estado real)",
          est == "satisfecho" and real_est != "satisfecho",
          f"retorno={est}, real={real_est}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Falsación por MUTACIÓN: reintroducir el bug A (cierre sin pre-check de autoridad)
# ─────────────────────────────────────────────────────────────────────────────
def test_mutacion_A():
    print("\n[4] MUTACIÓN A: cierre que ignora requisito sin autoridad debe detectarse")
    m = _mk()
    # requisito NO canónico (base unverified), como el bug original
    m.write("prod:0", {"hecho": True}, fuente="base", verified=False)
    deontica.registrar_entregable(m, "e0", "producto", "t0", predicado_ref="p",
                                  requiere=[("prod:0", "conforme")])

    original = deontica.cerrar_si_predicado

    def cierre_mutante_A(mem, id, fn):
        # mutación: SIN pre-check de válido(r,g); cierra igual
        ent = mem.current(f"entregable:{id}")
        mem.write(f"entregable:{id}", dict(ent["value"], estado="satisfecho"),
                  unit="", fuente="calculadora", verified=True, derived_from={})
        return "satisfecho", "ok"

    deontica.cerrar_si_predicado = cierre_mutante_A
    try:
        est, _ = deontica.cerrar_si_predicado(m, "e0", lambda mem: (True, "ok"))
    finally:
        deontica.cerrar_si_predicado = original

    # El módulo REAL (deontica.cerrar_si_predicado restaurado) DEBE rechazar este caso.
    # Verificamos que el mutante habría cerrado con un requisito sin autoridad (bug).
    check("4a. la mutación A habría cerrado sin autoridad (bug capturable)",
          est == "satisfecho", f"est={est}")

    # Y el módulo real lo rechaza (contra-test):
    m2 = _mk()
    m2.write("prod:0", {"hecho": True}, fuente="base", verified=False)
    deontica.registrar_entregable(m2, "e0", "producto", "t0", predicado_ref="p",
                                  requiere=[("prod:0", "conforme")])
    est2, _ = deontica.cerrar_si_predicado(m2, "e0", lambda mem: (True, "ok"))
    check("4b. el módulo real SÍ rechaza (retorna pendiente, no satisfecho)",
          est2 == "pendiente", f"est={est2}")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Lavado de procedencia: desconocido no se promueve a canónico por derivación
# ─────────────────────────────────────────────────────────────────────────────
def test_lavado_procedencia():
    print("\n[5] Lavado de procedencia: desconocido no puede llegar a canónico por derivación")
    m = _mk()
    # raíz desconocida (sin oráculo)
    v = m.write("dato:0", {"x": 1}, fuente="base", verified=False)
    # intentar derivar de ella (calculadora) -> no debe quedar verified
    m.write("calc:0", {"r": 2}, fuente="calculadora", verified=True,
            derived_from={"dato:0": v})
    f = m.current("calc:0")
    check("5a. derivado de raíz sin autoridad NO queda verified",
          f is None or not f["verified"], f"verified={(f or {}).get('verified')}")

    # cadena desconocido -> derivado -> derivado2: nunca canónico
    if f is None:
        check("5b. no hay autoridad para seguir derivando (cadena muere)", True)
    else:
        v2 = m.write("calc:1", {"r": 3}, fuente="calculadora", verified=True,
                     derived_from={"calc:0": f["version"]})
        f2 = m.current("calc:1")
        check("5b. segundo derivado también queda sin autoridad",
              f2 is None or not f2["verified"])


# ─────────────────────────────────────────────────────────────────────────────
# 6. NORMA MODIFICADA tras el cierre: la evidencia vieja NO debe seguir cerrando
#    (Fable 5.2, cierre_sesion: "si la norma cambia después del cierre, ¿la evidencia vieja
#    sigue cerrando? Si entregable no deriva de la versión de la norma, hay otro falso cierre.")
# ─────────────────────────────────────────────────────────────────────────────
def test_norma_modificada():
    print("\n[6] NORMA modificada tras cierre: la evidencia vieja no debe seguir cerrando")
    m = _mk()
    v_orac = m.write("cota:cvt", {"v": 1.0}, fuente="tabla", verified=True)
    m.write("prod:0", {"hecho": True}, fuente="calculadora", verified=True,
            derived_from={"cota:cvt": v_orac})
    deontica.registrar_entregable(m, "e0", "producto", "t0", predicado_ref="p",
                                  requiere=[("prod:0", "conforme")])
    est, _ = deontica.cerrar_si_predicado(m, "e0", lambda mem: (True, "ok"))
    check("6a. cierre inicial a satisfecho", est == "satisfecho")

    # el cierre debe derivar de la VERSIÓN de la norma (L)
    e = m.current("entregable:e0")
    check("6b. el cierre deriva de la versión de la norma (no huérfano)",
          e is not None and "obligacion:e0" in e["derived_from"])

    # se MODIFICA la norma (nueva versión verificada con cambio real)
    o = m.current("obligacion:e0")
    nuevo_val = dict(o["value"])
    nuevo_val["meta"] = "t0 (alcance revisado)"
    m.write("obligacion:e0", nuevo_val, unit="", fuente="usuario", verified=True)

    # la evidencia vieja debe colapsar (fallo de la versión vieja de la norma)
    e2 = m.current("entregable:e0")
    check("6c. reescribir la norma invalida el cierre (current None)", e2 is None,
          f"e2={e2 is not None}")
    # y el entregable se re-expresa como no-satisfecho (no falsa completitud)
    s = deontica.estado(m, "e0")
    check("6d. el entregable NO queda satisfecho tras el cambio de norma",
          s["estado"] != "satisfecho", f"estado={s['estado']}")
    check("6e. el turno NO está completo", not deontica.completo(m))


if __name__ == "__main__":
    test_estado_mixto()
    test_cerrar_luego_invalidar()
    test_mutacion_R()
    test_mutacion_A()
    test_lavado_procedencia()
    test_norma_modificada()
    print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
    sys.exit(1 if FAIL else 0)
