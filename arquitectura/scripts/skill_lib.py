#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""skill_lib — skills subsystem ON TOP of HCMA (not in parallel).

Reuses the primitives of `HCMAMemoriaSQLite` (write/current/get_version/keys) and does NOT reimplement
verification. Implements the model validated by Fable (POSTMORTEM_skills.md §7quinquies):

  skill:<id>:spec              verified (ingeniero/usuario) — contract + tests_dorados
  skill:<id>:impl              ALWAYS unverified — candidate (self-contained source code)
  test:<id>:<impl_hash>:<caso> verified (calculadora), derived_from=spec   (execution record)
  skill:<id>:ok:<impl_hash>    verified (calculadora), derived_from=ALL the test:* of that impl
  skill:<id>:activo            verified, derived_from=[ok:@version]  (pointer to the impl to run)

Covered invariants: I1 (frozen source), I2 (ok by impl_hash), I3 (idempotency by hash),
I4 (ok->test->spec lineage), I7 (completeness), I8 (recomputable), I11 (no partial promotion),
I12 (hash in two places), I13 (spec coherence), I14 (impl_hash+spec_version idempotency),
I15 (purity/isolated compilation), I16 (completeness at the moment). I9 (get_version/keys) in memory.

Does NOT touch hcma_v64.py nor paper6_osf/. Does NOT import dashboard/skills.py (the old lifecycle).
"""

import hashlib
import json as _json
import math


# ─────────────────────────────────────────────────────────────────────────────
# pure helpers
# ─────────────────────────────────────────────────────────────────────────────
def canon_codigo(codigo: str) -> str:
    """Normalizes the text before hashing: \r\n→\n, per-line strip, final strip.
    Hash over TEXT (not AST) so as not to couple identity to the Python version."""
    return "\n".join(line.rstrip() for line in codigo.replace("\r\n", "\n").split("\n")).strip()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def impl_hash(codigo: str) -> str:
    return sha256_hex(canon_codigo(codigo))


def spec_hash(spec: dict) -> str:
    return sha256_hex(_json.dumps(spec, sort_keys=True, ensure_ascii=False))


def _sanear(v):
    """Converts non-finites to a marker dict so `_enc` (allow_nan=False) does not explode."""
    if isinstance(v, float) and not math.isfinite(v):
        return {"no_finito": "nan" if math.isnan(v) else ("inf" if v > 0 else "-inf")}
    if isinstance(v, dict):
        return {k: _sanear(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_sanear(x) for x in v]
    return v


# Safe builtins for isolated compilation (I15). No open/import/socket/... .
_SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "sum": sum, "round": round,
    "len": len, "range": range, "enumerate": enumerate, "zip": zip,
    "float": float, "int": int, "str": str, "bool": bool, "list": list,
    "dict": dict, "tuple": tuple, "set": set, "sorted": sorted, "any": any,
    "all": all, "True": True, "False": False, "None": None,
}


def _compilar(codigo: str, entrypoint: str):
    """Compiles `codigo` in an isolated namespace and returns the `entrypoint` callable.

    I15 (purity): the impl does NOT have access to HCMA, fs, network, time, nor free import.
    Only `math`/`cmath` (pure modules) and minimal builtins."""
    ns = {"__builtins__": dict(_SAFE_BUILTINS), "math": math}
    try:
        import cmath
        ns["cmath"] = cmath
    except Exception:
        pass
    try:
        exec(compile(codigo, "<skill>", "exec"), ns)
    except Exception as e:
        raise ValueError(f"impl no compila: {e!r}") from e
    fn = ns.get(entrypoint)
    if fn is None:
        raise ValueError(f"entrypoint '{entrypoint}' ausente en la impl")
    if not callable(fn):
        raise ValueError(f"entrypoint '{entrypoint}' no es callable")
    return fn


# ─────────────────────────────────────────────────────────────────────────────
# spec validation (minimum schema, rejects without golden tests — Fable P2)
# ─────────────────────────────────────────────────────────────────────────────
def _validar_spec(spec: dict):
    if not isinstance(spec, dict):
        raise ValueError("spec debe ser un dict")
    if not isinstance(spec.get("nombre"), str) or not spec["nombre"]:
        raise ValueError("spec.nombre ausente")
    if not isinstance(spec.get("tests_dorados"), list) or not spec["tests_dorados"]:
        raise ValueError("spec sin tests_dorados (un ok vacío pasaría _linaje_ok trivial)")
    ids = []
    for t in spec["tests_dorados"]:
        if not isinstance(t, dict) or not isinstance(t.get("id"), str) or not t["id"]:
            raise ValueError("cada test dorado necesita 'id' string único")
        ids.append(t["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("ids de tests dorados repetidos")
    # outputs with comparison tolerance (does not live in the evaluar code)
    for o in spec.get("outputs", []):
        if not isinstance(o.get("nombre"), str) or not o["nombre"]:
            raise ValueError("output sin nombre")
        tol = o.get("tolerancia")
        if tol is None:
            raise ValueError(f"output '{o['nombre']}' sin tolerancia")


def _tolerancia_por_output(spec: dict):
    """Map output.nombre -> (tipo, valor). Default: abs 1e-6 if not declared."""
    out = {}
    for o in spec.get("outputs", []):
        t = o.get("tolerancia", {})
        out[o["nombre"]] = (t.get("tipo", "abs"), t.get("valor", 1e-6))
    return out


def _comparar(obtenido, esperado, tolerancias):
    """Compares obtained vs expected per output, with the spec's tolerance. Returns (pass, detail)."""
    if not isinstance(esperado, dict):
        return False, f"esperado no es dict: {esperado!r}"
    detalle = []
    ok = True
    for k, v in esperado.items():
        if k not in obtenido:
            ok = False
            detalle.append(f"{k}: ausente (esperado {v})")
            continue
        got = obtenido[k]
        t_tipo, t_val = tolerancias.get(k, ("abs", 1e-6))
        if isinstance(v, (int, float)) and isinstance(got, (int, float)) and not isinstance(got, bool):
            if math.isnan(got) or math.isinf(got):
                ok = False
                detalle.append(f"{k}: no-finito {got}")
            elif t_tipo == "rel":
                if abs(got - v) > t_val * max(abs(v), 1e-12):
                    ok = False
                    detalle.append(f"{k}: {got} vs {v} (rel {t_val})")
            else:
                if abs(got - v) > t_val:
                    ok = False
                    detalle.append(f"{k}: {got} vs {v} (abs {t_val})")
        elif got != v:
            ok = False
            detalle.append(f"{k}: {got!r} vs {v!r}")
    return ok, "; ".join(detalle)


