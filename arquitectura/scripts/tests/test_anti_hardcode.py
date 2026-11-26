#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_anti_hardcode.py — test AST que detecta especs/impls de dominio hardcodeadas en .py.

Blinda el anti-patrón que volvió a aparecer dos veces: escribir `SPEC_CANDIDATA_*`,
`SPEC_CANONICA_*`, `IMPL_*` o `FUENTES_*` a mano en un archivo, en vez de dejar que el LLM las
redacte en runtime (generar_spec_candidata usar_llm=True) o que un humano las ratifique por
canal sellado.

Detecta, por AST:
  (a) dict literales con las claves {inputs, formulas}   -> spec candidata hardcodeada
  (b) dict literales con la clave {tests_dorados}        -> spec canónica hardcodeada
  (c) string literales que contienen 'def calcular'      -> impl hardcodeada

Allowlist: los módulos que por diseño contienen estos dicts (el generador genérico y el
sello de procedencia), y los tests de este propio paquete.

Uso:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/tests/test_anti_hardcode.py
"""
import ast
import os
import pathlib
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Archivos que por diseño SÍ contienen estos literales (librería, no un caso de dominio).
ALLOWLIST_SPEC = {
    "generar_dominio_candidato.py",   # el borrador genérico del fallback (formulas vacías)
    "procedencia.py",                 # el ejemplo/demo de sello
    "pipeline_dominio.py",            # ejemplo docstring / demo genérico
}
ALLOWLIST_IMPL = {
    "skill_lib.py",       # lógica de skill, no impl de dominio
    "pipeline_dominio.py",  # el 'def calcular' es docstring de uso, no impl de dominio
}

CLAVES_SPEC_CANDIDATA = {"inputs", "formulas"}
CLAVE_SPEC_CANONICA = {"tests_dorados"}

# Módulos de dominio declarativo LEGADOS (etapa vieja, pre-camino-A). No son casos nuevos
# de dominio: son los dominios hardcodeados que ya sabemos que existen y que no borramos en
# este refactor (prototipo.py, migrar_dominios.py, selector_componentes.py, ejemplo_ferroresonante).
LEGADO_DOMINIO = {
    "prototipo.py",
    "migrar_dominios.py",
    "selector_componentes.py",
    "ejemplo_ferroresonante.py",
}

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


def _dict_claves(node):
    if not isinstance(node, ast.Dict):
        return None
    claves = set()
    for k in node.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            claves.add(k.value)
    return claves


def _dict_es_literal_de_dominio(node):
    """True si un dict literal declara inputs/formulas con VALORES literales (no accesos).

    Distingue un hardcode de dominio real (p. ej. {'inputs': [...], 'formulas': [...]})
    de una composición de campos (p. ej. {'inputs': spec.get('inputs'), 'formulas': [...]}),
    que en sin_dominio.py no es hardcode sino re-envasado de lo que el LLM ya generó.
    Requiere que el valor de 'formulas' sea una lista NO vacía (hardcode real lleva fórmulas).
    """
    claves = _dict_claves(node)
    if not (CLAVES_SPEC_CANDIDATA <= (claves or set())):
        return False
    # buscar el valor de 'formulas': si es una lista literal con >=1 elemento constante => hardcode
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and k.value == "formulas":
            if isinstance(v, ast.List) and len(v.elts) >= 1:
                return True
    return False


def escanear():
    violaciones = []
    for py in SCRIPTS.rglob("*.py"):
        # los tests son fixtures legítimos por naturaleza (prueban la lógica con specs de ejemplo)
        if py.name.startswith("test_"):
            continue
        # módulos de dominio declarativo LEGADOS (etapa vieja, no casos nuevos): no los tocamos
        if py.name in LEGADO_DOMINIO:
            continue
        src = py.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            violaciones.append(f"{py.name}: SyntaxError {e}")
            continue
        for node in ast.walk(tree):
            claves = _dict_claves(node)
            # spec candidata hardcodeada: dict literal con inputs+formulas y formulas no vacía
            if _dict_es_literal_de_dominio(node) and py.name not in ALLOWLIST_SPEC:
                violaciones.append(
                    f"{py.name}:{node.lineno} spec candidata hardcodeada (inputs+formulas con contenido)")
            # spec canónica hardcodeada: dict literal con tests_dorados
            if claves is not None and CLAVE_SPEC_CANONICA <= claves and py.name not in ALLOWLIST_SPEC:
                violaciones.append(
                    f"{py.name}:{node.lineno} spec canónica hardcodeada (clave 'tests_dorados')")
            # impl hardcodeada: string con 'def calcular' FUERA de docstrings
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and "def calcular" in node.value
                    and py.name not in ALLOWLIST_IMPL and py.name not in LEGADO_DOMINIO):
                violaciones.append(f"{py.name}:{node.lineno} impl hardcodeada ('def calcular')")
    return violaciones


def main():
    v = escanear()
    if v:
        print("VIOLACIONES de anti-hardcode detectadas:")
        for x in v:
            print("  ✗", x)
        check("sin especs/impls de dominio hardcodeadas", False, f"{len(v)} violaciones")
    else:
        check("sin especs/impls de dominio hardcodeadas", True)

    print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.exit(main())
