#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""pipeline_dominio.py — unifies the complete DOMAIN CREATION cycle.

Closes the step that Fable marked as post-paper (fable_pedir_cualquier_cosa, point 5 "Later,
explicitly"): unify

    candidate spec (LLM, autor=modelo)  ──▶  canonical spec (human)  ──▶  impl (code)
                                         ──▶  evaluate (golden tests)  ──▶  ok  ──▶  active

into ONE flow governed by HCMA. Reuses the primitives that already exist, without reimplementing:

    generar_dominio_candidato : the LLM drafts WHAT is computed (candidate spec, autor=modelo).
    skill_lib.registrar_spec  : only a human (engineer/user) ratifies the canonical spec.
    skill_lib.proponer_impl   : the deterministic code (HOW) is ALWAYS proposed unverified.
    skill_lib.evaluar         : runs golden tests and derives ok:<impl_hash> (canonical if there is
                                a source/oracle in the lineage of the tests).
    skill_lib.activar         : pointer to the executable impl.

The deontic/epistemic boundary is NOT relaxed: at NO point does an autor=modelo become
authority. The trust gradient goes candidate(unverified) -> spec(canonical, human) ->
ok(computed grade from the lineage).

This module is the deterministic ORCHESTRATOR that chains the pieces. Does NOT call the LLM except in
`proponer_candidata` (which delegates to generar_dominio_candidato).

Example usage (offline):
    m = HCMAMemoriaSQLite(':memory:')
    r = crear_dominio(m, pedido="doblar", spec_candidata=..., spec_canonica=...,
                      codigo_impl="def calcular(inputs): ...", fuente_tests={...})
    print(r)                        # includes the skill's final grade
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import skill_lib  # noqa: E402
import generar_dominio_candidato as GDC  # noqa: E402


def proponer_candidata(m, pedido, spec=None):
    """Step 1: the LLM (external) drafts the candidate spec and registers it UNVERIFIED.

    If `spec` is provided (e.g. by a test stub without network), it uses it; otherwise it calls the
    LLM (usar_llm=True). Returns (did, version, spec). It is NOT canonical (autor=modelo)."""
    if spec is None:
        spec = GDC.generar_spec_candidata(pedido, usar_llm=True)
    did, v = GDC.inscribir_candidata(m, spec)
    return did, v, spec


def ratificar_y_evaluar(m, did, spec_canonica, codigo_impl, fuente_tests=None):
    """Steps 2-5: human ratifies spec, impl is proposed, evaluated, activated.

    - `spec_canonica`: dict with `tests_dorados` (with optional `fuente` in each test to give
      canonical grade) and `outputs` with tolerance.
    - `codigo_impl`: self-contained Python code (defines `calcular(inputs)`).
    - `fuente_tests`: dict {fuente_id: (tipo, ref)} to register independent oracles that
       the golden tests cite as `fuente`. Without this, the ok stays 'conforme' (not 'canonical').

    Returns a dict with each step and the skill's final grade.
    """
    out = {"did": did, "pasos": {}}

    # Step 2: human ratifies the spec ('ingeniero' source is MANDATORY; 'modelo' fails)
    v_spec = skill_lib.registrar_spec(m, did, spec_canonica, fuente="ingeniero")
    out["pasos"]["spec_canonica"] = {"version": v_spec}

    # Step 3 (optional but recommended): register independent sources/oracles
    fuentes_registradas = {}
    for fid, (tipo, ref) in (fuente_tests or {}).items():
        v_fuente = skill_lib.registrar_fuente(m, fid, tipo, ref)
        fuentes_registradas[fid] = v_fuente
    out["pasos"]["fuentes"] = fuentes_registradas

    # Step 4: propose the impl (code) — ALWAYS unverified
    v_impl, h = skill_lib.proponer_impl(m, did, codigo_impl)
    out["pasos"]["impl"] = {"version": v_impl, "impl_hash": h}

    # Step 5: evaluate (runs golden tests) -> ok:<hash>
    res = skill_lib.evaluar(m, did, v_impl)
    out["pasos"]["evaluar"] = res

    if res.get("ok_version") is None:
        # the impl did not pass the tests: it is not activated
        out["grado"] = "desconocido"
        out["activo"] = False
        return out

    # Step 6: activate (pointer to the executable impl)
    skill_lib.activar(m, did, h)
    out["pasos"]["activo"] = {"impl_hash": h}

    # Computed grade of the skill (from the ok's lineage)
    out["grado"] = skill_lib.grado_skill(m, did, h)
    out["activo"] = True
    out["impl_hash"] = h
    return out


def crear_dominio(m=None, pedido="", spec_candidata=None, spec_canonica=None,
                 codigo_impl="", fuente_tests=None):
    """Generic end-to-end flow: candidate -> human -> impl -> evaluate -> active.

    Does NOT know of any concrete domain: it receives everything as a parameter. The deterministic
    calculation (impl) and the canonical spec (golden tests) are provided by the caller. The only
    concrete example candidate lives in tests, not here.

    Returns a dict with each step and the skill's final grade.
    """
    if m is None:
        m = HCMAMemoriaSQLite(":memory:")

    did, v, spec = proponer_candidata(m, pedido, spec=spec_candidata)
    resultado = {"did": did, "candidata_version": v, "candidata": spec}

    if spec_canonica is None:
        spec_canonica = {"nombre": spec["nombre"], "outputs": [], "tests_dorados": []}
    resultado.update(ratificar_y_evaluar(m, did, spec_canonica, codigo_impl, fuente_tests))
    return resultado