# ─────────────────────────────────────────────────────────────────────────────
# public API (6 functions from the postmortem + evaluar with I7/I8)
# ─────────────────────────────────────────────────────────────────────────────
def registrar_spec(m, id, spec, fuente="ingeniero"):
    """write spec verified (human authority). Validates schema and rejects without golden tests."""
    if fuente not in ("ingeniero", "usuario"):
        raise ValueError("spec solo de fuente humana (ingeniero/usuario)")
    _validar_spec(spec)
    return m.write(f"skill:{id}:spec", spec, unit="", fuente=fuente, verified=True)


def proponer_impl(m, id, codigo, fuente="base", entrypoint="calcular", formulas=None):
    """write impl ALWAYS unverified. Compiles early to fail on SyntaxError/entrypoint.
    Returns (version, impl_hash). Idempotent by content hash (I3)."""
    spec = m.current(f"skill:{id}:spec")
    if spec is None:
        raise ValueError(f"no hay spec para '{id}'; registrar_spec primero")
    h = impl_hash(codigo)
    # idempotency by (id, hash(codigo)): if the last impl has the same hash, do not rewrite
    value = {
        "lenguaje": "python",
        "entrypoint": entrypoint,
        "codigo": codigo,
        "formulas": formulas or {},
        "impl_hash": h,
        "spec_version_objetivo": spec["version"],
    }
    # compile to fail early (before persisting)
    _compilar(codigo, entrypoint)
    # if a current impl with the same value already exists, return its version (no-op)
    existing = _latest_impl(m, id)
    if existing is not None and existing.get("metadata", {}).get("impl_hash") == h:
        return existing["version"], h
    v = m.write(f"skill:{id}:impl", value, unit="", fuente=fuente, verified=False)
    return v, h


