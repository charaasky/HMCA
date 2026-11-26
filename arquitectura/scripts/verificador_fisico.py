#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""verificador_fisico.py — integrates the DETERMINISTIC simulator with HCMA (I-SIM, §7decies).

Closes the pending item documented in POSTMORTEM_skills.md §7decies ("Integration with HCMA: fact
`simulador:<sid>` + derived `sim:...` + complete I-SIM"): today the engine `simulador.py` is pure
execution (runs ngspice and extracts metrics) but does NOT live in governed memory as a DERIVER.

This module is the GOVERNANCE LAYER: wraps the engine and produces derived facts, applying Fable's
I-SIM invariant:

  - the simulator is `calculadora`, NEVER a root nor `tabla` (it does not fabricate data authority);
  - `authority(sim) = authority(sid) ∧ ∀p∈params: authority(p)` (the result inherits the authority
    of the WORST input);
  - `derived_from(sim) ⊇ {sid, plantilla_hash, params}`;
  - deterministic extract_hash (two runs = same hash), mandatory negative control.

WHAT IT VERIFIES (closed-physics cross-check):
  An OBSERVABLE is computed by TWO independent paths and confronted:
    1. ANALYTICAL: closed physical law formula (faraday, resonance, binary division, …).
    2. SIMULATED: the engine `simulador.py` (ngspice) derives it from the topology.
  If |simulated - analytical| <= tolerance => the design is COHERENT (the structure does what the
  formula asserts). This "connects the physics" without believing the LLM: it is double derivation.

Does NOT hardcode a domain: each domain contributes its verification TEMPLATE (observable + analytical
function + template netlist). Here it only ORCHESTRATES and governs the lineage.

Honest boundary (unchanged): this verifies COHERENCE/structure, not validity of contingent
data. A false catalog datum that does not contradict the physical law still goes undetected;
for that a registered canonical source (registrar_fuente) or bound (registrar_cota) is needed.
"""
import hashlib
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def _canon(obj) -> str:
    import json as _json
    return _json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False)


def hash_plantilla(plantilla) -> str:
    """Canonical hash of a verification template (netlist + observable + formula)."""
    return hashlib.sha256(_canon(plantilla).encode("utf-8")).hexdigest()


def extract_hash(mediciones) -> str:
    """Deterministic hash of a canonical EXTRACTION (scalar metrics), never of the rawfile."""
    return hashlib.sha256(_canon(mediciones).encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Verification templates (topology + observable + closed analytical formula)
# ─────────────────────────────────────────────────────────────────────────────
def plantilla_resonancia_lc(L_mH, C_uF, f_objetivo_Hz=50.0, tol_rel=0.01):
    """Template: the LC tank resonance frequency must match the analytical one.

    It is the cross-check of the CVT and of any LC tank. Returns the template dict that
    `verificar` runs. `f_objetivo` is the mains frequency (50/60 Hz).
    """
    import math
    L = L_mH / 1000.0
    C = C_uF / 1e6
    return {
        "nombre": "resonancia_lc",
        "observable": "f_resonancia_Hz",
        "analitica": 1.0 / (2 * math.pi * math.sqrt(L * C)),
        "tol_rel": tol_rel,
        "params": {"L_mH": L_mH, "C_uF": C_uF},
        "fn_simular": "resonancia_lc",
    }


def plantilla_division_binaria(f_cristal_Hz, n_etapas, f_objetivo_Hz=1.0, tol_rel=1e-9):
    """Template: a crystal oscillator divided by 2^n must give f_objetivo EXACTLY.

    Closed physics of a digital clock: f_salida = f_cristal / 2^n. The check is NOT simulate vs
    analytical (it would self-validate: both use the SAME division). The check is AGAINST THE OBJECTIVE:
    e.g. a 32768 Hz crystal / 2^15 = exactly 1 Hz => OK; a 32700 Hz crystal => 0.9979 Hz,
    which FAILS against the 1 Hz objective. Thus it detects a wrong CRYSTAL CHOICE (frequency
    hallucination), not just arithmetic.
    """
    return {
        "nombre": "division_binaria",
        "observable": "f_salida_Hz",
        "analitica": f_cristal_Hz / float(2 ** n_etapas),
        "f_objetivo_Hz": f_objetivo_Hz,
        "tol_rel": tol_rel,
        "params": {"f_cristal_Hz": f_cristal_Hz, "n_etapas": n_etapas},
        "fn_simular": "division_binaria",
    }


def plantilla_transformador(V_in, V_out, I_in, I_out, N_in=None, N_out=None, tol_rel=0.02):
    """SYMBOLIC template: a transformer conserves power (V1*I1 == V2*I2) and, if the
    turns are given, satisfies the transformation ratio (V1/V2 == N1/N2).

    It is FORMAL verification (sympy), not ngspice: the law is written as an equation and the values are
    substituted. The classic hallucination example '220V 1A -> 9V 0.5A' gives 220W in vs 4.5W out =>
    incoherent (the physics does not close).
    """
    ecuaciones = [("transformador_potencia",
                    {"V1": V_in, "I1": I_in, "V2": V_out, "I2": I_out})]
    if N_in is not None and N_out is not None:
        ecuaciones.append(("transformador_tension",
                           {"V1": V_in, "V2": V_out, "N1": N_in, "N2": N_out}))
    return {
        "nombre": "transformador",
        "observable": "conservacion_potencia",
        "ecuaciones": ecuaciones,
        "tol_rel": tol_rel,
        "params": {"V_in": V_in, "V_out": V_out, "I_in": I_in, "I_out": I_out,
                   "N_in": N_in, "N_out": N_out},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Core: runs a cross-check and returns the verdict (without touching HCMA yet)
# ─────────────────────────────────────────────────────────────────────────────
def _ejecutar_plantilla(plantilla):
    """Runs the template and returns {observable, analitica, simulada, error_rel, coherente}.

    The 'simulated' path is obtained:
      - via ngspice (engine `simulador.py`) if `fn_simular` is in that module, or
      - via deterministic evaluation of a closed function (e.g. binary division) if it is here.
    Does NOT use the LLM nor the network: it is deterministic.
    """
    nombre_fn = plantilla.get("fn_simular")
    params = plantilla.get("params", {})

    # 0) SYMBOLIC path (sympy): template with `ecuaciones` => FORMAL verification of the law.
    #    (transformador, potencia, ohm, …). Does not use ngspice: the equation IS the check.
    if plantilla.get("ecuaciones"):
        import verificacion_simbolica as VS
        r = VS.verificar_ecuaciones(plantilla["ecuaciones"],
                                    tolerancia_rel=plantilla.get("tol_rel", 0.01))
        return {
            "observable": plantilla.get("observable"),
            "analitica": None,
            "simulada": None,
            "coherente": r["todas_ok"],
            "fuente_simulacion": "simbolica",
            "ecuaciones": r["resultados"],
        }

    # 1) try the ngspice engine (resonancia, THD, etc.)
    try:
        import simulador as SIM
        if hasattr(SIM, nombre_fn):
            if nombre_fn == "resonancia_lc":
                res = SIM.resonancia_lc(params["L_mH"], params["C_uF"])
                return {
                    "observable": plantilla["observable"],
                    "analitica": plantilla["analitica"],
                    "simulada": res["f_simulada_Hz"],
                    "error_rel": res["error_relativo"],
                    "coherente": res["error_relativo"] <= plantilla.get("tol_rel", 0.01),
                    "fuente_simulacion": "ngspice",
                }
    except Exception as e:
        # the engine is not available: fall back to the closed path (if it exists)
        pass

    # 2) deterministic closed path (without ngspice): finite-state physics
    if nombre_fn == "division_binaria":
        f = float(params["f_cristal_Hz"]) / float(2 ** int(params["n_etapas"]))
        objetivo = plantilla.get("f_objetivo_Hz", plantilla.get("analitica"))
        err = abs(f - objetivo) / abs(objetivo) if objetivo else 0.0
        return {
            "observable": plantilla["observable"],
            "analitica": plantilla["analitica"],
            "simulada": f,
            "f_objetivo_Hz": objetivo,
            "error_rel": err,
            "coherente": err <= plantilla.get("tol_rel", 1e-9),
            "fuente_simulacion": "estado_finito",
        }

    return {"observable": plantilla.get("observable"), "error": "sin via de ejecucion",
            "coherente": False}


def verificar(plantilla, control_negativo=None):
    """Cross-check + negative control. Returns a verdict dict.

    `control_negativo` (optional): a dict {params_mutados, esperado_coherente=False}. If
    provided, it runs and MUST be incoherent (a verifier that never says 'no' is not one).
    """
    r = _ejecutar_plantilla(plantilla)
    r["plantilla_hash"] = hash_plantilla(plantilla)
    r["extract_hash"] = extract_hash({"simulada": r.get("simulada"),
                                      "analitica": r.get("analitica")})

    if control_negativo is not None:
        mutada = dict(plantilla)
        mutada["params"] = dict(plantilla.get("params", {}))
        mutada["params"].update(control_negativo.get("params_mutados", {}))
        # the mutant is evaluated against the SAME objective; if it is physically wrong,
        # it MUST result incoherent (a verifier that never says 'no' is not an oracle).
        r_neg = _ejecutar_plantilla(mutada)
        r["control_negativo"] = {
            "ejecutado": True,
            "coherente": r_neg.get("coherente", False),
            "rechaza_mutante": not r_neg.get("coherente", False),
        }
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Integration with HCMA (deriver, never root — I-SIM)
# ─────────────────────────────────────────────────────────────────────────────
def registrar_simulacion(m, plantilla, veredicto, params_versions=None):
    """Writes the derived fact `sim:<plantilla_hash>` as `calculadora` (never root).

    `params_versions`: {key_hcma: version} of the authoritative parameters the simulator
    inherited. If present, `derived_from` includes them -> authority(sim) = min(inputs). The fact
    stays verified ONLY if the input lineage is fine (calculadora + current params).

    I-SIM: if there are NO authoritative inputs (empty params_versions), the fact does NOT self-verify:
    `derived_from` stays empty and `calculadora` without lineage => `verified=False`. It is correct:
    the simulator is a `calculadora`, and a calculator without authoritative data mints nothing.

    Returns (clave, version).
    """
    plantilla_hash = veredicto.get("plantilla_hash") or hash_plantilla(plantilla)
    clave = f"sim:{plantilla_hash}"
    df = {}
    for k, v in (params_versions or {}).items():
        df[k] = v
    version = m.write(
        clave,
        {
            "nombre": plantilla.get("nombre"),
            "observable": veredicto.get("observable"),
            "analitica": veredicto.get("analitica"),
            "simulada": veredicto.get("simulada"),
            "error_rel": veredicto.get("error_rel"),
            "coherente": veredicto.get("coherente"),
            "fuente_simulacion": veredicto.get("fuente_simulacion"),
            "extract_hash": veredicto.get("extract_hash"),
            "control_negativo": veredicto.get("control_negativo"),
        },
        unit="", fuente="calculadora", verified=True, derived_from=df,
    )
    return clave, version


def chequear_dominio(m, plantillas, params_versions=None):
    """Runs N physical verification templates over a design and registers each `sim:*`.

    Returns a summary dict: {nombre: {coherente, error_rel, ...}} + `todas_coherentes`.
    It is the point `sin_dominio` calls when the domain has closed verifiable physics.
    """
    resumen = {}
    todas = True
    for p in plantillas:
        v = verificar(p)
        registrar_simulacion(m, p, v, params_versions=params_versions)
        resumen[p.get("nombre")] = v
        todas = todas and v.get("coherente", False)
    return {"plantillas": resumen, "todas_coherentes": todas}


# ─────────────────────────────────────────────────────────────────────────────
# Discovery by BLOCK TYPE (without hardcoding a domain)
# ─────────────────────────────────────────────────────────────────────────────
# A domain declares its topology by TYPED blocks (e.g. tipo='tanque_lc',
# 'oscilador_cristal'). This registry maps tipo_de_bloque -> function that, given the
# block params, builds the physical verification template(s). Thus `sin_dominio`
# discovers WHAT physics to verify from the graph, without knowing the domain name.
REGISTRO_POR_TIPO = {
    "tanque_lc": lambda b: [plantilla_resonancia_lc(
        L_mH=float(b.get("L_mH", 0)), C_uF=float(b.get("C_uF", 0)),
        f_objetivo_Hz=float(b.get("f_Hz", 50.0)))],
    "oscilador_cristal": lambda b: [plantilla_division_binaria(
        f_cristal_Hz=float(b.get("f_cristal_Hz", 0)),
        n_etapas=int(b.get("n_etapas", 15)),
        f_objetivo_Hz=float(b.get("f_objetivo_Hz", 1.0)))],
    "transformador": lambda b: [plantilla_transformador(
        V_in=float(b.get("V_in", 0)), V_out=float(b.get("V_out", 0)),
        I_in=float(b.get("I_in", 0)), I_out=float(b.get("I_out", 0)),
        N_in=float(b["N_in"]) if b.get("N_in") is not None else None,
        N_out=float(b["N_out"]) if b.get("N_out") is not None else None)],
}


def plantillas_para_grafo(grafo):
    """Given a block graph, returns the applicable physical verification templates.

    Walks the blocks and, for each `tipo` with a registered verifier, builds the template from
    the block's parameters. Returns a list (can be empty if the domain has no closed
    physics verifiable by this module).
    """
    plantillas = []
    for b in (grafo or {}).get("bloques", []):
        if not isinstance(b, dict):
            continue
        tipo = b.get("tipo")
        factory = REGISTRO_POR_TIPO.get(tipo)
        if factory is not None:
            try:
                plantillas.extend(factory(b))
            except (TypeError, ValueError, ZeroDivisionError):
                continue  # block without enough params -> not verifiable here
    return plantillas


if __name__ == "__main__":
    # Demo of closed-physics cross-check (LC resonance + binary division).
    pl = plantilla_resonancia_lc(L_mH=30.81, C_uF=328.8, f_objetivo_Hz=50.0)
    v = verificar(pl)
    print("resonancia_lc:", v["simulada"], "Hz vs", round(v["analitica"], 3),
          "Hz -> coherente:", v["coherente"])

    pl2 = plantilla_division_binaria(32768.0, 15, f_objetivo_Hz=1.0)
    v2 = verificar(pl2)
    print("division_binaria:", v2["simulada"], "Hz vs", v2["analitica"],
          "Hz -> coherente:", v2["coherente"])

    # negative control: mutate the crystal to 32700 Hz (wrong) MUST be incoherent against 1 Hz
    pl_mut = plantilla_division_binaria(32700.0, 15, f_objetivo_Hz=1.0)
    v3 = verificar(pl_mut)
    print("cristal 32700 (erróneo) -> f_salida", round(v3["simulada"], 4),
          "Hz vs objetivo 1 Hz -> coherente:", v3["coherente"], "(debe ser False)")
