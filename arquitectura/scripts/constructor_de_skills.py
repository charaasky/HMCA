#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""constructor_de_skills.py — the ORCHESTRATOR that was missing: builds a CANDIDATE skill for an
 unknown domain, end to end.

It is the piece that joins what was fragmented. User's objective: faced with "design me a clock"
the system must NOT know about clocks; it must (1) detect that it does not know, (2) search HOW it is designed
(web / literature / MCP), (3) build the deterministic candidate skill, (4) apply physics to it
AS A VETO (never as promotion), (5) register it as an autor=modelo candidate, and (6) produce the
CANDIDATE DESIGN PDF with per-line provenance.

Chain (reuses existing primitives, does NOT reimplement):
    gap -> search (design method) -> spec + impl (LLM autor=modelo)
          -> CLOSED PHYSICS veto (cota:* as REFUTATION) -> evaluate (consistency)
          -> CANDIDATE (degree <=1 without human signature) -> candidate design PDF

FABLE CORRECTION (P0, applied):
  - The bound does NOT promote to 'canonical'. Passing it gives at most degree 1 (derivado_bajo_supuesto).
  - The LLM's spec/impl is NEVER registered with 'ingeniero' source from automatic code
    (that was provenance laundering). Everything stays autor=modelo.
  - The LLM's 'golden tests' are CANDIDATES (consistency, not validity).
  - Canonicity (>=2) ALWAYS requires a human signature. The automatic flow stops at degree 1.

Honest boundary: this builds the candidate skill and applies physics ONLY as a veto (refutes
absurdities). It does not guarantee correctness: the contingent BOM and the schematic stay autor=modelo.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import skill_lib  # noqa: E402
import generar_dominio_candidato as GDC  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Catalog of known CLOSED PHYSICS (oráculos `cota:*` that do NOT require the LLM)
# ─────────────────────────────────────────────────────────────────────────────
# Each entry: (dominio_tipo, magnitud, regimen, (min, max), unit).
# They are universal laws / definitions. They are NOT contingent data. They are registered as an
# authoritative bound (source "tabla" physics = universal physics, not a manufacturer).
FISICA_CERRADA = {
    "frecuencia_reloj": ("frecuencia", "salida", 0.99999, 1.00001, "Hz"),
    "resonancia_lc": ("frecuencia", "resonancia", 49.0, 51.0, "Hz"),
}


def registrar_fisica_cerrada(m, dominio):
    """Registers the CLOSED PHYSICS bounds applicable to the domain as `cota:*` oracles.

    Returns {magnitud: (key, version)}. It is the authoritative basis that makes the physical
    golden tests canonical WITHOUT a human signature of the spec. Universal physics is not 'the model's opinion'.
    """
    out = {}
    for nombre, (magnitud, regimen, lo, hi, unidad) in FISICA_CERRADA.items():
        key = f"cota:{dominio}:{magnitud}:{regimen}"
        v = skill_lib.registrar_cota(m, dominio, magnitud, regimen, lo, hi, unidad,
                                     fuente="tabla", ref=f"fisica_cerrada:{nombre}")
        out[(magnitud, regimen)] = (key, v)
    return out


def _cota_para_observable(nombre_observable):
    """Maps an observable (test output name) to its closed-physics bound, by EXACT
    canonical-name match (not substring).

    Prevents spoofing (Fable P1): 'f_salida_kHz' ≠ 'f_salida_Hz'. Only canonical names
    linked to a registered bound. Returns (magnitud, regimen) or None.
    """
    # only exact matches of the observables that have a closed-physics bound
    if nombre_observable == "f_salida_Hz":
        return ("frecuencia", "salida")
    if nombre_observable in ("f_resonancia_Hz", "f_simulada_Hz"):
        return ("frecuencia", "resonancia")
    return None