def evaluar(m, id, impl_version):
    """Runs ALL the spec's golden tests against impl@impl_version and, if ALL pass,
    derives `ok:<impl_hash>`. Returns a dict with the result (Evaluacion). I7/I8/I11-I16."""
    spec = m.current(f"skill:{id}:spec")
    if spec is None:
        raise ValueError(f"no hay spec para '{id}'")
    impl = m.get_version(f"skill:{id}:impl", impl_version)
    if impl is None:
        raise ValueError(f"impl@v{impl_version} no existe para '{id}'")
    value = impl["value"]
    codigo = value.get("codigo")
    entrypoint = value.get("entrypoint", "calcular")
    h_declarado = value.get("impl_hash")
    h_real = impl_hash(codigo) if isinstance(codigo, str) else None
    if h_declarado != h_real:
        raise ValueError(f"impl corrupta: hash decl {h_declarado} != {h_real}")  # I12

    # idempotency (I14): if a current ok already exists for (impl_hash, spec_version), return it
    prev = m.current(f"skill:{id}:ok:{h_real}")
    if prev is not None and prev["value"].get("spec_version") == spec["version"]:
        return {"id": id, "impl_hash": h_real, "impl_version": impl_version,
                "spec_version": spec["version"], "ok_version": prev["version"],
                "todos_ok": True, "reusado": True}

    fn = _compilar(codigo, entrypoint)  # I15 (purity already validated in compilar)
    tolerancias = _tolerancia_por_output(spec["value"])
    tests = spec["value"].get("tests_dorados", [])
    test_refs = []   # [(caso_id, version, pass)]
    all_pass = True
    for caso in tests:
        cid = caso["id"]
        inputs = caso.get("inputs", {})
        esperado = caso.get("esperado", {})
        try:
            # CONTRACT: fn receives the inputs dict as the ONLY positional argument.
            obtenido = fn(inputs)
            pass_, detalle = _comparar(obtenido, esperado, tolerancias)
            err = None
        except Exception as e:
            obtenido = None
            pass_ = False
            detalle = f"excepción: {e!r}"
            err = repr(e)
        val = {
            "caso": cid,
            "inputs": _sanear(inputs),
            "esperado": _sanear(esperado),
            "obtenido": _sanear(obtenido),
            "pass": pass_,
            "detalle": detalle,
            "error": err,
            "impl_hash": h_real,
            "impl_version": impl_version,
        }
        # I11: the test's lineage carries its trust base. If the golden case declares
        # `fuente` (list of already-verified `fuente:<fid>` / `cota:*` keys), the test's `derived_from`
        # includes them, making it an oracle (grade 'canonico'). Without them, it derives
        # only from spec (grade 'conforme': consistency, not validity).
        df = {f"skill:{id}:spec": spec["version"]}
        for fk in caso.get("fuente", []):
            fsrc = m.current(fk)
            if fsrc is None:
                # declared source but not current -> it does not serve as an oracle; we ignore it
                # (the test stays 'conforme', not 'canonico').
                continue
            df[fk] = fsrc["version"]
        v = m.write(f"test:{id}:{h_real}:{cid}", val, unit="", fuente="calculadora",
                    verified=True, derived_from=df)
        test_refs.append({"caso": cid, "test_version": v, "pass": pass_})
        all_pass = all_pass and pass_

    if not all_pass:
        return {"id": id, "impl_hash": h_real, "impl_version": impl_version,
                "spec_version": spec["version"], "ok_version": None, "tests": test_refs,
                "todos_ok": False, "motivo": "fallan: " + "; ".join(
                    f"{t['caso']}" for t in test_refs if not t["pass"])}

    # I7 (completeness): derived_from == ALL the test:* of this run, by full KEY
    derived_from = {f"test:{id}:{h_real}:{t['caso']}": t["test_version"] for t in test_refs}
    ok_value = {
        "impl_hash": h_real,
        "impl_version": impl_version,
        "spec_version": spec["version"],
        "spec_hash": spec_hash(spec["value"]),
        "tests": test_refs,
        "n_casos": len(tests),
    }
    ok_v = m.write(f"skill:{id}:ok:{h_real}", ok_value, unit="", fuente="calculadora",
                   verified=True, derived_from=derived_from)
    return {"id": id, "impl_hash": h_real, "impl_version": impl_version,
            "spec_version": spec["version"], "ok_version": ok_v, "tests": test_refs,
            "todos_ok": True}


def _latest_impl(m, id):
    """Latest impl version (by descending version) for proponer_impl idempotency.
    Uses direct SQL read (there is no 'latest' primitive; get_version requires a version)."""
    row = m.conn.execute(
        "SELECT version, value FROM facts WHERE key=? ORDER BY version DESC LIMIT 1",
        (f"skill:{id}:impl",)).fetchone()
    if row is None:
        return None
    return {"version": row["version"], "metadata": _json.loads(row["value"])}


