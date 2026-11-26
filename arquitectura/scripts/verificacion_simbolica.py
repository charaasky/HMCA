#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""verificacion_simbolica.py — SYMBOLIC verification layer (sympy) of closed physical laws.

AlphaProof principle ("the LLM proposes, the formal verifier disposes"): a physical law is written
as a SYMBOLIC EQUATION; the values the agent proposes are SUBSTITUTED and the equation is verified
within tolerance. If the agent hallucinates a value, the equation does NOT close => it is not verified.

It is SYMBOLIC derivation (solve + substitution), not a bare float: it allows `resolver()` to solve the
correct unknown deterministically, against which what the agent proposed is confronted.

Symbols without value => "not verifiable" (what has no datum is not verified): it is the honest
form of "I don't know => I don't verify", consistent with the awareness of not knowing.
"""
import sympy as sp

# ── Common physics symbols (real) ──────────────────────────────────────
P, V, I, R = sp.symbols("P V I R", real=True)
V1, V2, I1, I2, N1, N2 = sp.symbols("V1 V2 I1 I2 N1 N2", real=True)
f, L, C = sp.symbols("f L C", real=True)

# Name->symbol map to pass values with string keys (serializable) and
# convert them to the EXACT symbols defined above (same assumptions).
SIMBOLOS = {"P": P, "V": V, "I": I, "R": R,
            "V1": V1, "V2": V2, "I1": I1, "I2": I2,
            "N1": N1, "N2": N2, "f": f, "L": L, "C": C}

# Closed physical laws (by name). Each is sp.Eq(lhs, rhs).
ECUACIONES = {
    "potencia":            sp.Eq(P, V * I),
    "ohm":                 sp.Eq(V, I * R),
    "transformador_tension":   sp.Eq(V1 / V2, N1 / N2),
    "transformador_potencia":  sp.Eq(V1 * I1, V2 * I2),
    "resonancia_lc":           sp.Eq(f, 1 / (2 * sp.pi * sp.sqrt(L * C))),
}


def _float(expr):
    return float(sp.N(expr))


def _coerce_valores(valores):
    """Converts string keys to the exact symbols (same assumptions)."""
    out = {}
    for k, v in (valores or {}).items():
        if isinstance(k, str):
            s = SIMBOLOS.get(k)
            if s is None:
                raise ValueError(f"símbolo desconocido: {k}")
            out[s] = v
        else:
            out[k] = v
    return out


def verificar_identidad(ecuacion, valores, tolerancia_rel=0.01):
    """Substitutes `valores` into `ecuacion` and verifies equality within relative tolerance.

    `valores` can use string keys (serializable) or symbols. Returns {ok, error_rel, lhs,
    rhs, detalle}. If symbols remain unsubstituted, returns ok=False (not verifiable).
    """
    valores = _coerce_valores(valores)
    eq = sp.simplify(ecuacion)
    lhs = eq.lhs.subs(valores)
    rhs = eq.rhs.subs(valores)
    libres = (lhs.free_symbols | rhs.free_symbols)
    if libres:
        return {"ok": False, "error_rel": None, "lhs": None, "rhs": None,
                "detalle": "símbolos sin valor: " + ", ".join(sorted(map(str, libres)))}
    lv = _float(lhs)
    rv = _float(rhs)
    if rv == 0:
        err = abs(lv - rv)
    else:
        err = abs(lv - rv) / abs(rv)
    ok = err <= tolerancia_rel
    return {"ok": ok, "error_rel": err, "lhs": lv, "rhs": rv,
            "detalle": f"{lv:.6g} vs {rv:.6g} (err {err:.3g})"}


def resolver(ecuacion, incognita, valores):
    """Solves the equation for `incognita` given `valores`. Returns the value or None.

    Deterministic: it is the 'physics computes the correct value' path.
    """
    valores = _coerce_valores(valores)
    eq = sp.simplify(ecuacion)
    sol = sp.solve(sp.Eq(eq.lhs, eq.rhs).subs(valores), incognita)
    if not sol:
        return None
    return _float(sol[0])


def verificar_ecuaciones(ecuaciones, tolerancia_rel=0.01):
    """Verifies a list of (equation_name, values). Returns {todas_ok, resultados}."""
    resultados = {}
    todas_ok = True
    for nombre, valores in ecuaciones:
        ec = ECUACIONES.get(nombre)
        if ec is None:
            resultados[nombre] = {"ok": False, "detalle": f"ecuación desconocida: {nombre}"}
            todas_ok = False
            continue
        r = verificar_identidad(ec, valores, tolerancia_rel=tolerancia_rel)
        resultados[nombre] = r
        todas_ok = todas_ok and r["ok"]
    return {"todas_ok": todas_ok, "resultados": resultados}


if __name__ == "__main__":
    import json
    # potencia: P=220W, V=220, I=1 -> coherent
    print("potencia 220V*1A=220W:",
          json.dumps(verificar_identidad(ECUACIONES["potencia"], {V: 220, I: 1, P: 220}),
                     ensure_ascii=False))
    # transformador 220V→9V, 1A→0.5A => potencia 220 vs 4.5 (INCOHERENT, negative control)
    r = verificar_identidad(ECUACIONES["transformador_potencia"],
                            {V1: 220, I1: 1, V2: 9, I2: 0.5})
    print("transformador 220W in vs 4.5W out:", json.dumps(r, ensure_ascii=False))
    # resolver: given V=9, I=0.5 => correct P
    print("resolver P = V*I con V=9, I=0.5 ->", resolver(ECUACIONES["potencia"], P, {V: 9, I: 0.5}))