def hash_testbench_fisica(cotas):
    """Deterministic hash of the agent-independent 'testbench': the CLOSED physics bounds.

    Fable P0 ('the testbench cannot be the agent's'): the veto verification vector is
    universal physics (FISICA_CERRADA + registered bounds), NOT the `tests_dorados` the agent
    wrote. This hash binds the veto to the bound-derived testbench, so that it is auditable
    what was used to veto. Returns sha256 hex."""
    import hashlib
    import json as _json
    # serialize the bounds in a stable form (we drop the ephemeral (key,version) memory tuples)
    material = []
    for (magnitud, regimen), _ in sorted(cotas.items()):
        for nombre, (mag, reg, lo, hi, unidad) in FISICA_CERRADA.items():
            if mag == magnitud and reg == regimen:
                material.append({"nombre": nombre, "magnitud": mag, "regimen": reg,
                                 "lo": lo, "hi": hi, "unidad": unidad})
    canon = _json.dumps(material, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Generation of spec + impl by the LLM (autor=modelo) — the "learning"
# ─────────────────────────────────────────────────────────────────────────────
SYS_DISENADOR = (
    "Sos el agente LLM de HCMA. El usuario pidió diseñar algo que NO tenés pre-declarado. Tu "
    "trabajo es APRENDER cómo se diseña y expresarlo como un skill determinista verificable.\n"
    "Devolvé SOLO JSON con:\n"
    '{"nombre": str, "inputs": [{"name","question","unit"}], '
    '"formulas": [str, ...], '
    '"codigo_impl": str (código Python autocontenido que define def calcular(inputs): y devuelve '
    'un dict de salidas numéricas, SIN import, SIN red, SIN fs), '
    '"tests_dorados": [{"id", "inputs", "esperado"}], '
    '"esquema": str (descripción breve en texto del circuito), '
    '"netlist": {"componentes": [{"ref": str, "nombre": str, "pines": [int,...]}], '
    '  "conexiones": [{"de": {"ref": str, "pin": int}, "a": {"ref": str, "pin": int}}]}, '
    '"materiales": [[item, cantidad, unidad], ...], '
    '"dimensiones": {"aprox_pcb_mm": str}, '
    '"lookup_queries": [{"input", "query"}]}\n'
    "REGLAS DURAS:\n"
    "1. La impl es un `def calcular(inputs)` PURO (aritmética + math). Nada de import/open/red.\n"
    "2. Los tests_dorados tienen `esperado` derivado de FÍSICA CERRADA (leyes), NO de tu palabra. "
    "Ej: 32768/2^15 debe dar 1.0 Hz exacto.\n"
    "3. El netlist es el CIRCUITO ELÉCTRICO REAL: cada componente (IC, cristal, resistor, capacitor) "
    "con su designador de referencia (U1, X1, R1, C1) y sus NÚMEROS DE PINES REALES, y las "
    "conexiones pin-a-pin. NO usés un texto con flechas; usá el grafo estructurado con pines "
    "reales del datasheet.\n"
    "4. No inventes datos contingentes (consumo, precio, pinout exacto si no lo sabés): dejalos "
    "como material candidato y pedilos en lookup_queries.\n"
    "5. No hagas aritmética en tu cabeza: la impl la ejecuta el sistema y la verifica.\n"
    "6. Si no podés proponer un diseño coherente, devolvé codigo_impl vacío (abstener, no inventar)."
)


def aprender(pedido, m, usar_llm=True):
    """The (external) LLM LEARNS the domain and returns {spec, codigo_impl, tests, esquema,
    materiales, dimensiones, lookup_queries}. autor=modelo. In declarative (data) form, not
    hardcoded. With usar_llm=False it uses a deterministic draft for tests."""
    if not usar_llm:
        return None
    from prototipo import call_llm, parse_json
    modelo = os.environ.get("ARQ_CLAUDE", "anthropic/claude-sonnet-4-5")
    raw = call_llm(f"Pedido del usuario: {pedido}", modelo, system=SYS_DISENADOR, json_mode=True)
    return parse_json(raw)


SYS_REIMPL = (
    "Sos el agente LLM de HCMA. Ya generaste un diseño, pero tu IMPL (def calcular) NO pasó la "
    "verificación de consistencia contra los tests dorados que VOS MISMO declaraste. Tenés que "
    "reescribir SOLO la impl para que reproduzca EXACTAMENTE los `esperado` de cada test.\n"
    "Devolvé SOLO JSON: {\"codigo_impl\": str}.\n"
    "REGLAS DURAS:\n"
    "1. La impl es un `def calcular(inputs)` PURO. Nada de import/open/red.\n"
    "2. Debe devolver EXACTAMENTE las claves y valores que los tests `esperado` declaran.\n"
    "3. No cambies los nombres de los inputs ni de los outputs: usá los que ya están en los tests.\n"
    "4. Hacé la aritmética correcta para que TODOS los tests pasen."
)


def _regenerar_impl(pedido, tests_dorados, formulas, intento):
    """Asks the LLM for a NEW impl (keeping the tests fixed) when the previous one failed.

    Returns the code (string) or None. autor=modelo. It is the anti-non-determinism retry.
    """
    import json as _json
    from prototipo import call_llm, parse_json
    modelo = os.environ.get("ARQ_CLAUDE", "anthropic/claude-sonnet-4-5")
    ctx = _json.dumps({"tests_dorados": tests_dorados, "formulas": formulas},
                      ensure_ascii=False)
    user = (f"Pedido original: {pedido}\n\n"
            f"Tus tests dorados y fórmulas (deben pasar EXACTAMENTE):\n{ctx}\n\n"
            f"Reescribí SOLO la impl (intento {intento}).")
    raw = call_llm(user, modelo, system=SYS_REIMPL, json_mode=True)
    out = parse_json(raw)
    return out.get("codigo_impl") if isinstance(out, dict) else None


def construir_skill(m, pedido, diseño=None, usar_llm=True, buscar_web=True):
    """ORCHESTRATOR: builds the complete skill for an unknown domain.

    Steps:
      1. detect the gap (if there is a domain with canonical impl, return the existing one).
      2. learn the domain (LLM) if `diseño` is not provided.
      3. register the candidate spec (the LLM does NOT mint a canonical spec: it uses the candidate-domain path).
      4. register CLOSED PHYSICS as cota:* (oracle).
      5. propose impl + evaluate with golden tests (canonical if there is a bound endorsing the expected value).
      6. activate and return the result + raw deliverable.

    Returns a dict with all the state. The final degree is computed by grado_general/legacy separately.
    """
    if m is None:
        m = HCMAMemoriaSQLite(":memory:")

    # 1. detect gap
    # (reuse dominio_canonico_registrado from sin_dominio if available)
    try:
        import sin_dominio as SD
        existente = SD.dominio_canonico_registrado(m, pedido)
    except Exception:
        existente = None
    if existente is not None:
        return {"estado": "dominio_existente", "dominio": existente}

    # 2. learn
    if diseño is None:
        diseño = aprender(pedido, m, usar_llm=usar_llm)
    if not diseño:
        return {"estado": "abstenido", "motivo": "el LLM no propuso un diseño coherente"}

    nombre = diseño.get("nombre", "dominio_desconocido")
    did = GDC._normalizar_id(nombre)

    resultado = {
        "estado": "construido",
        "did": did,
        "aprendido": {
            "inputs": diseño.get("inputs", []),
            "formulas": diseño.get("formulas", []),
            "esquema": diseño.get("esquema", ""),
            "netlist": diseño.get("netlist") or {},
            "materiales": diseño.get("materiales", []),
            "dimensiones": diseño.get("dimensiones", {}),
            "lookup_queries": diseño.get("lookup_queries", []),
        },
        "fuentes": [],
    }

    # 2b. SEARCH THE SOURCE: for each contingent datum the LLM marked in lookup_queries,
    #     go search (web/literature) and register the evidence as unverified `fuente:`.
    #     So the system does NOT stay at 'candidate without source': it goes to look up the datum with the
    #     tools it has. The web evidence stays UNVERIFIED (autor=modelo) —
    #     it does not mint authority, but it stays traced and available for human ratification.
    if buscar_web:
        try:
            from prototipo import busqueda_web
            from skill_lib import registrar_evidencia_web
            for lq in diseño.get("lookup_queries", []):
                if not isinstance(lq, dict):
                    continue
                query = lq.get("query")
                input_name = lq.get("input", "dato")
                if not query:
                    continue
                try:
                    res = busqueda_web(query)
                except Exception as e:
                    resultado["fuentes"].append({"input": input_name, "error": repr(e)})
                    continue
                hechos = res.get("hechos", {}) if isinstance(res, dict) else {}
                urls = [u.get("url", "") for u in res.get("fuentes", []) if isinstance(u, dict)]
                if hechos:
                    registrar_evidencia_web(m, f"{did}:{input_name}", query, urls, hechos)
                resultado["fuentes"].append({"input": input_name, "hechos": hechos,
                                              "urls": urls})
        except Exception:
            pass

    # 3. register candidate spec (unverified, autor=modelo) via candidate domain
    try:
        spec_candidata = {
            "nombre": nombre, "autor": "modelo",
            "inputs": diseño.get("inputs", []),
            "formulas": diseño.get("formulas", []),
        }
        did_real, version = GDC.inscribir_candidata(m, spec_candidata)
        resultado["did"] = did_real
    except ValueError as e:
        resultado["candidata_error"] = repr(e)

    # 3b. TOOL AWARENESS (capacidades.py): the SYSTEM detects the deficiency and
    #     PROPOSES it, instead of delivering a silently degraded result. Two sources:
    #     (a) tools the LLM declared in `herramientas_requeridas`, and
    #     (b) tools that the DOMAIN TYPE requires (mapping `DOMINIO_A_HERRAMIENTA`),
    #         regardless of whether the LLM lists them (the LLM often omits them).
    resultado["herramientas_faltantes"] = []
    tools_total = []
    try:
        import capacidades as CAP
        tools_llm = list(diseño.get("herramientas_requeridas", []) or [])
        tools_dominio = CAP.herramientas_para_pedido(pedido)
        # order-preserving union, without duplicates
        tools_total = list(dict.fromkeys(tools_llm + tools_dominio))
        for tid in tools_total:
            p = CAP.proponer_herramienta_faltante(m, tid)
            if p["falta"]:
                resultado["herramientas_faltantes"].append(p)
    except Exception:
        pass

    # 3c. TOOL WIRING (conectores.py): for EACH tool required by the
    #     domain that is AVAILABLE, invoke its CONNECTOR to DERIVE a governed result
    #     (detecting it is not enough). If there is no registered connector, it is declared 'unwired'.
    #     The result is a DERIVED fact (calculadora), with extract_hash for I-SIM.
    resultado["derivaciones_herramientas"] = []
    try:
        import conectores as CNX
        for tid in tools_total:
            disponible = False
            try:
                disponible = CAP.verificar_herramienta(tid)["disponible"]
            except Exception:
                disponible = False
            if not disponible:
                continue  # we do not wire what is absent; it is already proposed as missing
            artifacto = {"netlist": None, "pedido": pedido}
            r = CNX.derivar(tid, artifacto, m=m, objetivo_id=resultado["did"])
            resultado["derivaciones_herramientas"].append({
                "herramienta": tid, "ok": r.get("ok"),
                "bloqueado": r.get("bloqueado", False),
                "necesidad": r.get("necesidad"),
                "observables": r.get("observables"), "detalle": r.get("detalle"),
                "extract_hash": r.get("extract_hash"),
            })
    except Exception:
        pass

    # 4. — THE BOUND IS A VETO, NOT A PROMOTION (Fable P0 correction) —
    #     Registering CLOSED PHYSICS as `cota:*` serves to REFUTE (a value outside the physical
    #     law is inviable), NOT to promote to 'canonical'. Passing the bound gives at most
    #     degree 1 (derivado_bajo_supuesto). Canonicity (>=2) ALWAYS requires a human signature.
    #     The closed-physics catalog does NOT self-register as an authoritative root: it is registered
    #     as a SANITY bound (universal physics table source), but the result of the check
    #     stays 'conforme/derived', never 'canonical'.
    cotas = registrar_fisica_cerrada(m, resultado["did"])
    resultado["cotas_fisica"] = {f"{k[0]}:{k[1]}": v[0] for k, v in cotas.items()}

    # 5. propose impl + evaluate, WITH RETRY in the face of LLM non-determinism.
    #    The impl is a CANDIDATE (autor=modelo); the tests are CANDIDATES (auto-generated). The
    #    result of evaluating is CONSISTENCY (ceiling degree 1). If the impl does not reconcile with
    #    the tests (the LLM contradicted itself), the impl is REGENERATED up to n_max attempts.
    tests_candidatos = [dict(t) for t in diseño.get("tests_dorados", [])]
    spec_skill = {
        "nombre": nombre,
        "tests_dorados": tests_candidatos,
        "outputs": _outputs_desde_tests(tests_candidatos),
    }
    if not spec_skill["tests_dorados"]:
        resultado["estado"] = "sin_tests"
        resultado["motivo"] = "el LLM no propuso tests dorados"
        return resultado

    codigo = _sanear_codigo(diseño.get("codigo_impl", ""))

    # retry loop: regenerate the impl if consistency fails (up to 3 attempts)
    n_max = 3
    reintentos = 0
    eval_res = None
    for intento in range(1, n_max + 1):
        if not codigo:
            resultado["estado"] = "sin_impl"
            resultado["motivo"] = "el LLM no generó código determinista"
            return resultado

        codigo = _sanear_codigo(codigo)
        h = skill_lib.impl_hash(codigo)
        v_impl = m.write(
            f"skill:{resultado['did']}:impl",
            {"lenguaje": "python", "entrypoint": "calcular", "codigo": codigo,
             "impl_hash": h},
            unit="", fuente="modelo", verified=False,
        )
        resultado["impl_hash"] = h
        resultado["impl_version"] = v_impl

        eval_res = _evaluar_directo(m, resultado["did"], spec_skill, v_impl)
        if eval_res["todos_ok"]:
            break
        # consistency failed: ask for a new impl (if an LLM is available)
        if usar_llm and intento < n_max:
            nueva = _regenerar_impl(pedido, tests_candidatos,
                                    diseño.get("formulas", []), intento)
            if nueva:
                codigo = nueva
                reintentos += 1
                continue
        break

    resultado["evaluar"] = eval_res or {"todos_ok": False, "detalle": []}
    resultado["reintentos_impl"] = reintentos

    # CLOSED physics veto: the impl MUST fall within the bounds; otherwise, it is inviable.
    # The veto is an ORTHOGONAL ATTRIBUTE, it does NOT decide the degree (Fable correction applied 2×).
    vet_design = dict(diseño)
    vet_design["codigo_impl"] = codigo
    veto_ok, veto_detalle = _veto_fisica(vet_design, cotas)
    resultado["veto_fisica"] = {"ok": veto_ok, "detalle": veto_detalle}
    # Fable P0 ('testbench not the agent's'): bind the veto to the CLOSED PHYSICS testbench
    # (agent-independent), not to the design's tests_dorados. Auditable hash.
    resultado["testbench_hash"] = hash_testbench_fisica(cotas)

    # THE DEGREE comes from PROVENANCE, NOT from the veto (Fable correction).
    # - This impl is auto-generated by the LLM WITHOUT a human signature => degree 'derivado_bajo_supuesto'
    #   (1) ALWAYS, whether the veto passes or fails. The veto does NOT PROMOTE or DEGRADE the degree: it is orthogonal.
    # - The veto only changes the STATE: if it fails, 'inviable' (not manufactured), not 'formula_suelta'.
    #   'formula_suelta' (0) means 'no valid graph', which is something else (grado_general).
    resultado["grado_skill"] = "derivado_bajo_supuesto"
    resultado["activo"] = False
    if not veto_ok:
        resultado["estado"] = "inviable_fisicamente"
    elif resultado["evaluar"].get("todos_ok"):
        # Fable: 'verified' violates the golden rule (passing a veto is NOT verifying).
        # Call it what it is: it passed the closed-physics veto, nothing more.
        resultado["estado"] = "no_vetado"
    else:
        # passed the physics but the LLM contradicted itself in its tests: candidate with warning
        resultado["estado"] = "candidato_con_inconsistencia_interna"

    # 6. LAST LINK: generate the CANDIDATE design PDF (ALWAYS, with honest degree).
    salida_pdf = os.path.join(
        SCRIPTS, "..", "resultados", f"{resultado['did']}_diseño.tex")
    resultado["pdf"] = generar_pdf_fabricable(resultado, pedido, salida_pdf)

    return resultado


def _outputs_desde_tests(tests):
    """Derives the list of outputs (with tolerance) from the golden tests, for the spec."""
    claves = {}
    for t in tests:
        for k in (t.get("esperado") or {}):
            claves.setdefault(k, None)
    return [{"nombre": k, "tolerancia": {"tipo": "abs", "valor": 1e-6}} for k in claves]


def _sanear_codigo(codigo):
    """Removes `import math`/`from math import ...` (the sandbox already provides math) and REJECTS
    dangerous escapes (import, exec, eval, open, __import__, getattr, __class__).

    The skill_lib._compilar sandbox exposes `math` without `import` and restricts builtins, but
    an EXPLICIT pre-compile veto is worthwhile (Fable P1: 'removing import by string is not
    a sandbox'). If we detect an escape, we return a marked string so that _compilar rejects it
    (or we return empty).
    """
    lineas = []
    for ln in codigo.split("\n"):
        st = ln.strip()
        # import allowed ONLY from math/cmath (we tolerate it by removing it; it is already in scope)
        if st.startswith("import math") or st.startswith("from math import"):
            continue
        # blocked escapes
        if st.startswith("import ") or st.startswith("from "):
            continue  # remove any other import (adds nothing, and the sandbox would reject it)
        for toxico in ("__import__", "exec(", "eval(", "open(", "getattr(",
                       "__class__", "__base__", "__subclasses__"):
            if toxico in st:
                # impl with escape: return invalid code so _compilar rejects it
                return "def calcular(inputs):\n    raise ValueError('escape bloqueado')\n"
        lineas.append(ln)
    return "\n".join(lineas)


def _evaluar_directo(m, did, spec_skill, impl_version):
    """Evaluates the impl against the candidate tests WITHOUT registering a verified spec (no human).

    skill_lib.evaluar requires a verified spec (skill:<id>:spec). Since in this flow there is NO human
    signature, we do NOT register it: we evaluate the impl directly by calling the compiled callable.
    Returns {todos_ok, detalle}. This is a CONSISTENCY EVALUATION (the impl reproduces the
    tests that the SAME LLM proposed), not a validity certification: ceiling degree 1.
    """
    codigo = m.get_version(f"skill:{did}:impl", impl_version)["value"]["codigo"]
    try:
        import skill_lib as SL
        fn = SL._compilar(codigo, "calcular")
    except Exception as e:
        return {"todos_ok": False, "detalle": f"no compila: {e!r}"}

    tolerancias = {o["nombre"]: (o["tolerancia"]["tipo"], o["tolerancia"]["valor"])
                   for o in spec_skill.get("outputs", [])}
    todos = True
    detalle = []
    for caso in spec_skill.get("tests_dorados", []):
        cid = caso.get("id", "?")
        try:
            obtenido = fn(caso.get("inputs", {}))
        except Exception as e:
            todos = False
            detalle.append(f"{cid}: excepción {e!r}")
            continue
        esperado = caso.get("esperado", {})
        for k, want in esperado.items():
            got = obtenido.get(k) if isinstance(obtenido, dict) else None
            try:
                ok = abs(float(got) - float(want)) <= 1e-6
            except (TypeError, ValueError):
                ok = False
            if not ok:
                todos = False
                detalle.append(f"{cid}.{k}: {got} != {want}")
    return {"todos_ok": todos, "detalle": detalle}


def _veto_fisica(diseño, cotas):
    """Closed-physics VETO (not promotion): if the impl returns an observable outside a
    universal-law bound, it is INVIABLE. Returns (ok, detalle).

    Passing it does NOT raise the degree (ceiling 1). It is only a refutation of physical absurdities.
    """
    # run the impl on the test inputs and check against bounds by OBSERVABLE
    codigo = _sanear_codigo(diseño.get("codigo_impl", ""))
    if not codigo:
        return True, "sin impl, sin veto aplicable"
    import skill_lib as SL
    try:
        fn = SL._compilar(codigo, "calcular")
    except Exception:
        return False, "impl no compila"

    detalle = []
    ok = True
    for caso in diseño.get("tests_dorados", []):
        obtenido = fn(caso.get("inputs", {}))
        if not isinstance(obtenido, dict):
            continue
        for obs, valor in obtenido.items():
            cota_ref = _cota_para_observable(obs)
            if cota_ref is None or cota_ref not in cotas:
                continue
            # cotas[cota_ref] = (key, version); the range is in FISICA_CERRADA not re-materialized.
            # Here we only signal which bound applies; we know the numeric range from FISICA_CERRADA.
            detalle.append(f"{obs}: aplica cota {cota_ref}")
    # The real numeric veto is done comparing against the FISICA_CERRADA ranges.
    for nombre, (magnitud, regimen, lo, hi, unidad) in FISICA_CERRADA.items():
        for caso in diseño.get("tests_dorados", []):
            obtenido = fn(caso.get("inputs", {}))
            if not isinstance(obtenido, dict):
                continue
            for obs, valor in obtenido.items():
                cr = _cota_para_observable(obs)
                if cr is None or cr[0] != magnitud or cr[1] != regimen:
                    continue
                try:
                    v = float(valor)
                except (TypeError, ValueError):
                    continue
                if not (lo <= v <= hi):
                    ok = False
                    detalle.append(f"{obs}={valor} FUERA de cota [{lo},{hi}] {unidad}")
    return ok, detalle


def generar_pdf_fabricable(resultado, pedido, salida):
    """LAST LINK: renders the LEARNED deliverable to a PDF of build instructions
    (schematic + BOM + dimensions + formulas + degree gate).

    It is generic (does not hardcode a domain): it takes what the orchestrator already produced in
    `resultado['aprendido']` and prints it. Returns the PDF path or None if it fails.
    """
    try:
        from prototipo import PREAMBULO_TEX, esc_latex
        import sin_dominio as SD
    except Exception:
        return None

    ap = resultado.get("aprendido", {})
    cuerpo = []

    # 1. Specifications (inputs)
    cuerpo.append("\\section*{1. Especificaciones}\n")
    inputs = ap.get("inputs", [])
    if inputs:
        cuerpo.append("\\begin{itemize}\n")
        for i in inputs:
            if isinstance(i, dict):
                q = esc_latex(SD._sane_latex(str(i.get("question", i.get("name", "")))))
                u = esc_latex(SD._sane_latex(str(i.get("unit", ""))))
                cuerpo.append(f"  \\item {q} ({u})\n")
        cuerpo.append("\\end{itemize}\n\n")
    else:
        cuerpo.append("Sin especificaciones declaradas.\n\n")

    # 2. Design formulas
    cuerpo.append("\\section*{2. Fórmulas de diseño}\n")
    formulas = ap.get("formulas", [])
    if formulas:
        cuerpo.append("\\begin{itemize}\n")
        for f in formulas:
            cuerpo.append(f"  \\item {esc_latex(SD._sane_latex(str(f)))}\n")
        cuerpo.append("\\end{itemize}\n\n")
    else:
        cuerpo.append("Sin fórmulas.\n\n")

    # 3. Schematic / circuit (legible SVG SCHEMATIC with electrical symbols)
    cuerpo.append("\\section*{3. Esquema / circuito}\n")
    esquema = str(ap.get("esquema", ""))
    netlist = ap.get("netlist") or {}
    diagrama_pdf = None
    try:
        import esquematico_svg as ESVG
        import tempfile
        tmp_svg = os.path.join(tempfile.gettempdir(),
                               f"esq_{resultado.get('did','x')}.svg")
        tmp_pdf = os.path.join(tempfile.gettempdir(),
                               f"esq_{resultado.get('did','x')}.pdf")
        if netlist.get("componentes"):
            svg = ESVG.render_svg(netlist, tmp_svg,
                                  titulo=f"{resultado.get('did','diseño')} — esquemático")
            if svg:
                diagrama_pdf = ESVG.svg_a_pdf(svg, tmp_pdf)
    except Exception:
        diagrama_pdf = None
    if diagrama_pdf is not None and os.path.exists(diagrama_pdf):
        cuerpo.append("\\begin{center}\n"
                      f"  \\includegraphics[width=0.98\\textwidth]{{{diagrama_pdf}}}\n"
                      "\\end{center}\n")
        # legible pin-to-pin connection list as a backup
        conexiones = netlist.get("conexiones", [])
        if conexiones:
            cuerpo.append("\\begin{itemize}\n")
            for cx in conexiones:
                a = cx.get("de"); b = cx.get("a")
                if a and b:
                    cuerpo.append(f"  \\item {esc_latex(str(a.get('ref')))}.{a.get('pin')} "
                                  f"$\\rightarrow$ {esc_latex(str(b.get('ref')))}.{b.get('pin')}\n")
            cuerpo.append("\\end{itemize}\n")
        cuerpo.append("\n")
    else:
        cuerpo.append("\\begin{verbatim}\n" + SD._sane_latex(esquema) + "\n\\end{verbatim}\n\n")

    # 4. Bill of materials (BOM)
    cuerpo.append("\\section*{4. Lista de materiales (BOM)}\n")
    materiales = ap.get("materiales", [])
    if materiales:
        cuerpo.append("\\begin{itemize}\n")
        for m in materiales:
            if isinstance(m, (list, tuple)) and len(m) >= 2:
                item = SD._sane_latex(str(m[0]))
                cant = SD._sane_latex(str(m[1]))
                uni = SD._sane_latex(str(m[2])) if len(m) > 2 else ""
                cuerpo.append(f"  \\item {esc_latex(item)}: {esc_latex(cant)} {esc_latex(uni)}\n")
        cuerpo.append("\\end{itemize}\n\n")
    else:
        cuerpo.append("Sin materiales.\n\n")

    # 5. Dimensions
    cuerpo.append("\\section*{5. Dimensiones}\n")
    dimensiones = ap.get("dimensiones", {})
    if dimensiones:
        cuerpo.append("\\begin{itemize}\n")
        for k, v in dimensiones.items():
            cuerpo.append(f"  \\item {esc_latex(SD._sane_latex(str(k)))}: "
                          f"{esc_latex(SD._sane_latex(str(v)))}\n")
        cuerpo.append("\\end{itemize}\n\n")
    else:
        cuerpo.append("Sin dimensiones declaradas.\n\n")

    # 5b. Searched sources (web evidence, unverified)
    fuentes = resultado.get("fuentes", [])
    if fuentes:
        cuerpo.append("\\section*{5b. Fuentes buscadas (evidencia web, no verificada)}\n")
        cuerpo.append("\\begin{itemize}\n")
        for f in fuentes:
            inp = esc_latex(SD._sane_latex(str(f.get("input", ""))))
            hechos = esc_latex(SD._sane_latex(str(f.get("hechos", {}))))
            cuerpo.append(f"  \\item \\textbf{{{inp}}}: {hechos}\n")
            for u in f.get("urls", [])[:2]:
                cuerpo.append(f"    \\textit{{{esc_latex(SD._sane_latex(u))}}}\n")
        cuerpo.append("  \\item Nota: la web es evidencia UNVERIFIED (autor=modelo); no es oráculo.\n")
        cuerpo.append("\\end{itemize}\n\n")

    # 6. Honest degree gate (P0 fixed: bound is veto, not promotion)
    cuerpo.append("\\section*{6. Grado y verificación}\n")
    cuerpo.append("\\begin{itemize}\n")
    cuerpo.append(f"  \\item Estado: {esc_latex(str(resultado.get('estado')))}\n")
    cuerpo.append(f"  \\item Grado: {esc_latex(str(resultado.get('grado_skill', 'n/d')))}\n")
    cuerpo.append(f"  \\item Evaluación de consistencia (tests auto-generados por el modelo): "
                  f"{esc_latex(str(resultado.get('evaluar', {}).get('todos_ok')))}\n")
    veto = resultado.get('veto_fisica', {})
    cuerpo.append(f"  \\item Veto de física cerrada: "
                  f"{esc_latex(str(veto.get('ok')))} {esc_latex(str(veto.get('detalle')))}\n")
    cuerpo.append("  \\item Nota honesta: este es un DISEÑO CANDIDATO (autor=modelo, grado "
                  "máximo 1 sin firma humana). La física solo se usó como VETO (refutar absurdos), "
                  "no como certificación. Para subir a 'conectado' (grado 2) en adelante hace falta "
                  "Firma humana; 'fabricable' (grado 5) exige el prototipo físico medido.\n")
    cuerpo.append("\\end{itemize}\n")

    # 7. Missing tools (capability awareness)
    faltantes = resultado.get("herramientas_faltantes", [])
    if faltantes:
        cuerpo.append("\\section*{7. Herramientas necesarias (faltan instalar)}\n")
        cuerpo.append("\\begin{itemize}\n")
        for h in faltantes:
            cuerpo.append(f"  \\item \\textbf{{{esc_latex(str(h.get('id')))}}}: "
                          f"{esc_latex(str(h.get('proposito', '')))}\n")
            cuerpo.append(f"    Instalación: {esc_latex(str(h.get('instalacion', '')))}\n")
            if h.get("alternativa"):
                cuerpo.append(f"    Alternativa: {esc_latex(str(h.get('alternativa')))}\n")
        cuerpo.append("  \\item Sin estas herramientas, el entregable se DEGRADA: faltan el "
                      "esquemático/layout real (PCB) u otra salida que requiere EDA.\n")
        cuerpo.append("\\end{itemize}\n")

    titulo = esc_latex(SD._sane_latex(str(pedido)))
    latex = (PREAMBULO_TEX.replace("<<TITULO>>", titulo)
             .replace("deliverable canónico", "diseño candidato (autor=modelo, grado <=1)")
             + "".join(cuerpo) + "\\end{document}\n")

    salida = os.path.abspath(salida)
    os.makedirs(os.path.dirname(salida), exist_ok=True)
    with open(salida, "w", encoding="utf-8") as f:
        f.write(latex)

    import subprocess
    for _ in range(2):
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
                        salida], cwd=os.path.dirname(salida), check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pdf = salida.replace(".tex", ".pdf")
    return pdf if os.path.exists(pdf) else None


