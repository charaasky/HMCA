#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""autoratificacion.py — self-ratification cycle WITH retries (post-authorize).

Design validated by Fable 5.2 (`fable_autoratificacion`). Implements the state machine for
ratifying a domain CANDIDATE, separating the BOUNDARY (evidence, not "human vs
machine") and the central principle:

    self-ratification is safe ONLY for what is DEDUCIBLE (derivable by a non-LLM verifier
    from already-ratified premises); CONTINGENT DATA requires a PRE-REGISTERED source
    and, if there is none, the system STOPS AND ASKS (does not retry blindly).

This is the piece that closes the "it gives me loose formulas" bug in its epistemic dimension:
the system is AWARE of what it can self-certify and what it cannot, and it declares it.

UX FLOW (what the user requested):
  1. `I don't know this -> I'll create a new domain for you to compute it`
  2. it builds it, RATIFIES it (only what is deducible), and DELIVERS it with an epistemic label
     (the DRAFT is not shown: the epistemic state travels in every delivery).
  3. if the contingent datum has no source, it ASKS whether to retry N times / provide a source /
     provide the datum.

STATES (machine):
    DESCONOCIDO        -> the request has no domain or candidate yet.
    CANDIDATA          -> there is a candidate (autor=modelo), no frozen oracle.
    ORACULO_CONGELADO  -> the oracle (source) is hashed and frozen for the cycle.
    EN_VERIFICACION    -> the verifier is running (separate runner).
    VERIFICADA_MEC     -> passed the non-LLM verifier + deontic gate without self-approval.
    PROVISIONAL        -> delivered with 'provisional' label (missing contingent datum).
    BLOQUEADA_SIN_ORACULO -> there is no registered source for a contingent datum: STOP.
    AGOTADA            -> the retries were exhausted without verifying.
    RATIFICADA_HUMANA  -> a higher and distinct state (not automatic): human signature of the global domain.

INVARIANTS:
  I0 frozen oracle: the oracle is hashed BEFORE attempt 1; the cycle only mutates the
     candidate. Mutating test/tolerance/source within the cycle => STOP and ask.
  I1 authorship: `autor` is immutable; ratifying NEVER rewrites the author. Verification is a
     separate record that points to the evidence.
  I2 separation of functions: forbidden to ratify if any evidence has as its root author
     the author of the candidate, except proof from the non-LLM verifier.
  I3 epistemic label: every delivery carries a label (verified/provisional/assumed).
  I4 trusted source: 'modelo' NEVER enters FUENTE_CONFIABLE; what enters is the artifact.

It does not touch the core (memoria_sqlite.py) or paper6_osf/. It is an external module over the
deontic layer (deontica.py) and the TMS memory.
"""
import os
import sys
import hashlib
import json

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import topologia as T  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# States
# ─────────────────────────────────────────────────────────────────────────────
DESCONOCIDO = "desconocido"
CANDIDATA = "candidata"
ORACULO_CONGELADO = "oraculo_congelado"
EN_VERIFICACION = "en_verificacion"
VERIFICADA_MEC = "verificada_mec"
PROVISIONAL = "provisional"
BLOQUEADA_SIN_ORACULO = "bloqueada_sin_oraculo"
AGOTADA = "agotada"
RATIFICADA_HUMANA = "ratificada_humana"

ESTADOS = (DESCONOCIDO, CANDIDATA, ORACULO_CONGELADO, EN_VERIFICACION,
           VERIFICADA_MEC, PROVISIONAL, BLOQUEADA_SIN_ORACULO, AGOTADA,
           RATIFICADA_HUMANA)

# Assertion classes (deducible vs contingent boundary), default = contingent.
CLASE_DEFINICIONAL = "definicional"      # P = Φ/η, conversions: self-ratifies
CLASE_MODELO_EMPIRICO = "empirico"       # thermal derating: only with source or declared validity
CLASE_DATO_CONTINGENTE = "contingente"   # η (lm/W): only with registered source; otherwise, STOP
CLASE_NORMATIVO = "normativo"            # lux per standard: never automatic (source+version+jurisdiction)

CLASES = (CLASE_DEFINICIONAL, CLASE_MODELO_EMPIRICO, CLASE_DATO_CONTINGENTE, CLASE_NORMATIVO)

FUENTE_CONFIABLE = {"calculadora", "usuario", "ingeniero", "tabla"}
ORACULO_PREFIX = "fuente:"
REGISTRO_PREFIX = "registro_fuentes:"


def _hash(obj):
    canon = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# 1. clasificar_afirmaciones  (default = contingent)
# ─────────────────────────────────────────────────────────────────────────────
def clasificar_afirmaciones(candidata, m=None):
    """Returns {afirmacion_id: clase}, with CONTINGENT default.

    `candidata` carries its own assertions already annotated with a class when possible;
    EVERYTHING not annotated or not provable falls into contingent (Fable: 'the default is contingent',
    'if the LLM labels deducible it is self-proclamation again').

    P0-3 (fixed): a DEFINITIONAL class is only legitimate if it is DERIVABLE by a non-LLM verifier
    from premises ALREADY RATIFIED IN THE HCMA (`m`), NOT self-declared by the candidate.
    Also `de` must be NON-empty: a law without a premise is not derivable from anything.
    """
    out = {}
    afirmaciones = candidata.get("afirmaciones", {})
    if not afirmaciones:
        return out

    # ratified premises = those that are verified+authority+¬stale in the governed memory
    premisas_ratificadas = _premisas_ratificadas(m)
    for aid, a in afirmaciones.items():
        if isinstance(a, dict):
            clase = a.get("clase")
            derivable_por = a.get("derivable_por")
            de = a.get("de") or []
            # legitimate definitional: derivable_por IS a non-LLM verifier + non-empty premises
            # and all ratified in the HCMA
            if (clase == CLASE_DEFINICIONAL and derivable_por is not None and len(de) > 0
                    and set(de).issubset(premisas_ratificadas)):
                out[aid] = CLASE_DEFINICIONAL
                continue
            if clase in (CLASE_MODELO_EMPIRICO, CLASE_NORMATIVO) and a.get("fuente_requerida"):
                out[aid] = clase
                continue
            if clase == CLASE_DATO_CONTINGENTE:
                out[aid] = CLASE_DATO_CONTINGENTE
                continue
        out[aid] = CLASE_DATO_CONTINGENTE  # default
    return out


def _premisas_ratificadas(m):
    """Keys that are current verified+authority+¬stale in memory, or set() if there is no memory."""
    if m is None:
        return set()
    out = set()
    try:
        for k in m.keys(""):  # all current keys
            f = m.current(k)
            if f is not None and f["verified"] and f["authority"] and not f["stale"]:
                out.add(k)
    except Exception:
        pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 2. resolver_oraculo  (only REGISTRO_FUENTES, neither model nor free-web)
# ─────────────────────────────────────────────────────────────────────────────
def resolver_oraculo(m, afirmacion):
    """Looks up the source artifact ONLY in the pre-registered sources register.

    Returns the artifact (already with `fuente_id` and hash) or None. NEVER the value of the LLM's
    memory nor a transcript: an artifact retrieved by a non-LLM tool is required.
    """
    clave = afirmacion.get("fuente_id") or afirmacion.get("name")
    if not clave:
        return None
    # The sources register lives as `fuente:<id>` facts (canonical iff authority=True) or
    # as `registro_fuentes:<id>`. We look for the canonical source first.
    f = None
    for prefijo in (ORACULO_PREFIX, REGISTRO_PREFIX):
        cand = m.current(prefijo + clave)
        if cand is not None:
            f = cand
            break
    if f is None or not f["verified"]:
        return None
    valor = f["value"]
    if isinstance(valor, dict) and "valor" in valor:
        # artifact already hashed; we return it as-is
        return valor
    return {"fuente_id": clave, "valor": valor, "hash": _hash(valor),
            "ref": f.get("meta", {}).get("ref", "")}


# ─────────────────────────────────────────────────────────────────────────────
# 3. congelar_oraculo  (hash before attempt 1)
# ─────────────────────────────────────────────────────────────────────────────
def congelar_oraculo(dominio, artefactos, casos=None):
    """Hashes the set of source artifacts + cases and returns a frozen oracle.

    The id is deterministic over the content: ALWAYS the same hash for the same oracle.
    Mutating within the cycle is forbidden (I0). `casos` are the TRUTH cases (inputs+expected)
    that live in the oracle (governed memory, not in the candidate).
    """
    canonicos = {k: v for k, v in (artefactos or {}).items() if v is not None}
    casos = casos or []
    oracle = {"dominio": dominio.get("nombre"), "artefactos": canonicos, "casos": casos,
              "oraculo_id": _hash({"dominio": dominio.get("nombre"),
                                   "artefactos": canonicos, "casos": casos})}
    return oracle


# ─────────────────────────────────────────────────────────────────────────────
# 4. generar_candidata  (autor=modelo, without touching the oracle)
# ─────────────────────────────────────────────────────────────────────────────
def generar_candidata(dominio, feedback_limitado=None):
    """Produces/updates the candidate with autor=modelo.

    In the deterministic test flow, the candidate is already generated by the domain; here we
    mark it with autor=modelo and absorb the limited feedback (pass/fail, without expected
    values) for the next iteration. The oracle is NOT touched (I0).
    """
    cand = dict(dominio)
    cand["autor"] = cand.get("autor", "modelo")
    cand["feedback"] = feedback_limitado or {}
    # the draft is NOT shown: what travels is the epistemic state (I3)
    cand["_borrador_oculto"] = True
    return cand


# ─────────────────────────────────────────────────────────────────────────────
# 5. verificar  (runner separate from the generator)
# ─────────────────────────────────────────────────────────────────────────────
def verificar(candidata, oraculo, runner=None):
    """Runs the verifier in a runner SEPARATE from the generator.

    `runner(candidata, oraculo)` must be a NON-LLM (deterministic/property) function that
    returns `(ok, evidencia)`. Here it validates:
      - the oracle did not change (I0): same oraculo_id.
      - the evidence does not have the candidate's author as its root author (I2). This separation
        of functions is NOT saved by a self-declared flag (`prueba_verificador_no_llm`):
        that was a CIRCULAR protection (the same evidence that said `autor_raiz=modelo`
        could say `no_llm=True` and bypass the gate). The 'non-LLM' authority is given by the
        IDENTITY of the runner (the default deterministic verifier), not an output field.
    Returns a Veredicto dict.
    """
    runner = runner or _verificador_default
    # I0: the frozen oracle cannot have mutated
    if runner_oraculo_id(candidata) is not None:
        if runner_oraculo_id(candidata) != oraculo["oraculo_id"]:
            return {"veredicto": "bloqueado_sin_oraculo",
                    "motivo": "oráculo mutado dentro del ciclo (I0)", "evidencia": None}

    ok, evidencia = runner(candidata, oraculo)
    evidencia = evidencia or {}

    # I2: separation of functions — the evidence CANNOT have root author = candidate.
    # Armored falsification point (there was a bypass before): the `prueba_verificador_no_llm` flag
    # was self-declared by the same evidence and saved the escalation. Now the evidence
    # with autor_raíz == autor_candidata is ALWAYS blocked, with no flag exception.
    autor_candidata = candidata.get("autor")
    if autor_candidata is not None and _evidencia_autorraiz_eq(evidencia, autor_candidata):
        return {"veredicto": "bloqueado_sin_oraculo",
                "motivo": ("prohibido ratificar: evidencia con autor raíz = autor de la "
                           "candidata (I2, sin bypass)"), "evidencia": evidencia}

    if ok:
        return {"veredicto": "verificada_mec", "motivo": "", "evidencia": evidencia}
    return {"veredicto": "fallo", "motivo": evidencia.get("motivo", "verificación no pasó"),
            "evidencia": evidencia}


def runner_oraculo_id(candidata):
    """Returns the frozen oraculo_id registered in the candidate, or None."""
    return candidata.get("_oraculo_id")


def _evidencia_autorraiz_eq(evidencia, autor):
    """True if some evidence declares root author == author (unless it is non-LLM).

    Checks both the top-level evidence dict (where `autor_raiz` may come
    directly) and its nested values."""
    if isinstance(evidencia, dict):
        if evidencia.get("autor_raiz") == autor:
            return True
        for v in evidencia.values():
            if isinstance(v, dict) and v.get("autor_raiz") == autor:
                return True
            if isinstance(v, (list, tuple)):
                for item in v:
                    if isinstance(item, dict) and item.get("autor_raiz") == autor:
                        return True
    return False


def _verificador_default(candidata, oraculo):
    """NON-LLM verifier that EXECUTES the candidate against the oracle's cases.

    Contract fixed after Fable review (anti-tautology, P0-2):
      - The candidate brings `fn` (deterministic callable, with no network/fs/memory access).
      - The oracle brings `casos`: list of {id, inputs, esperado, tolerancia}. The expected
        values live in the oracle (governed memory), NOT in the candidate.
      - The verifier calls `fn(inputs)` and compares against the oracle's `esperado`.

    It does NOT believe in `obtenido`/`esperado` self-declared by the candidate: that was the
    tautological P0. Nor does it believe in an `esperado_de` string: the provenance of the
    expected values is the fact of being in the oracle.
    """
    fn = candidata.get("fn")
    if not callable(fn):
        return False, {"motivo": "candidata sin 'fn' callable: no hay nada que ejecutar",
                       "prueba_verificador_no_llm": True}
    casos = oraculo.get("casos") or []
    if not casos:
        return False, {"motivo": "oráculo sin casos: no hay verdad con qué comparar",
                       "prueba_verificador_no_llm": True}

    import math as _math
    detalle = []
    for c in casos:
        cid = c.get("id", "?")
        inputs = c.get("inputs", {})
        esperado = c.get("esperado")
        tol = c.get("tolerancia", 1e-9)
        # close degenerate values (Fable P1)
        try:
            tval = float(tol)
        except (TypeError, ValueError):
            return False, {"motivo": f"caso '{cid}': tolerancia no numérica",
                           "prueba_verificador_no_llm": True}
        if not _math.isfinite(tval) or tval < 0:
            return False, {"motivo": f"caso '{cid}': tolerancia no finita/negativa",
                           "prueba_verificador_no_llm": True}
        if esperado is None:
            return False, {"motivo": f"caso '{cid}': sin esperado en el oráculo",
                           "prueba_verificador_no_llm": True}
        try:
            obtenido = fn(inputs)
        except Exception as e:
            detalle.append(f"{cid}: excepción {e!r}")
            return False, {"motivo": "casos fallan: " + "; ".join(detalle),
                           "prueba_verificador_no_llm": True}
        # compare by output (dict) or scalar
        if isinstance(esperado, dict):
            for ok_name, want in esperado.items():
                got = obtenido.get(ok_name) if isinstance(obtenido, dict) else None
                if not _comparar_valor(got, want, tval, _math):
                    detalle.append(f"{cid}.{ok_name}: {got} vs {want}")
                    return False, {"motivo": "casos fallan: " + "; ".join(detalle),
                                   "prueba_verificador_no_llm": True}
        else:
            if not _comparar_valor(obtenido, esperado, tval, _math):
                detalle.append(f"{cid}: {obtenido} vs {esperado}")
                return False, {"motivo": "casos fallan: " + "; ".join(detalle),
                               "prueba_verificador_no_llm": True}

    return True, {"motivo": "ok", "prueba_verificador_no_llm": True,
                  "n_casos": len(casos)}


def _comparar_valor(got, want, tol, math):
    """Compares got vs want with absolute tolerance, rejecting non-finite values."""
    try:
        g = float(got)
        w = float(want)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(g) or not math.isfinite(w):
        return False
    return not (abs(g - w) > tol)


# ─────────────────────────────────────────────────────────────────────────────
# 6. ratificar_mecanicamente  (writes verification record, does not touch author)
# ─────────────────────────────────────────────────────────────────────────────
def ratificar_mecanicamente(m, candidata, veredicto, oraculo_id, fuente_refs=None):
    """Writes the `verificacion:<did>:<hash_candidata>` record ONLY if the verdict was
    'verificada_mec'. Anti-P0 gate (Fable: 'ratification without a verdict must reject').

    The fact ALWAYS stays unverified: a MECHANICAL ratification is not canonicity.
    Canonicity is given only by the human (registrar_spec). If there are governed sources that
    back the oracle, they are linked via `derived_from` (real key->version, never None).

    Returns (clave, version) or None if it rejects.
    """
    if not isinstance(veredicto, dict) or veredicto.get("veredicto") != VERIFICADA_MEC:
        return None  # rejects: it is not ratified without a real verification verdict
    nombre = candidata.get("nombre", "dominio")
    from generar_dominio_candidato import _normalizar_id as _nid
    did = _nid(nombre)
    hash_cand = _hash(candidata)
    # link ONLY real governed sources (key->version); never invent versions
    derived_from = {}
    for ref_key, ref_ver in (fuente_refs or {}).items():
        if ref_ver is not None:
            derived_from[ref_key] = ref_ver
    clave = "verificacion:" + did + ":" + hash_cand
    v = m.write(clave, {
        "veredicto": "verificada_mec",
        "evidencia": veredicto.get("evidencia"),
        "oraculo_id": oraculo_id,
        "autor_candidata": candidata.get("autor"),
    }, unit="", fuente="calculadora", verified=False, derived_from=derived_from)
    return clave, v


# ─────────────────────────────────────────────────────────────────────────────
# 7. ciclo_reintentos  (orchestrates attempts and cutoffs)
# ─────────────────────────────────────────────────────────────────────────────
def ciclo_reintentos(m, dominio, artefactos, n_max,
                     consentimiento=None, runner=None, progreso=None, casos=None):
    """Orchestrates the ratification cycle with retries and cutoffs.

    Returns a dict with the final state, the candidate, the epistemic label, and the cutoff
    reason. Stops and asks in the 4 Fable cases: contingent/normative datum without source,
    cycle needs to touch the oracle, N exhausted, or deducible cannot be proven.

    `consentimiento` (callable)(motivo, pregunta) -> bool: authorizes retrying; if None,
    the cycle does not retry (safe stop). `progreso` is a list of attempts.
    `casos` are the oracle's TRUTH cases (inputs+expected), which live outside the
    candidate; if there are none, the verifier fails with 'oracle without cases' (no tautology).
    """
    progreso = progreso if progreso is not None else []
    # 1. classify (reading the HCMA's ratified premises, not the candidate's)
    candidata = generar_candidata(dominio)
    clases = clasificar_afirmaciones(candidata, m=m)

    # 2. resolve the oracle for the classes that require it
    artefactos_resueltos = dict(artefactos or {})
    # aid -> declared fuente_id map (to look up the correct artifact)
    afirmaciones = candidata.get("afirmaciones", {})
    faltan_fuente = []
    for aid, clase in clases.items():
        if clase in (CLASE_DATO_CONTINGENTE, CLASE_NORMATIVO, CLASE_MODELO_EMPIRICO):
            fuente_id = _fuente_id_de(afirmaciones, aid)
            art = resolver_oraculo(m, {"name": aid, "fuente_id": fuente_id})
            if art is None:
                faltan_fuente.append(fuente_id or aid)
            else:
                artefactos_resueltos[fuente_id or aid] = art

    # 3. if the oracle is missing for something non-deducible => BLOCKED (stop and ask)
    if faltan_fuente:
        return _final(BLOQUEADA_SIN_ORACULO, candidata,
                      "provisional",
                      f"falta fuente pre-registrada para: {', '.join(faltan_fuente)}",
                      faltan_fuente, progreso)

    # 4. freeze the oracle (hash before attempt 1)
    oraculo = congelar_oraculo(dominio, artefactos_resueltos, casos=casos)
    candidata["_oraculo_id"] = oraculo["oraculo_id"]

    # 5. retry loop (only mutates the candidate)
    n = 0
    while n < (n_max or 1):
        n += 1
        progreso.append({"intento": n,
                         "oraculo_id": oraculo["oraculo_id"]})
        veredicto = verificar(candidata, oraculo, runner=runner)
        progreso[-1]["veredicto"] = veredicto["veredicto"]
        if veredicto["veredicto"] == "verificada_mec":
            ratificar_mecanicamente(m, candidata, veredicto,
                                    oraculo["oraculo_id"])
            return _final(VERIFICADA_MEC, candidata, "verificado",
                          "verificado contra oráculo congelado", [], progreso,
                          veredicto["evidencia"])
        # failure/self-approval: early cutoff if the same failure repeats
        motivo = veredicto.get("motivo")
        if len(progreso) >= 2 and progreso[-2].get("motivo") == motivo and n > 1:
            break
        progreso[-1]["motivo"] = motivo
        # do we retry? requires consent (safe stop if none)
        if not _pide_reintento(consentimiento, motivo):
            break

    # determine cutoff
    ultimo = progreso[-1] if progreso else {}
    if ultimo.get("veredicto") in ("fallo", "bloqueado_sin_oraculo") and n >= (n_max or 1):
        return _final(AGOTADA, candidata, "provisional",
                      "agotados los reintentos sin verificar", [], progreso)
    if faltan_fuente:
        return _final(BLOQUEADA_SIN_ORACULO, candidata, "provisional",
                      "falta fuente pre-registrada (parar y preguntar)", faltan_fuente, progreso)
    return _final(AGOTADA, candidata, "provisional",
                  f"corte anticipado en intento {n}", [], progreso)


def _pide_reintento(consentimiento, motivo):
    if consentimiento is None:
        return False
    try:
        return bool(consentimiento(motivo))
    except Exception:
        return False


def _fuente_id_de(afirmaciones, aid):
    """Returns the declared fuente_id of an assertion, or the aid itself."""
    a = (afirmaciones or {}).get(aid)
    if isinstance(a, dict) and a.get("fuente_id"):
        return a["fuente_id"]
    return aid


def _final(estado, candidata, etiqueta, motivo, faltan, progreso, evidencia=None):
    return {"estado": estado, "candidata": candidata, "etiqueta_epistemica": etiqueta,
            "motivo": motivo, "faltan_fuente": faltan, "progreso": progreso,
            "evidencia": evidencia}


# ─────────────────────────────────────────────────────────────────────────────
# 8. entregar  (with mandatory epistemic label)
# ─────────────────────────────────────────────────────────────────────────────
def entregar(resultado_ratificacion):
    """Returns the deliverable to the user, ALWAYS with an epistemic label (I3)."""
    cand = resultado_ratificacion.get("candidata", {})
    estado = resultado_ratificacion["estado"]
    # the draft is NOT shown, but the epistemic state travels
    entregable = {k: v for k, v in cand.items() if not k.startswith("_")}
    label = resultado_ratificacion.get("etiqueta_epistemica", "provisional")
    etiquetas = {
        VERIFICADA_MEC: "verificado contra oráculo congelado (mecánico)",
        PROVISIONAL: "provisional (pendiente de fuente/dato)",
        BLOQUEADA_SIN_ORACULO: "bloqueado: falta fuente pre-registrada",
        AGOTADA: "agotado: reintentos sin verificar",
        RATIFICADA_HUMANA: "verificado por humano",
    }
    texto = etiquetas.get(estado, label)
    topo = entregable.pop("topologia", None)
    if resultado_ratificacion.get("faltan_fuente"):
        entregable["falta_fuente"] = resultado_ratificacion["faltan_fuente"]
    return {
        "entregable": entregable,
        "etiqueta_epistemica": texto,
        "estado": estado,
        "grado_topologico": topo or T.ASUMIDA,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 9. escalar  (takes the question to the human)
# ─────────────────────────────────────────────────────────────────────────────
def escalar(motivo, pregunta_concreta):
    """Registers an escalation to the human. Never self-resolves."""
    return {"tipo": "escalacion", "motivo": motivo, "pregunta": pregunta_concreta,
            "resuelta_por": None}


# ─────────────────────────────────────────────────────────────────────────────
# 10. invalidar_por_oraculo  (retracts everything that depended on the fallen oracle)
# ─────────────────────────────────────────────────────────────────────────────
def invalidar_por_oraculo(m, oraculo_id):
    """If the source falls, the TMS retracts everything that depended on it.

    Iterates the `verificacion:*` facts and those deriving from the fallen `fuente:*`; the TMS already
    propagates transitive invalidation via `_invalidate_descendants`. Here we return which
    verifications lost support for auditing.
    """
    caidos = []
    for k in m.keys("verificacion:"):
        f = m.current(k)
        if f is None:
            continue
        df = f.get("derived_from") or {}
        # if it depends on an artifact of the fallen oracle, we mark it
        for dk in df:
            if dk.startswith((ORACULO_PREFIX, REGISTRO_PREFIX)):
                # invalidate the source if it is the oracle's
                src = m.current(dk)
                if src is not None and _hash(src["value"]) == oraculo_id:
                    caidos.append(k)
    return caidos


__all__ = [
    "DESCONOCIDO", "CANDIDATA", "ORACULO_CONGELADO", "EN_VERIFICACION",
    "VERIFICADA_MEC", "PROVISIONAL", "BLOQUEADA_SIN_ORACULO", "AGOTADA",
    "RATIFICADA_HUMANA", "ESTADOS", "CLASES",
    "CLASE_DEFINICIONAL", "CLASE_MODELO_EMPIRICO", "CLASE_DATO_CONTINGENTE", "CLASE_NORMATIVO",
    "clasificar_afirmaciones", "resolver_oraculo", "congelar_oraculo",
    "generar_candidata", "verificar", "ratificar_mecanicamente",
    "ciclo_reintentos", "entregar", "escalar", "invalidar_por_oraculo",
    "FUENTE_CONFIABLE",
]
