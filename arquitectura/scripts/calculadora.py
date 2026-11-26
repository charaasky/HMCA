#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""calculadora.py — DETERMINISTIC execution of calculations (trusted source 'calculadora').

The LLM decomposes the query into a SPEC (inputs + formulas) but the EXECUTION is deterministic:
a SAFE arithmetic evaluator (AST, no eval/exec, no functions, no attributes) computes each
formula. The result is written as a verified fact with lineage (derived_from → inputs) by the
monitor, not by the LLM.

Stdlib only. Does not import prototipo.py.
"""
import ast
import operator

# Allowed operators. No calls, attributes, subscripts, or comprehensions.
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# DoS limits (P0-C, Fable ruling): node and exponent caps.
MAX_NODES = 200
MAX_EXPONENT = 1000


def _count_nodes(node):
    n = 0
    for _ in ast.walk(node):
        n += 1
        if n > MAX_NODES:
            raise CalcError(f"expresión demasiado compleja (> {MAX_NODES} nodos)")
    return n


class CalcError(ValueError):
    """Invalid or not-allowed expression (=> deterministic denial)."""


def evaluar(expr, variables=None):
    """Evaluates a SAFE arithmetic expression. Returns float. Raises CalcError on any
    not-allowed node (calls, attributes, unknown names, non-numeric constants)."""
    try:
        tree = ast.parse(str(expr).strip(), mode="eval")
    except SyntaxError as e:
        raise CalcError(f"sintaxis inválida: {e}")
    _count_nodes(tree.body)
    return _eval(tree.body, variables or {})


def _eval(node, variables):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise CalcError(f"constante no numérica: {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id in variables:
            return float(variables[node.id])
        raise CalcError(f"variable desconocida: {node.id!r}")
    if isinstance(node, ast.BinOp):
        fn = _OPS.get(type(node.op))
        if fn is None:
            raise CalcError(f"operador no permitido: {type(node.op).__name__}")
        left = _eval(node.left, variables)
        right = _eval(node.right, variables)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalcError(f"exponente demasiado grande (|exp| > {MAX_EXPONENT})")
        try:
            return fn(left, right)
        except ZeroDivisionError:
            raise CalcError("división por cero")
        except OverflowError:
            raise CalcError("desbordamiento numérico")
    if isinstance(node, ast.UnaryOp):
        fn = _OPS.get(type(node.op))
        if fn is None:
            raise CalcError(f"operador unario no permitido: {type(node.op).__name__}")
        return fn(_eval(node.operand, variables))
    raise CalcError(f"nodo no permitido: {type(node).__name__}")


def computar(spec):
    """spec = {inputs: {name: value}, formulas: [{name, expr, unit}]}.

    Evaluates the formulas IN ORDER; each result becomes available as a variable for the next
    ones. Returns [{name, value, unit, expr}] (float). Raises CalcError on any failure
    (deterministic: no partial result)."""
    variables = {}
    for k, v in (spec.get("inputs") or {}).items():
        try:
            variables[k] = float(v)
        except (TypeError, ValueError):
            raise CalcError(f"input no numérico: {k}={v!r}")
    out = []
    for f in spec.get("formulas") or []:
        name = f.get("name")
        expr = f.get("expr")
        if not isinstance(name, str) or not name:
            raise CalcError("fórmula sin 'name'")
        if not isinstance(expr, str) or not expr:
            raise CalcError(f"fórmula '{name}' sin 'expr'")
        val = evaluar(expr, variables)
        variables[name] = val
        out.append({"name": name, "value": val, "unit": f.get("unit", "") or "",
                    "expr": expr})
    return out


if __name__ == "__main__":
    import json
    spec = {
        "inputs": {"v1": 4.17, "v2": 11.11, "d": 100, "m1": 120, "m2": 70},
        "formulas": [
            {"name": "tiempo_s", "expr": "d / (v1 + v2)", "unit": "s"},
            {"name": "distancia_A_m", "expr": "v1 * tiempo_s", "unit": "m"},
            {"name": "energia_J", "expr": "0.5 * m1 * v1**2 + 0.5 * m2 * v2**2", "unit": "J"},
        ],
    }
    print(json.dumps(computar(spec), indent=2, ensure_ascii=False))
    # security: a malicious expression must fail, not execute
    try:
        evaluar("__import__('os').system('id')", {})
        print("¡SEGURIDAD ROTA!")
    except CalcError as e:
        print("seguro:", e)
