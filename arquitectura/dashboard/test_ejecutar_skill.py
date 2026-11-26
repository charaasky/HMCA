#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test P0-C: ejecutar_skill determinista + límites de DoS del AST.

Cubre:
  1) skill declarativo con inputs VERIFIED -> resultado tier verified.
  2) input faltante -> {ok:False, falta:[...]} (se le pide al usuario, el LLM no inventa).
  3) input literal -> resultado tier claimed.
  4) AST rechaza exponente gigante (9**9**9) y llamadas maliciosas.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import llm_gateway as LG  # noqa: E402
import calculadora as CALC  # noqa: E402
import memory_context as MC  # noqa: E402


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return p


def main():
    db = _tmp()
    m = HCMAMemoriaSQLite(db, crear_esquema=True)

    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    m.write("motor.potencia", 1500, unit="W", fuente="ingeniero", verified=True)
    m.write("motor.tension", 12, unit="V", fuente="ingeniero", verified=True)

    # 1) inputs verified -> tier verified
    spec = {
        "inputs": {"P": {"key": "motor.potencia"}, "V": {"key": "motor.tension"}},
        "formulas": [{"name": "I", "expr": "P / V", "unit": "A"}],
    }
    r = LG.ejecutar_skill("corriente", spec, memoria=m)
    check("inputs verified -> tier verified",
          r.get("ok") and r.get("tier") == "verified"
          and abs(r["resultados"][0]["value"] - 125.0) < 1e-6,
          f"tier={r.get('tier')} r={r.get('resultados')}")

    # 2) input faltante
    spec2 = {"inputs": {"P": {"key": "no.existe"}},
             "formulas": [{"name": "x", "expr": "P * 2", "unit": ""}]}
    r2 = LG.ejecutar_skill("x", spec2, memoria=m)
    check("input faltante -> pedir al usuario", not r2.get("ok") and bool(r2.get("falta")),
          f"falta={r2.get('falta')}")

    # 3) input literal -> tier claimed
    spec3 = {"inputs": {"P": {"valor": 1500}, "V": {"valor": 12}},
             "formulas": [{"name": "I", "expr": "P / V", "unit": "A"}]}
    r3 = LG.ejecutar_skill("corriente", spec3, memoria=m)
    check("input literal -> tier claimed", r3.get("ok") and r3.get("tier") == "claimed",
          f"tier={r3.get('tier')}")

    # 4) DoS: exponente gigante
    try:
        CALC.evaluar("9**9**9", {})
        check("AST: 9**9**9 rechazado", False)
    except CALC.CalcError:
        check("AST: 9**9**9 rechazado", True)

    # 5) DoS: llamada/atributo no permitido
    try:
        CALC.evaluar("__import__('os').system('id')", {})
        check("AST: llamada rechazada", False)
    except CALC.CalcError:
        check("AST: llamada rechazada", True)

    # 6) DoS: nodos excesivos
    expr = "+".join(["1"] * 500)
    try:
        CALC.evaluar(expr, {})
        check("AST: nodos excesivos rechazado", False)
    except CALC.CalcError:
        check("AST: nodos excesivos rechazado", True)

    # 7) ruta: skill declarativo en memoria -> ejecutar_skill_route lo ejecuta
    MC.MEMORY_DB = db
    m.write("skill:corriente:spec",
            {"inputs": {"P": {"key": "motor.potencia"}, "V": {"key": "motor.tension"}},
             "formulas": [{"name": "I", "expr": "P / V", "unit": "A"}]},
            fuente="ingeniero", verified=True)
    rr = MC.ejecutar_skill_route("corriente")
    check("ruta ejecuta skill declarativo verified",
          rr is not None and rr.get("ok") and rr.get("tier") == "verified",
          f"rr={rr}")

    m.close()
    os.remove(db)
    print("\nRESULTADO:", "✔ P0-C (ejecutar skill + AST seguro) OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
