#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""topologia.py — physical topology awareness + honesty gate (structural abstention).

Closes the P0 that the user uncovered: HCMA derives magnitudes with lineage (provenance) but does NOT
model the PHYSICAL TOPOLOGY (how components connect). A bag of numbers is not a
manufacturable object. This module introduces the TOPOLOGY boundary, analogous to the `no_calculable`
of magnitudes, and the deontic GATE that degrades the assertion when it is missing.

Fable 5.2 model (fable_caminos_topologia + fable_topologia_fisica):
  - Topology is an IMPLICIT ASSUMPTION of every derivation (not a premise; ATMS).
  - State `valido_bajo.topologia` ∈ {ausente, asumida, declarada} (Falkenhainer&Forbus).
  - Propagation by MINIMUM: the grade of a value is min(inputs, topological assumption).
  - Minimum authority: a topology proposed by the model stays `asumida` and NEVER rises;
    only the human ratifies it (`declarada`).
  - Exit gate: forbidden to emit "ready to manufacture" if any value has topology
    != `declarada`; emit the real grade and the list of missing premises.

This is step 1 of the plan (cheap, "stop lying today"), without net schema, verifier
nor generator.

STATES (increasing order of strength):
  AUSENTE  → the derivation does not record a topological assumption (what happens today).
  ASUMIDA  → there is a topology, but proposed by the model/LLM, not ratified.
  DECLARADA → topology ratified by a human (authority).

DELIVERABLE GRADE (increasing order):
  FORMULA_SUELTA  → only magnitudes, without topology (not manufacturable).
  DERIVADO_BAJO_SUPUESTO → magnitudes under assumed topology.
  CONECTADO       → declared topology, verified connections.
  FABRICABLE      → conectado + realized (every component to a part).