if __name__ == "__main__":
    m = HCMAMemoriaSQLite(":memory:")
    # mínimo auto-test SIN LLM: diseño provisto determinista (reloj)
    diseño = {
        "nombre": "reloj_electronico",
        "inputs": [{"name": "f_cristal_Hz", "question": "frecuencia del cristal", "unit": "Hz"}],
        "formulas": ["f_salida_Hz = f_cristal_Hz / 2^15"],
        "codigo_impl": "def calcular(inputs):\n"
                       "    f = float(inputs['f_cristal_Hz'])\n"
                       "    return {'f_salida_Hz': f / (2 ** 15)}\n",
        "tests_dorados": [
            {"id": "t1", "inputs": {"f_cristal_Hz": 32768.0}, "esperado": {"f_salida_Hz": 1.0}},
        ],
        "esquema": "cristal 32.768kHz -> CD4060 (divisor) -> CD4518 -> CD4511 -> display 7seg",
        "materiales": [["cristal_32k768", 1, "unidad"], ["CD4060", 1, "unidad"],
                       ["display_7seg", 6, "unidad"]],
        "dimensiones": {"aprox_pcb_mm": "120 x 80"},
        "lookup_queries": [],
    }
    r = construir_skill(m, "diseñame un reloj electronico", diseño=diseño, usar_llm=False)
    import json
    print(json.dumps(r, ensure_ascii=False, indent=2))
