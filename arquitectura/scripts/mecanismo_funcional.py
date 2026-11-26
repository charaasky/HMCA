#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""mecanismo_funcional.py — 'functional' rung (grade 3): verify the mechanism.

Closes the rung that connects 'conectado' (the structure connects) with 'funcional' (the
structure DOES what it claims). Design from Fable 5.2 (fable_fabricable):

  - `conectado` says "the formulas refer to this structure".
  - `funcional` says "the structure does what is claimed".

Functionality is asserted PER BLOCK. Each block declares:
  - `funcion`: what function it asserts (e.g. "the ADC converts voltage to counts").
  - `clase_modelo`: how that function's mechanism is modeled (lineal / parametrico /
    no_lineal_real / caja_negra).
  - optionally `mecanismo`: the non-linearity the function involves ("conmutacion",
    "saturacion", "regulacion") if any.

The coverage rule (reuses topologia.cobertura_modelo / negarse_a_promover):
  - a LINEAR function is covered by a linear model or better.
  - a NON-LINEAR function (switching, saturation) is NOT covered by a LINEAR model:
    the system REFUSES to assert it "functional". It only enters as BLACK BOX with a
    datasheet contract (contingent datum), which is 'functional' with a ceiling, but it is NOT computed.

Returns:
  - `verificar_funcional(grafo, modelos)` -> dict with the global functional grade and, per block,
    {afirma, cubierta, motivo, clase_modelo}.

Does NOT touch the core. Reuses topologia.py (mechanism coverage) and topologia_bloques.py (graph).
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import topologia as T  # noqa: E402

# mechanism model classes (plus one extra: black box with contract, not computable)
CLASE_LINEAL = "lineal"
CLASE_PARAMETRICO = "parametrico"
CLASE_NO_LINEAL_REAL = "no_lineal_real"
CLASE_CAJA_NEGRA = "caja_negra"   # datasheet/contract, contingent datum, NOT computed


def _cubre(clase_modelo, mecanismo):
    """Coverage of a function given the model class and the mechanism (non-linearity)."""
    # black box: ALWAYS enters as 'functional' with a ceiling, but it is contingent datum (not
    # verified by calculation). We cover it so as not to block, but with a provenance reason.
    if clase_modelo == CLASE_CAJA_NEGRA:
        return True, "funcional como caja negra (contrato de datasheet, dato contingente no verificado)"
    if mecanismo in ("no_lineal", "saturacion", "regulacion", "conmutacion", "histéresis"):
        cubre = clase_modelo in (CLASE_PARAMETRICO, CLASE_NO_LINEAL_REAL)
        motivo = "" if cubre else (
            f"mecanismo no-lineal ({mecanismo}) NO cubierto por modelo {clase_modelo}")
        return cubre, motivo
    # linear mechanism (or no declared mechanism): any non-degenerate model covers
    if clase_modelo == CLASE_LINEAL:
        return True, ""
    # parametric/no_lineal_real also cover linear (superset)
    if clase_modelo in (CLASE_PARAMETRICO, CLASE_NO_LINEAL_REAL):
        return True, ""
    return False, f"modelo no reconocido: {clase_modelo}"


def verificar_funcional(grafo, modelos):
    """Verifies the graph's functionality. `modelos` = {bloque_id: {funcion?, clase_modelo,
    mecanismo?}}.

    Returns {bloques: [per-block verdict], funcional: bool, grado: str, defectos: [str]}.

    'funcional' global = True if ALL blocks that ASSERT a function are covered by
    their model, and there is at least one block that asserts a function. If any block asserts a
    non-linear function with a linear model, the block is 'not covered' and the global is False (grade
    stays at 'conectado').
    """
    bloques = grafo.get("bloques", []) if isinstance(grafo, dict) else []
    ids = {b.get("id") for b in bloques if isinstance(b, dict)}

    veredictos = []
    defectos = []
    n_afirman = 0
    n_cubiertas = 0

    for bid, info in (modelos or {}).items():
        if bid not in ids:
            defectos.append(f"modelo para bloque inexistente: {bid}")
            continue
        funcion = info.get("funcion")
        clase = info.get("clase_modelo")
        mecanismo = info.get("mecanismo")
        if not funcion or not clase:
            veredictos.append({"bloque": bid, "afirma": False, "cubierta": True,
                               "motivo": "sin función o sin clase de modelo declarada"})
            continue
        n_afirman += 1
        cubre, motivo = _cubre(clase, mecanismo)
        if cubre:
            n_cubiertas += 1
        else:
            defectos.append(f"bloque {bid}: {motivo}")
        veredictos.append({"bloque": bid, "afirma": True, "cubierta": cubre,
                           "clase_modelo": clase, "mecanismo": mecanismo, "motivo": motivo})

    funcional = n_afirman > 0 and n_afirman == n_cubiertas and not defectos
    grado = T._FUNCIONAL if funcional else T._CONECTADO
    return {"bloques": veredictos, "funcional": funcional, "grado": grado,
            "defectos": defectos}


def grado_global(topologia_estado, funcional_ok):
    """Global grade combining topology (conectado) and functionality.

    - DECLARED topology + funcional_ok -> 'funcional' (grade 3)
    - DECLARED topology without functional -> 'conectado' (grade 2)
    """
    if topologia_estado == T.DECLARADA and funcional_ok:
        return T._FUNCIONAL
    return T._CONECTADO


if __name__ == "__main__":
    grafo = {
        "bloques": [
            {"id": "bateria", "tipo": "fuente", "puertos": [{"id": "V+", "dominio": "potencia"}, {"id": "V-", "dominio": "tierra"}]},
            {"id": "adc", "tipo": "adc", "puertos": [{"id": "V+", "dominio": "potencia"}, {"id": "V-", "dominio": "tierra"}]},
        ],
        "nets": [
            {"id": "vbus", "conexiones": [{"bloque": "bateria", "puerto": "V+"}, {"bloque": "adc", "puerto": "V+"}]},
            {"id": "gnd", "conexiones": [{"bloque": "bateria", "puerto": "V-"}, {"bloque": "adc", "puerto": "V-"}]},
        ],
        "bindings": [],
    }
    # double-ramp ADC = switching -> black box (datasheet contract)
    modelos = {
        "adc": {"funcion": "convierte tensión a cuentas", "clase_modelo": "caja_negra", "mecanismo": "conmutacion"},
    }
    v = verificar_funcional(grafo, modelos)
    import json
    print(json.dumps(v, ensure_ascii=False, indent=1))

    # now with a linear model (incorrect): refuses to promote switching
    modelos_mal = {
        "adc": {"funcion": "convierte tensión a cuentas", "clase_modelo": "lineal", "mecanismo": "conmutacion"},
    }
    print(json.dumps(verificar_funcional(grafo, modelos_mal), ensure_ascii=False, indent=1))
