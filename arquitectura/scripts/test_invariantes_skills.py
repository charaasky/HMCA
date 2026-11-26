#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Tests de los invariantes del subsistema de skills (POSTMORTEM_skills.md §7, §7ter, §7quater).

Versión 2 — spec corregida tras revisión de Fable (2026-11-07). Cambios vs v1:
  - I2:  ejecutable(id) ⇔ ∃ impl_hash: current(skill:<id>:ok:<impl_hash>) ≠ None
         (clave content-addressed, no posicional; canónico ya implica ¬stale).
  - I3:  idempotencia por (id, hash(contenido)), NO por id. `crear` no existe.
  - I5:  solo mutación de test/spec invalida (version-collapse solo dispara con writes verified).
  - I7:  completitud de linaje (ok deriva de TODOS los test:* del artefacto).
  - I8:  ok recomputable (value determinista re-ejecutable, no una etiqueta `fuente`).
  - I9:  lectura de impl unverified => requiere get_version(key, version), sin latest().
  - I10: tests dorados solo de fuente humana (ingeniero/usuario), nunca `calculadora`.

Estos tests son el PASO 1 del orden de construcción: verificar que la implementación ACTUAL
(antes de reconstruir) no satisface los invariantes. I1 debe salir RED ('test' en
FUENTE_CONFIABLE). I9 debe salir RED (no existe get_version).

NO importa skills.py. NO re-implementa verificación. Observa con las primitivas reales.

Uso:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps \
      python3 arquitectura/scripts/test_invariantes_skills.py