"""

# Topology states
AUSENTE = "ausente"
ASUMIDA = "asumida"
DECLARADA = "declarada"

# Mechanism COVERAGE states (the model class, second assumption that caps the grade).
# Fable (debilidades_camino4): a LINEAR model does not cover a non-linear function (saturation,
# buckling, switching). Asserting the function outside coverage => 'not applicable', never 'passes'.
MODELO_NO_DECLARADO = "modelo_no_declarado"
MODELO_LINEAL = "lineal"           # only covers linear behavior; does NOT cover saturation/regulation
MODELO_PARAMETRICO = "parametrico"  # generic B-H curve; covers the MECHANISM with capped grade
MODELO_COMPLETO = "no_lineal_real" # measured material curve; quantitative verification

# Deliverable grades (increasing order)
_FORMULA_SUELTA = "formula_suelta"
_DERIVADO_BAJO_SUPUESTO = "derivado_bajo_supuesto"
_CONECTADO = "conectado"
_FUNCIONAL = "funcional"
_REALIZABLE = "realizable"
_FABRICABLE = "fabricable"

_ORDEN_TOPOLOGIA = {AUSENTE: 0, ASUMIDA: 1, DECLARADA: 2}
_ORDEN_MODELO = {
    MODELO_NO_DECLARADO: 0,
    MODELO_LINEAL: 1,
    MODELO_PARAMETRICO: 2,
    MODELO_COMPLETO: 3,
}
_ORDEN_GRADO = {
    _FORMULA_SUELTA: 0,
    _DERIVADO_BAJO_SUPUESTO: 1,
    _CONECTADO: 2,
    _FUNCIONAL: 3,
    _REALIZABLE: 4,
    _FABRICABLE: 5,
}


def grado_entregable(estados_topologia):
    """Computes the REAL grade of a deliverable from the topology states of its values.

    Receives an iterable of states (ausente/asumida/declarada). Returns the grade degraded
    by the minimum (the weakest topology rules) plus the list of missing premises.
    """
    estados = list(estados_topologia)
    if not estados:
        return _FORMULA_SUELTA, ["no hay magnitudes"]
    # minimum topology state present
    min_estado = min(estados, key=lambda e: _ORDEN_TOPOLOGIA[e])
    faltantes = []

    if min_estado == AUSENTE:
        grado = _FORMULA_SUELTA
        faltantes.append("topología de conexiones no declarada (fórmulas sueltas)")
    elif min_estado == ASUMIDA:
        grado = _DERIVADO_BAJO_SUPUESTO
        faltantes.append("topología solo asumida (propuesta, no ratificada por humano)")
    else:
        grado = _CONECTADO

    return grado, faltantes


def afirmacion_permitida(grado_entregable, clase_pedida):
    """Deontic gate: is it lawful to assert `clase_pedida` given the real `grado_entregable`?

    A class STRONGER than the real grade can never be asserted. Returns (permitido, motivo).
    """
    if clase_pedida not in _ORDEN_GRADO:
        return False, f"clase no reconocida: {clase_pedida}"
    if _ORDEN_GRADO[clase_pedida] <= _ORDEN_GRADO[grado_entregable]:
        return True, ""
    return False, (f"no se puede afirmar '{clase_pedida}': el grado real es "
                   f"'{grado_entregable}'")


def degradar_afirmacion(grado_entregable, clase_pedida, faltantes):
    """Returns the DEGRADED (honest) assertion from the real grade and the requested one.

    If the grade is lower than the requested class, it degrades to the real class and lists what is missing.
    """
    permitido, _ = afirmacion_permitida(grado_entregable, clase_pedida)
    if permitido:
        return {"afirmacion": clase_pedida, "degradada": False, "faltantes": []}
    # degrade to the real class (the strongest allowed by the grade)
    clases_ordenadas = sorted(_ORDEN_GRADO, key=lambda c: _ORDEN_GRADO[c])
    real_idx = _ORDEN_GRADO[grado_entregable]
    clase_real = clases_ordenadas[real_idx]
    return {"afirmacion": clase_real, "degradada": True, "faltantes": faltantes}


# ── convenience ───────────────────────────────────────────────────────────────
CLASE_LISTO_PARA_FABRICAR = _FABRICABLE
CLASE_DIMENSIONAMIENTO = _FORMULA_SUELTA
CLASE_FUNCIONAL = _FUNCIONAL


# ── step 2: mechanism coverage (grade inflated by model class) ─────────
def cobertura_modelo(clase_modelo, mecanismo_requerido):
    """Does the `clase_modelo` cover the `mecanismo_requerido`?

    Fable (debilidades_camino4 D2 generalized): a device whose function lives in a
    NON-LINEARITY (saturation/buckling/switching/hysteresis) is NOT covered by a LINEAR
    model. Asserting the function outside coverage is the inflated-grade P0.

    `mecanismo_requerido` ∈ {LINEAL, NO_LINEAL}. Returns (cubre, motivo).
    """
    if mecanismo_requerido == "lineal":
        # any non-degenerate model covers a linear mechanism (minimum linear)
        return clase_modelo != MODELO_NO_DECLARADO, (
            "modelo no declarado" if clase_modelo == MODELO_NO_DECLARADO else "")
    if mecanismo_requerido in ("no_lineal", "saturacion", "regulacion"):
        cubre = clase_modelo in (MODELO_PARAMETRICO, MODELO_COMPLETO)
        motivo = "" if cubre else (
            f"mecanismo no-lineal ({mecanismo_requerido}) NO cubierto por modelo {clase_modelo}"
        )
        return cubre, motivo
    raise ValueError(f"mecanismo_requerido no reconocido: {mecanismo_requerido}")


def grado_funcional(clase_modelo, mecanismo_requerido):
    """REAL functional grade given the model class and the mechanism one intends to assert.

    - If the mechanism is non-linear and the model is linear => no coverage => abstain
      (the function cannot be asserted). Returns the honest rung.
    """
    cubre, motivo = cobertura_modelo(clase_modelo, mecanismo_requerido)
    if not cubre:
        return _CONECTADO, motivo  # conectado, but the FUNCTION is not covered
    if clase_modelo == MODELO_COMPLETO:
        return _FUNCIONAL, ""
    if clase_modelo == MODELO_PARAMETRICO:
        return _FUNCIONAL, "funcional con modelo PARAMÉTRICO (grado topeado; sin medición)"
    return _DERIVADO_BAJO_SUPUESTO, ""


def negarse_a_promover(funcion_afirmada, mecanismo, clase_modelo):
    """The gate that REFUSES to promote a function when the model does not cover it.

    Fable: 'that refusal is your best demonstration'. Returns a dict with the decision.
    """
    cubre, motivo = cobertura_modelo(clase_modelo, mecanismo)
    if cubre:
        grado, detalle = grado_funcional(clase_modelo, mecanismo)
        return {"promueve": True, "grado": grado, "motivo": detalle}
    return {"promueve": False, "grado": _CONECTADO,
            "motivo": (f"NO se puede afirmar '{funcion_afirmada}': {motivo}")}