def ejecutables(m):
    """Ids of skills with a canonical `ok:<impl_hash>`, with their COMPUTED grade (lineage).

    Returns {id: {impl_hash: {'ok_version': int, 'grado': str}}} partitioned by COMPUTED
    (not declared) grade. `grado` comes from `linaje.grado()` over the `ok`.
    """
    import linaje as _linaje
    out = {}
    for k in m.keys("skill:"):
        partes = k.split(":")
        # skill:<id>:ok:<impl_hash>
        if len(partes) == 4 and partes[2] == "ok":
            sid = partes[1]
            ok = m.current(k)
            if ok is None:
                continue
            g = _linaje.grado(m, k, ok["version"])
            out.setdefault(sid, {})[partes[3]] = {"ok_version": ok["version"], "grado": g}
    return out


def existentes(m, id):
    """True if there is ANY `skill:<id>:*` fact (spec/impl/ok/activo) in any state."""
    return len(m.keys(f"skill:{id}:")) > 0


def registrar_fuente(m, id, tipo, ref, unidad="", fuente="ingeniero"):
    """`fuente:<fid>` fact (independent oracle) canonical. I11.

    tipo ∈ {norma, tabla_fabricante, medición, texto_referencia, cálculo_independiente}.
    `ref` = the citation (document+page / instrument+date / hash).

    GOLDEN RULE (P0 corrected after Fable review): a canonical source is ONLY minted by a
    HUMAN ACTOR. `fuente` must be 'ingeniero' or 'usuario'. If 'tabla'/'calculadora'/
    'modelo'/anything else is passed, it is DEGRADED automatically to unverified (the web is NOT 'tabla').

    To register evidence recovered by web (not human-verified), use
    registrar_evidencia_web() instead, which stays ALWAYS unverified with autor=modelo.
    """
    if fuente not in ("ingeniero", "usuario"):
        # degrade: no one other than a human mints a canonical source
        return m.write(f"fuente:{id}", {"tipo": tipo, "ref": ref}, unit=unidad,
                       fuente="modelo", verified=False)
    return m.write(f"fuente:{id}", {"tipo": tipo, "ref": ref}, unit=unidad,
                   fuente=fuente, verified=True)


def registrar_evidencia_web(m, id, query, urls, extracto, unidad=""):
    """Evidence recovered by web search. ALWAYS unverified, autor=modelo.

    It is the RECOVERY channel, not an oracle. The Lenovo/DOE/etc. URL does not prove that the
    extracted value is in the document: it requires a human to confirm the concrete datasheet
    to promote to `tabla`. We store the provenance (query, urls, extract, ts)
    so that future promotion is auditable.
    """
    import time as _time
    val = {"tipo": "web_no_verificada", "query": query, "urls": urls or [],
           "extracto": extracto or {},
           "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())}
    return m.write(f"fuente:{id}", val, unit=unidad, fuente="modelo", verified=False,
                   meta={"autor": "modelo", "canal": "web"})


def registrar_cota(m, dominio, magnitud, regimen, minimo, maximo, unidad, fuente="tabla",
                   ref=None):
    """`cota:<dominio>:<magnitud>:<regimen>` fact canonical (physical sanity filter). I12.

    `fuente` HCMA that signs it (tabla/norma/medición). `ref` optional citation."""
    val = {"min": minimo, "max": maximo, "unidad": unidad}
    if ref:
        val["ref"] = ref
    return m.write(f"cota:{dominio}:{magnitud}:{regimen}", val, unit=unidad,
                   fuente=fuente, verified=True)


def grado_skill(m, id, impl_hash):
    """Computed grade of a skill's `ok` (convenience). 'desconocido' if there is no ok."""
    import linaje as _linaje
    ok = m.current(f"skill:{id}:ok:{impl_hash}")
    if ok is None:
        return "desconocido"
    return _linaje.grado(m, f"skill:{id}:ok:{impl_hash}", ok["version"])


def registrar_uso(m, id, run_id, impl_hash, ok_version):
    """Records ONE use event with lineage to the impl and the ok that justified it."""
    import time as _time
    return m.write(
        f"uso:{id}:{run_id}", {"impl_hash": impl_hash}, unit="", fuente="orquestador",
        verified=True,
        derived_from={f"skill:{id}:ok:{impl_hash}": ok_version},
        meta={"tipo": "uso", "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())})


def activar(m, id, impl_hash):
    """`activo` pointer to the impl that runs. derived_from the corresponding ok (I13)."""
    ok = m.current(f"skill:{id}:ok:{impl_hash}")
    if ok is None:
        raise ValueError(f"no hay ok vigente para '{id}' impl_hash={impl_hash}")
    return m.write(f"skill:{id}:activo", {"impl_hash": impl_hash}, unit="", fuente="ingeniero",
                   verified=True, derived_from={f"skill:{id}:ok:{impl_hash}": ok["version"]})