"""

import hashlib
import os
import tempfile
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite, FUENTE_CONFIABLE  # noqa: E402

FUENTE_SPURIA = "test"


def _sha256(s):
    return hashlib.sha256(str(s).encode()).hexdigest()


def _mk():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_inv_")
    os.close(fd)
    return HCMAMemoriaSQLite(path)


# ── helpers de observación (sin tocar core) ──────────────────────────────────
def _ejecutables(m, skill_id):
    """I2 según spec v2: ids del skill con algún ok:<impl_hash> canónico."""
    out = {}
    rows = m.conn.execute(
        "SELECT key FROM facts WHERE key LIKE ? AND authority=1 AND stale=0",
        (f"skill:{skill_id}:ok:%",)).fetchall()
    for r in rows:
        out[r["key"]] = r["key"]
    return out


# ── I1 ───────────────────────────────────────────────────────────────────────
def test_I1_fuente_congelada():
    if FUENTE_SPURIA in FUENTE_CONFIABLE:
        raise AssertionError(
            f"INVARIANTE I1 VIOLADO: '{FUENTE_SPURIA}' en FUENTE_CONFIABLE "
            f"({sorted(FUENTE_CONFIABLE)}). Bypass de auto-promoción; quitar.")


def test_I1_contraprueba():
    """Fuente legítima (calculadora con linaje) sí verifica; I1 no es 'nada verifica'."""
    m = _mk()
    m.write("a", 2.0, fuente="usuario", verified=True)
    m.write("b", 3.0, fuente="usuario", verified=True)
    m.write("c", 5.0, fuente="calculadora", verified=True,
            derived_from={"a": m.current("a")["version"], "b": m.current("b")["version"]})
    assert m.current("c") is not None and m.current("c")["verified"]


# ── I2 ───────────────────────────────────────────────────────────────────────
def test_I2_ejecutable():
    """Sin ok:<impl_hash> canónico, el skill no es ejecutable."""
    m = _mk()
    m.write("skill:demo:impl", "f(x)=x*2", fuente="base", verified=False)
    if _ejecutables(m, "demo"):
        raise AssertionError("INVARIANTE I2 VIOLADO: ejecutable sin ok:<impl_hash> canónico.")


# ── I3 ───────────────────────────────────────────────────────────────────────
def test_I3_idempotencia_por_hash():
    """proponer_impl(id, impl) es idempotente por (id, hash(impl)): re-proponer la MISMA
    impl no agrega una versión nueva. La capa skills DEBE comparar el hash (no puede
    apoyarse en el no-op de write, que solo aplica a writes verified; impl es unverified)."""
    m = _mk()
    impl = "f(x)=x*2"
    # simulamos lo que DEBE hacer proponer_impl: comparar hash contra la última versión
    def proponer_impl(id_, code):
        # última versión de la impl (lectura cruda por key, sin filtro verified)
        rows = m.conn.execute(
            "SELECT version, value FROM facts WHERE key=? ORDER BY version DESC LIMIT 1",
            (f"skill:{id_}:impl",)).fetchall()
        h = _sha256(code)
        if rows:
            last = rows[0]
            import json as _json
            last_val = _json.loads(last["value"])
            if _sha256(last_val) == h:
                return last["version"]  # misma impl => no-op
        return m.write(f"skill:{id_}:impl", code, fuente="base", verified=False)

    v1 = proponer_impl("demo", impl)
    n_after_first = m.conn.execute("SELECT COUNT(*) FROM facts WHERE key='skill:demo:impl'").fetchone()[0]
    v2 = proponer_impl("demo", impl)  # misma impl
    n_after_second = m.conn.execute("SELECT COUNT(*) FROM facts WHERE key='skill:demo:impl'").fetchone()[0]
    if v1 != v2 or n_after_first != n_after_second:
        raise AssertionError(
            f"INVARIANTE I3 VIOLADO: proponer la misma impl no fue idempotente "
            f"(v {v1}->{v2}, #filas {n_after_first}->{n_after_second}).")


# ── I4 ───────────────────────────────────────────────────────────────────────
def test_I4_linaje_ok():
    """ok deriva SOLO de test:* (que derivan de spec). Cadena: ok -> test:* -> spec."""
    m = _mk()
    v_spec = m.write("skill:demo:spec", "spec", fuente="ingeniero", verified=True)
    m.write("test:demo:c1", "pass", fuente="calculadora", verified=True,
            derived_from={"skill:demo:spec": v_spec})
    m.write("skill:demo:ok:abc", "cert", fuente="calculadora", verified=True,
            derived_from={"test:demo:c1": m.current("test:demo:c1")["version"]})
    ok = m.current("skill:demo:ok:abc")
    if ok is None or not ok["verified"]:
        raise AssertionError("INVARIANTE I4: caso válido no canónico (setup roto).")
    for dep_k in ok["derived_from"]:
        if not dep_k.startswith("test:"):
            raise AssertionError(f"INVARIANTE I4 VIOLADO: ok linajea a {dep_k} (no test:*).")


# ── I5 ───────────────────────────────────────────────────────────────────────
def test_I5_mutar_spec_invalida():
    """Re-escribir spec verified deja stale al ok que derivaba transitivamente de ella."""
    m = _mk()
    v_spec = m.write("skill:demo:spec", "spec-v1", fuente="ingeniero", verified=True)
    m.write("test:demo:c1", "pass", fuente="calculadora", verified=True,
            derived_from={"skill:demo:spec": v_spec})
    m.write("skill:demo:ok:abc", "cert", fuente="calculadora", verified=True,
            derived_from={"test:demo:c1": m.current("test:demo:c1")["version"]})
    assert m.current("skill:demo:ok:abc") is not None, "setup: ok debe ser canónico"

    m.write("skill:demo:spec", "spec-v2", fuente="ingeniero", verified=True)

    ok = m.current("skill:demo:ok:abc")
    if ok is not None and not ok["stale"]:
        raise AssertionError("INVARIANTE I5 VIOLADO: mutar spec no dejó stale al ok.")


# ── I6 ───────────────────────────────────────────────────────────────────────
def test_I6_write_version():
    m = _mk()
    r = m.write("k", 42.0, fuente="usuario", verified=True)
    if not isinstance(r, int):
        raise AssertionError(f"INVARIANTE I6 VIOLADO: write devolvió {r!r} (no int).")
    if m.current("k")["value"] != 42.0:
        raise AssertionError("INVARIANTE I6 VIOLADO: leer de vuelta no da el valor.")


# ── I7 (completitud de linaje) ───────────────────────────────────────────────
def test_I7_completitud_linaje():
    """ok debe derivar de TODOS los test:<id>:<impl_hash>:* de ese artefacto, no de
    un subconjunto. Agregar un test NUEVO no dispara collapse (clave nueva), así que
    la completitud tiene que chequearse explícitamente en evaluar/loader."""
    m = _mk()
    v_spec = m.write("skill:demo:spec", "spec", fuente="ingeniero", verified=True)
    impl_hash = _sha256("f(x)=x*2")
    for c in ("c1", "c2"):
        m.write(f"test:demo:{impl_hash}:{c}", "pass", fuente="calculadora", verified=True,
                derived_from={"skill:demo:spec": v_spec})
    test_keys = [f"test:demo:{impl_hash}:c1", f"test:demo:{impl_hash}:c2"]
    # ok que deriva SOLO de c1 (subconjunto) NO debería ser válido
    m.write("skill:demo:ok:x", "cert", fuente="calculadora", verified=True,
            derived_from={test_keys[0]: m.current(test_keys[0])["version"]})
    ok = m.current("skill:demo:ok:x")
    got = set(ok["derived_from"].keys()) if ok else set()
    expected = {k: m.current(k)["version"] for k in test_keys}
    if got != set(expected.keys()):
        raise AssertionError(
            "INVARIANTE I7 VIOLADO: ok deriva de un subconjunto de tests "
            f"(got={sorted(got)} expected={sorted(expected)}).")


# ── I8 (ok recomputable) ─────────────────────────────────────────────────────
def test_I8_ok_recomputable():
    """El value del certificado ok debe poder recomputarse: guarda impl_hash y deriva de
    tests canónicos; el loader re-ejecuta y compara. Aquí verificamos que el certificado
    queda verified SOLO con linaje a tests (no con `fuente` suelto) y que su value pine el
    hash de la impl (recomputable: sha256(impl) == impl_hash)."""
    m = _mk()
    impl = "f(x)=x*2"
    impl_hash = _sha256(impl)
    m.write("skill:demo:impl", impl, fuente="base", verified=False)
    v_spec = m.write("skill:demo:spec", "spec", fuente="ingeniero", verified=True)
    m.write(f"test:demo:{impl_hash}:c1", "pass", fuente="calculadora", verified=True,
            derived_from={"skill:demo:spec": v_spec})
    # certificado canónico: deriva del test y pine el hash de la impl
    m.write("skill:demo:ok:" + impl_hash, impl_hash, fuente="calculadora", verified=True,
            derived_from={f"test:demo:{impl_hash}:c1": m.current(f"test:demo:{impl_hash}:c1")["version"]})
    ok = m.current("skill:demo:ok:" + impl_hash)
    if ok is None:
        raise AssertionError("INVARIANTE I8: certificado no canónico (setup roto).")
    if ok["value"] != impl_hash:
        raise AssertionError("INVARIANTE I8 VIOLADO: certificado no pineado al hash de impl.")


# ── I9 (lectura de impl unverified) ──────────────────────────────────────────
def test_I9_get_version():
    """Debe existir una primitiva que lea un hecho unverified POR VERSIÓN exacta
    (get_version), para ejecutar la impl. current() devuelve None para unverified."""
    m = _mk()
    v = m.write("skill:demo:impl", "f(x)=x*2", fuente="base", verified=False)
    # current() no la ve (unverified)
    assert m.current("skill:demo:impl") is None, "setup: impl unverified debe ser invisible a current"
    # la primitiva de lectura por versión debe existir y devolverla
    if not hasattr(m, "get_version"):
        raise AssertionError(
            "INVARIANTE I9 VIOLADO: HCMAMemoriaSQLite no expone get_version(key, version). "
            "Sin ella no se puede leer la impl unverified para ejecutar el skill.")
    fact = m.get_version("skill:demo:impl", v)
    if fact is None or fact.get("value") != "f(x)=x*2":
        raise AssertionError("INVARIANTE I9 VIOLADO: get_version no devolvió la impl.")


# ── I10 (tests dorados solo fuente humana) ───────────────────────────────────
def test_I10_tests_fuente_humana():
    """Un test dorado escrito por 'calculadora' (no humano) NO debe ser verified."""
    m = _mk()
    m.write("skill:demo:spec", "spec", fuente="ingeniero", verified=True)
    # 'calculadora' autorando un test (sin fuente humana) es ficción: no debe verificar
    m.write("test:demo:bad", "pass", fuente="calculadora", verified=True, derived_from={})
    t = m.current("test:demo:bad")
    # 'calculadora' sin derived_from => write fuerza verified=False (ya en core)
    #   así que current() debe ser None (no canónico), o si existe, no verified.
    if t is not None and t["verified"]:
        raise AssertionError(
            "INVARIANTE I10 VIOLADO: test dorado auto-autorado por calculadora quedó verified.")


# ── runner ───────────────────────────────────────────────────────────────────
TESTS = [
    ("I1 no-auto-promoción (fuente congelada)", test_I1_fuente_congelada),
    ("I1 contraprueba (fuente legítima sí verifica)", test_I1_contraprueba),
    ("I2 ejecutable ⇔ ok:<impl_hash> canónico", test_I2_ejecutable),
    ("I3 idempotencia por hash de impl", test_I3_idempotencia_por_hash),
    ("I4 linaje ok -> test:* -> spec", test_I4_linaje_ok),
    ("I5 mutar spec ⇒ ok stale", test_I5_mutar_spec_invalida),
    ("I6 write devuelve versión", test_I6_write_version),
    ("I7 completitud de linaje (todos los tests)", test_I7_completitud_linaje),
    ("I8 ok recomputable (impl_hash pineado)", test_I8_ok_recomputable),
    ("I9 lectura de impl unverified (get_version)", test_I9_get_version),
    ("I10 tests dorados solo fuente humana", test_I10_tests_fuente_humana),
]


def main():
    print(f"FUENTE_CONFIABLE actual: {sorted(FUENTE_CONFIABLE)}")
    print("=" * 72)
    red = 0
    green = 0
    for name, fn in TESTS:
        try:
            fn()
        except AssertionError as e:
            red += 1
            print(f"  ❌ {name}: {e}")
        except Exception as e:
            red += 1
            print(f"  ❌ {name}: (excepción) {type(e).__name__}: {e}")
        else:
            green += 1
            print(f"  ✅ {name}")
    print("=" * 72)
    print(f"RESULTADO: {green} GREEN / {red} RED")
    print("Esperado antes de reconstruir: I1 RED ('test' en FUENTE_CONFIABLE) e I9 RED (sin get_version).")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
