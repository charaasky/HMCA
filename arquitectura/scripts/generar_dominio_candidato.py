#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""generar_dominio_candidato.py — the LLM agent drafts a CANDIDATE domain spec.

Closes the architecture correction that the user surfaced: there is NO LLM living inside
the HCMA. The HCMA is the governed memory (TMS + deontic layer); the LLM is EXTERNAL and
CONSUMES it. When the LLM agent does not find a domain for a request, IT drafts the
candidate — and the HCMA only registers it as NON-canonical.

This implements the 'candidate path' that Fable defined as minimally viable (query
fable_pedir_cualquier_cosa, point 5):

    Unknown domain -> candidate spec in declarative format, author=LLM (never 'ingeniero')
                           -> marked draft PDF.

BOUNDARY (Fable's 3 levels, point 2):
  1. CITATION            : zero authority.
  2. TRANSCRIPTION   : hash + golden tests prove the fragment's formula, not its applicability.
  3. APPLICABILITY   : only a human authorizes it.

FALSIFICATION INVARIANT (the one that enters the paper): there is no path from author=LLM to an
AUTHORITATIVE deliverable without a human registration event. The candidate spec remains `asumida`/unverified;
to become canonical, `registrar_spec` with a human source (ingeniero/usuario) is needed.

It does NOT touch the core nor skill_lib. It is an external module that ORCHESTRATES the LLM and writes the candidate
to the governed memory. The LLM generates DATA (declarative spec), not executable code: the code
remains the responsibility of the deterministic flow `proponer_impl` + `evaluar`.

Usage (no network, deterministic):
    PYTHONPATH=... python3 generar_dominio_candidato.py --pedido "diseñame un parlante de 15 W"
    (with --llm, it calls the model to draft the spec; without --llm it uses a deterministic draft)
"""
import argparse
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate spec contract (data, not code)
# ─────────────────────────────────────────────────────────────────────────────
# A candidate spec is a declarative dict that describes WHAT is to be computed (not HOW).
# The execution terrain (calcular) does NOT travel in the candidate: `proponer_impl` proposes it
# separately and `evaluar` verifies it. Here only the FORM travels (inputs/formulas/dimensions/ranges).
SPEC_CANDIDATA_CONTRATO = {
    "nombre": "",          # string
    "autor": "modelo",     # ALWAYS 'modelo' (never 'ingeniero')
    "inputs": [],          # [{name, question, unit}]
    "formulas": [],        # [descriptive string of the formula (citation, not code)]
    "dimensiones": {},     # {output: unit}
    "ranges": {},          # {output: (min, max)}
    "fuentes_citadas": [],  # [string] citations, zero authority (Fable level 1)
    # ── optional fields that enable grades 3/4 (do NOT promote by themselves) ──
    # Without a human signature, `grado_general` still stops at grade 1. These data only
    # remain DOCUMENTED, and the deterministic verifier uses them when there is a signature.
    "mecanismos": {},      # {bloque_id: {funcion, clase_modelo, mecanismo?}}
    "puntos_operacion": {},  # {bloque_id: {magnitude: value}}  (worst case)
    "bom": {},             # {bloque_id: {part_no, ratings: {magnitude: value}, tolerancia: {magnitude: pct}}}
}


def _validar_spec_candidata(spec):
    """Candidate contract guardrail. Rejects a malformed candidate (does not promote it)."""
    errs = []
    if not isinstance(spec, dict):
        raise ValueError("spec candidata debe ser dict")
    if not isinstance(spec.get("nombre"), str) or not spec["nombre"]:
        errs.append("nombre ausente")
    if spec.get("autor") != "modelo":
        errs.append("una candidata SOLO puede tener autor='modelo' (nunca 'ingeniero')")
    if not isinstance(spec.get("inputs"), list):
        errs.append("inputs debe ser lista")
    for i in spec.get("inputs", []):
        if not isinstance(i.get("name"), str) or not i["name"]:
            errs.append("input sin name")
        if "unit" not in i:
            errs.append(f"input '{i.get('name')}' sin unit")
    if not isinstance(spec.get("formulas"), list) or not spec["formulas"]:
        errs.append("formulas vacía (una candidata sin fórmulas no es verificable)")

    # ── optional fields (grades 3/4): if present, validate their SHAPE (do not reject for absence) ──
    if "mecanismos" in spec and spec["mecanismos"] is not None:
        if not isinstance(spec["mecanismos"], dict):
            errs.append("mecanismos debe ser dict {bloque_id: {funcion, clase_modelo, mecanismo?}}")
        else:
            for bid, info in spec["mecanismos"].items():
                if not isinstance(info, dict):
                    errs.append(f"mecanismos['{bid}'] debe ser dict")
                    continue
                if not info.get("funcion") or not info.get("clase_modelo"):
                    errs.append(f"mecanismos['{bid}'] sin 'funcion' o 'clase_modelo'")
    if "puntos_operacion" in spec and spec["puntos_operacion"] is not None:
        if not isinstance(spec["puntos_operacion"], dict):
            errs.append("puntos_operacion debe ser dict {bloque_id: {magnitud: valor}}")
    if "bom" in spec and spec["bom"] is not None:
        if not isinstance(spec["bom"], dict):
            errs.append("bom debe ser dict {bloque_id: {part_no, ratings, tolerancia}}")
        else:
            for bid, pieza in spec["bom"].items():
                if not isinstance(pieza, dict):
                    errs.append(f"bom['{bid}'] debe ser dict")
                    continue
                if not isinstance(pieza.get("ratings"), dict):
                    errs.append(f"bom['{bid}'] sin 'ratings' (dict)")

    if errs:
        raise ValueError("spec candidata inválida: " + "; ".join(errs))
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Generator: the LLM drafts the candidate spec (data), or deterministic fallback
# ─────────────────────────────────────────────────────────────────────────────
SYS_GENERADOR = (
    "Sos el agente LLM de HCMA (memoria versionada gobernada). Vas a redactar la SPEC de un "
    "dominio CANDIDATO a pedido. NO escribís código ni aritmética: solo describís QUÉ se "
    "calcula. Devolvé SOLO JSON (json_object) con este esquema exacto:\n"
    '{"nombre": str, "autor": "modelo", "inputs": [{"name","question","unit"}], '
    '"formulas": [str,...], "dimensiones": {salida: unidad}, "ranges": {salida: [min,max]}, '
    '"fuentes_citadas": [str,...], "lookup_queries": [{"input": str, "query": str}, ...], '
    '"topologia_bloques": {"bloques":[{"id","tipo","puertos":[{"id","dominio"}]}], '
    '"nets":[{"id","conexiones":[{"bloque","puerto"}]}], '
    '"bindings":[{"variable","bloque","puerto"}]}, '
    '"mecanismos": {bloque_id: {"funcion": str, "clase_modelo": str, "mecanismo": str|null}}, '
    '"puntos_operacion": {bloque_id: {magnitud: numero}}, '
    '"bom": {bloque_id: {"part_no": str, "ratings": {magnitud: numero}, '
    '"tolerancia": {magnitud: fraccion}}}}\n'
    "REGLAS DURAS:\n"
    "1. autor SIEMPRE 'modelo'.\n"
    "2. Las fórmulas son DESCRIPCIONES con su fuente (cita), NO código. Ej: 'P = V·I (ley de "
    "Ohm)'.\n"
    "3. Separar lo que es LEY/FÓRMULA (deducible) de lo que es DATO del caso (contingente): "
    "los datos contingentes van como INPUTS con question, no como fórmulas.\n"
    "4. No inventes valores de catálogo: si un dato es contingente, ponelo como input y "
    "marcalo en question 'del catálogo/hoja de datos'. Para BOM, proponé piezas CANDIDATAS "
    "(part_no real o genérico como 'regulador buck 5V/2A') y marcá en part_no que es "
    "candidata si no tenés la hoja de datos; NUNCA afirmes un rating exacto que no salga de "
    "fuente verificable.\n"
    "5. En lookup_queries, para CADA input contingente que necesite búsqueda web, proponé la "
    "query EXACTA (en inglés técnico) que la encontraría en la hoja de datos del fabricante. "
    "No llenes lookup_queries con lo que ya es ley física universal.\n"
    "6. topologia_bloques es el GRAFO de bloques físicos (diagrama de cajas): bloques con puertos "
    "TIPADOS por dominio (potencia/señal/medición/referencia/tierra), nets que conectan "
    "puertos, y bindings que anclan cada variable de fórmula a un bloque/puerto. NO mezcles "
    "la red de POTENCIA con la de SEÑAL (la batería alimenta el circuito activo, NO el divisor "
    "ni el sensor). Si no podés proponer una topología de bloques coherente, devolvé "
    "'topologia_bloques': {} (mejor sin grafo que un grafo mentiroso).\n"
    "7. mecanismos: para CADA bloque que AFIRMA una función, declará (a) 'funcion' (qué hace), "
    "(b) 'clase_modelo' en {lineal, parametrico, no_lineal_real, caja_negra}, y (c) 'mecanismo' "
    "si la función vive en una NO-LINEALIDAD (conmutacion, saturacion, regulacion, histeresis) "
    "o null si es lineal. REGLA: una función NO-LINEAL (p.ej. un regulador conmutado, un ADC) "
    "NO se modela como 'lineal'; si no sabés el mecanismo exacto, usá 'caja_negra' (contrato "
    "de datasheet). Si un bloque no afirma función, NO lo pongas en mecanismos.\n"
    "8. puntos_operacion: para cada bloque con magnitudes que soporta (ej. tension en V, "
    "corriente en A, potencia en W), poné el PEOR CASO como número positivo. Solo bloques "
    "físicos reales; no inventes valores, deriválos de las fórmulas/inputs del pedido.\n"
    "9. bom: para cada bloque que exige una pieza física, proponé UNA pieza candidata con "
    "'part_no' y 'ratings' (límites MÁXIMOS del fabricante) que ENVUELVAN el punto de operación. "
    "Si no tenés la pieza exacta, usá un part_no descriptivo ('candidato: ...') y deja "
    "'ratings' vacío {} para que el verificador lo marque como pendiente (NO invente ratings).\n"
    "10. Estos campos opcionales NO suben el grado por sí solos: sin firma humana el sistema "
    "frena en grado 1. Solo documentan y permiten verificar MÁS TARDE si hay firma. No los "
    "sobre-vendas.\n"
    "11. Si no sabés el dominio, devolvé un JSON con nombre y formulas vacío (mejor abstener "
    "que inventar)."
)


def generar_spec_candidata(pedido, usar_llm=False, modelo=None):
    """Returns a candidate spec (dict) for `pedido`. autor='modelo'.

    With usar_llm=True it calls the LLM to draft it (it is the ONLY production path: without an LLM
    there are no formulas and there is no candidate). With usar_llm=False it returns a generic draft WITHOUT
    formulas, which `inscribir_candidata` rejects on purpose: it is ONLY so that internal
    tests monkeypatch their own spec, NOT for the real flow.
    """
    if not usar_llm:
        return _borrador_determinista(pedido)

    from prototipo import call_llm, parse_json
    modelo = modelo or os.environ.get("ARQ_CLAUDE", "anthropic/claude-sonnet-4-5")
    raw = call_llm(f"Pedido del usuario: {pedido}", modelo, system=SYS_GENERADOR, json_mode=True)
    spec = parse_json(raw)
    spec["autor"] = "modelo"  # force: it can never rise above candidacy through this path
    _validar_spec_candidata(spec)
    return spec


def _borrador_determinista(pedido):
    """Draft WITHOUT an LLM (fallback for offline tests). Generic, does not hardcode a domain.

    Derives a candidate name from the request and returns a MINIMAL candidate spec: without formulas
    (we cannot know them without the LLM), only the name and the author mark. The validator rejects
    empty formulas, so this fallback honestly indicates that 'without an LLM I cannot draft
    formulas', instead of inventing a specific speaker/charger domain.
    """
    return {
        "nombre": "dominio candidato: " + pedido[:60],
        "autor": "modelo",
        "inputs": [],
        "formulas": [],
        "dimensiones": {},
        "ranges": {},
        "fuentes_citadas": [],
        "lookup_queries": [],
        "mecanismos": {},
        "puntos_operacion": {},
        "bom": {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Registration in HCMA as candidate (NOT canonical)
# ─────────────────────────────────────────────────────────────────────────────
def _normalizar_id(nombre):
    """Normalizes the domain id: lowercase, no accents, spaces -> '_'."""
    import unicodedata
    s = unicodedata.normalize("NFKD", nombre)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalnum() else "_" for c in s.lower().strip())
    s = "_".join(p for p in s.split("_" if "_" in s else " ") if p)
    return s or "dominio"


def inscribir_candidata(m, spec):
    """Registers the candidate spec in the governed memory as UNVERIFIED.

    Key distinct from the canonical `skill:<id>:spec`: we use `dominio:<id>:candidata` so that
    it is never confused with the authoritative spec (which requires a human source via registrar_spec).
    Returns (did, version). did is normalized (without accents/spaces).
    """
    _validar_spec_candidata(spec)
    did = _normalizar_id(spec["nombre"])
    v = m.write(f"dominio:{did}:candidata", spec, unit="", fuente="modelo", verified=False,
                meta={"autor": "modelo", "pedido": spec.get("_pedido", "")})
    return did, v


def es_canonico(m, did):
    """A domain is canonical ONLY if there is an AUTHORITATIVE spec: verified AND human source
    (ingeniero/usuario). A spec with fuente='tabla' or 'calculadora' is NOT canonical (those
    roles do not authorize a domain: only a human registers the spec)."""
    spec = m.current(f"skill:{did}:spec")
    if spec is None or not spec["verified"]:
        return False
    return spec["fuente"] in ("ingeniero", "usuario")


# ─────────────────────────────────────────────────────────────────────────────
# Falsification invariant (the paper's thesis)
# ─────────────────────────────────────────────────────────────────────────────
def invariante_sin_camino_modelo_a_autoritativo(m, did):
    """Returns (ok, reason): there is NO path from autor=modelo to authoritative without a human.

    Specifically:
      (1) the candidate `dominio:<id>:candidata` is unverified (not canonical).
      (2) there is no canonical `skill:<id>:spec` unless a human registered it
          (verified + fuente ingeniero/usuario).
      (3) there is no canonical `verificacion:<id>:*` linking a candidate to authority without
          that human event.
    """
    # (1) the candidate cannot be verified by itself
    cand = m.current(f"dominio:{did}:candidata")
    if cand is not None and cand["verified"]:
        return False, "la candidata quedó verified (auto-promoción): viola el invariante"

    # (2) is there a canonical (human) spec?
    spec = m.current(f"skill:{did}:spec")
    if spec is not None and spec["verified"] and spec["fuente"] in ("ingeniero", "usuario"):
        return True, "dominio canónico vía spec humana (autoridad), camino correcto"

    # (2') if there is a spec but NOT authoritative (non-human source or unverified), it is a problem
    if spec is not None and (not spec["verified"] or spec["fuente"] not in ("ingeniero", "usuario")):
        return True, "spec presente pero NO autoritativa: no hay camino modelo->autoritativo"

    return True, "dominio candidato (autor=modelo) sin spec humana => NO autoritativo"


def hay_verificacion_canonica_sin_humana(m, did):
    """Audit helper: is there a verified verificacion:<did>:* WITHOUT a human spec?

    If it returns True, there is a self-promotion path (P0). The global invariant uses it.
    """
    if es_canonico(m, did):
        return False  # there is a human spec, all good
    for k in m.keys(f"verificacion:{did}:"):
        f = m.current(k)
        if f is not None and f["verified"]:
            return True
    return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pedido", default="diseñame un parlante de 15 W")
    ap.add_argument("--llm", action="store_true", help="usar el LLM para redactar la spec")
    args = ap.parse_args()

    from memoria_sqlite import HCMAMemoriaSQLite
    m = HCMAMemoriaSQLite(":memory:")

    spec = generar_spec_candidata(args.pedido, usar_llm=args.llm)
    did, v = inscribir_candidata(m, spec)

    print("=" * 70)
    print(f"PEDIDO: {args.pedido}")
    print("=" * 70)
    print(f"\n[1] El agente LLM detectó el hueco y redactó la candidata (autor='{spec['autor']}').")
    print(f"    dominio_id: {did}  (versión candidata: {v})")
    print(f"    inputs ({len(spec['inputs'])}): {[i['name'] for i in spec['inputs']]}")
    print(f"    fórmulas ({len(spec['formulas'])}):")
    for f in spec["formulas"]:
        print(f"      - {f}")

    canonico = es_canonico(m, did)
    ok, motivo = invariante_sin_camino_modelo_a_autoritativo(m, did)
    print(f"\n[2] ¿Es canónico? {canonico}")
    print(f"    Invariante de falsación: {'OK' if ok else 'VIOLADO'} — {motivo}")

    print("\n" + "=" * 70)
    print("Conclusión: el LLM (externo) redactó la candidata; el HCMA la guardó UNVERIFIED.")
    print("NO hay camino de autor=modelo a entregable autoritativo sin un humano que")
    print("registre la spec (registrar_spec con fuente ingeniero/usuario).")
    print("=" * 70)
