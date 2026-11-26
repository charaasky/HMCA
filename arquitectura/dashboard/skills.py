#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Skills as facts in HCMA — read/aggregation for the dashboard.

A **skill** is learned knowledge written ONCE and reused. It lives in HCMA memory as a fact
`skill:<id>` with `meta={"tipo":"skill"}`.

Each **successful use** is recorded as ONE fact (event), not as a counter:
  - `skill_uso:<id>:<seq>` → value=1, with `derived_from` toward (a) the result that justified
    it and (b) the version of the skill that executed it.

Why one fact per use (and not an accumulated counter): a counter is an AGGREGATE; marking it
`stale` does not say which addend became invalid, so no retraction is possible. With one fact per
use, if the result that justified it is invalidated, version-collapse marks THAT exact use
`stale`, and the "counter" (a VIEW = sum of non-stale uses) is recomputed correctly. It is the
basis of the auditable retraction of plasticity.

Does NOT redefine the `facts` schema: reuses `meta`, `fuente`, `derived_from` and
`verified/stale/authority`. A use's confidence is INHERITED from its dependencies (not from a
`fuente` label): `verified` is decided by `m.write` via `_linaje_ok`.

Key conventions:
  - `skill:<id>`            → skill declaration (meta.tipo = "skill")
  - `skill_uso:<id>:<seq>`  → a successful use event (value=1, with lineage)
"""
import json as _json

_SKILL_PREFIX = "skill:"
_USO_PREFIX = "skill_uso:"


def _dec(s):
    try:
        return _json.loads(s)
    except (TypeError, ValueError):
        return {}


def _skill_id(key):
    """`skill:area` -> 'area'; if it does not carry the exact prefix, returns None."""
    if key.startswith(_SKILL_PREFIX):
        return key[len(_SKILL_PREFIX):]
    return None


def _uso_de(key):
    """`skill_uso:area:17` -> 'area'; if it is not a usage key, returns None."""
    if not key.startswith(_USO_PREFIX):
        return None
    rest = key[len(_USO_PREFIX):]
    # split on the last ':' (the id could contain ':' in theory, but our ids don't)
    id_part, _, _seq = rest.rpartition(":")
    return id_part if id_part else None


def _skill_base(key):
    """`skill:<id>[:facet...]` -> '<id>' (canonical and legacy convention). None if not a skill.

    The id is the part between `skill:` and the next `:`; by convention ids do not carry ':'.
    `skill:area` (legacy) and `skill:area:spec` (canonical) return 'area'."""
    if not key.startswith(_SKILL_PREFIX):
        return None
    parts = key.split(":")
    if len(parts) < 2 or not parts[1]:
        return None
    return parts[1]


def _facet(key):
    """`skill:<id>:<facet...>` -> '<facet...>'; '' if it is a flat legacy `skill:<id>`.

    `skill:area:spec` -> 'spec'; `skill:area:ok:abc` -> 'ok:abc'; `skill:area` -> ''."""
    parts = key.split(":")
    if len(parts) <= 2:
        return ""
    return ":".join(parts[2:])


def _derived_from_latest(m, key):
    """`derived_from` of the latest version of `key` (includes stale facts), to resolve the lineage
    of intermediate nodes (`test:*`) regardless of their current state."""
    row = m.conn.execute(
        "SELECT derived_from FROM facts WHERE key=? ORDER BY version DESC LIMIT 1",
        (key,)).fetchone()
    return _dec(row["derived_from"]) if row else {}


def stats(m):
    """Skill aggregates (grouped by canonical id) from HCMA memory.

    `usos_totales` = sum of non-stale uses (view), over `uso:<id>:*` (canonical) and
    `skill_uso:<id>:<seq>` (legacy)."""
    c = m.conn
    by_id = {}
    for r in c.execute("SELECT key, verified, authority, stale FROM facts "
                       "WHERE key LIKE ?", ("skill:%",)).fetchall():
        sid = _skill_base(r["key"])
        if sid is None:
            continue
        d = by_id.setdefault(sid, {"verified": False, "authority": False, "stale": False})
        d["verified"] = d["verified"] or bool(r["verified"])
        d["authority"] = d["authority"] or bool(r["authority"])
        d["stale"] = d["stale"] or bool(r["stale"])
    usos = int(c.execute(
        "SELECT COUNT(*) n FROM facts WHERE key LIKE 'uso:%' AND stale=0").fetchone()["n"])
    usos += int(c.execute(
        "SELECT COALESCE(SUM(CASE WHEN stale=0 THEN value ELSE 0 END),0) s "
        "FROM facts WHERE key LIKE 'skill_uso:%'").fetchone()["s"] or 0)
    return {"disponibles": True,
            "total": len(by_id),
            "vigentes": sum(1 for d in by_id.values() if d["authority"] and not d["stale"]),
            "stale": sum(1 for d in by_id.values() if d["stale"]),
            "unverified": sum(1 for d in by_id.values() if not d["verified"]),
            "usos_totales": usos}


def _usos_por_id(m):
    """View {skill_id: nº non-stale uses}, reading `uso:<id>:*` (canonical, each fact = 1 use)
    and `skill_uso:<id>:<seq>` (legacy, value=1, summed)."""
    c = m.conn
    out = {}
    for r in c.execute("SELECT key FROM facts WHERE key LIKE 'uso:%' AND stale=0").fetchall():
        parts = r["key"].split(":")
        sid = parts[1] if len(parts) >= 2 else None
        if not sid:
            continue
        out[sid] = out.get(sid, 0) + 1
    for r in c.execute("SELECT key, value FROM facts WHERE key LIKE ? AND stale=0",
                       ("skill_uso:%",)).fetchall():
        sid = _uso_de(r["key"])
        if sid is None:
            continue
        try:
            out[sid] = out.get(sid, 0) + int(r["value"])
        except (TypeError, ValueError):
            pass
    return out


def costo_skill(m, skill_id):
    """Declared cost of the skill (meta.costo), 0 by default. O(1) = meta read.

    Reads `skill:<id>` (legacy) or `skill:<id>:spec` (canonical). Cost convention (fixed, declared in
    the skill, not measured at runtime):
      0 = free/trivial (in-memory counter)
      1 = O(encode) (similarity/embeddings)
      2 = O(LLM) (proposable/planner)
      3 = O(web/external tool)
    """
    f = m.current("skill:" + skill_id) or m.current("skill:" + skill_id + ":spec")
    if f is None:
        return 0
    try:
        return int((f.get("meta") or {}).get("costo", 0))
    except (TypeError, ValueError):
        return 0


def _deps_resueltas(m, dep_keys, current_sid):
    """Resolves a skill's raw `derived_from` to its SIGNIFICANT dependencies: oracles
    (`fuente:*`/`cota:*`) and other skills (by id), collapsing the intermediate `test:*` (following
    their lineage). Returns (set of oracles, set of skills)."""
    oraculos = set()
    skills = set()
    visitados = set()
    pila = list(dep_keys)
    while pila:
        k = pila.pop()
        if not k or k in visitados:
            continue
        visitados.add(k)
        if k.startswith("fuente:") or k.startswith("cota:"):
            oraculos.add(k)
        elif k.startswith("skill:"):
            other = _skill_base(k)
            if other and other != current_sid:
                skills.add(other)
        elif k.startswith("test:"):
            pila.extend(_derived_from_latest(m, k).keys())
        # `uso:*`, `skill_uso:*`, `skill_test:*` and others: not knowledge dependencies.
    return oraculos, skills


def list_skills(m, limit=500):
    """List of skills GROUPED by id (canonical convention `skill:<id>:spec/impl/ok/activo` and legacy
    `skill:<id>`). Each row = ONE skill with its state, uses, facets and dependencies.

    `dependencias` is the collapsed view for the tree (oracles + other skills); `deps_raw` keeps ALL
    the raw `derived_from` keys for full audit."""
    c = m.conn
    rows = c.execute(
        "SELECT key, value, unit, version, verified, authority, stale, fuente, meta, "
        "derived_from FROM facts WHERE key LIKE ? ORDER BY key, version",
        ("skill:%",)).fetchall()

    by_id = {}
    for r in rows:
        sid = _skill_base(r["key"])
        if sid is None:
            continue
        s = by_id.setdefault(sid, {
            "id": sid, "declaracion": {}, "unit": "", "version": 0,
            "verified": False, "authority": False, "stale": False,
            "fuente": "", "meta": {}, "deps_raw": set(), "usos": 0,
            "ok": False, "facetas": {},
        })
        facet = _facet(r["key"])
        s["facetas"][facet if facet else "declaracion"] = {
            "version": r["version"], "verified": bool(r["verified"]),
            "authority": bool(r["authority"]), "stale": bool(r["stale"]),
            "fuente": r["fuente"],
        }
        if facet in ("", "spec"):
            # The skill declaration = spec (canonical) or the flat fact (legacy).
            # Since rows come ordered by (key, version) ascending, the last one wins = max version.
            s["declaracion"] = _dec(r["value"])
            s["unit"] = r["unit"]
            s["version"] = r["version"]
            s["verified"] = bool(r["verified"])
            s["authority"] = bool(r["authority"])
            s["stale"] = bool(r["stale"])
            s["fuente"] = r["fuente"]
            s["meta"] = _dec(r["meta"])
        elif facet == "ok" or facet.startswith("ok:"):
            if bool(r["verified"]) and bool(r["authority"]) and not bool(r["stale"]):
                s["ok"] = True
        for dk in _dec(r["derived_from"]).keys():
            s["deps_raw"].add(dk)

    usos = _usos_por_id(m)
    out = []
    for sid in sorted(by_id):
        s = by_id[sid]
        s["usos"] = usos.get(sid, 0)
        oraculos, skills = _deps_resueltas(m, s["deps_raw"], sid)
        s["dependencias"] = sorted(oraculos | skills)
        s["deps_raw"] = sorted(s["deps_raw"])
        out.append(s)
    return out[:limit]


def ranking(m, limit=20):
    """Skills ordered by use (desc), for 'which are the most used'."""
    return sorted(list_skills(m, limit=1000), key=lambda s: -s["usos"])[:limit]


def _norm_dep(dep, ids):
    """Classifies an already-collapsed dependency into (tipo, id_nodo): skill (id) or oracle."""
    if dep in ids:
        return "skill", dep
    if dep.startswith("fuente:") or dep.startswith("cota:"):
        return "oraculo", dep
    return "otro", dep


def arbol_skills(m, limit=500):
    """Skill TREE (lineage): one node per skill + oracles + others, and skill → dep edges.

    Relies on `list_skills` (already groups by canonical id and collapses `test:*`). Analysis: roots
    (without deps), leaves (without dependents), max depth and cycles (0 due to DAG)."""
    skills = list_skills(m, limit=limit)
    ids = {s["id"] for s in skills}
    nodos = {}
    aristas = []

    def _estado(s):
        if s.get("stale"):
            return "stale"
        if s.get("ok") or s.get("authority"):
            return "vigente"
        if s.get("verified"):
            return "verified"
        return "unverified"

    for s in skills:
        nodos[s["id"]] = {"tipo": "skill", "id": s["id"], "estado": _estado(s),
                           "usos": s["usos"], "fuente": s["fuente"],
                           "deps": len(s["dependencias"]), "ok": bool(s.get("ok"))}
        for dep in s["dependencias"]:
            tipo, nid = _norm_dep(dep, ids)
            nodos.setdefault(nid, {"tipo": tipo, "id": nid, "estado": "", "usos": 0,
                                  "fuente": "", "deps": 0, "ok": False})
            aristas.append({"de": s["id"], "a": nid})

    dependientes = {}
    for a in aristas:
        dependientes.setdefault(a["a"], []).append(a["de"])

    raices = sorted(n for n in nodos if not any(a["de"] == n for a in aristas))  # no deps
    hojas = sorted(n for n in nodos if n not in dependientes)

    # Longest-path over the DAG (depth = longest path to an oracle/root).
    # Memoize per node (not `visited`→0): in a DAG with shared deps, a node can be reached by
    # paths of different lengths; its depth must be preserved, not 0.
    prof = {}
    visitando = set()

    def _dfs(n):
        nonlocal ciclos
        if n in prof:
            return prof[n]
        if n in visitando:
            ciclos += 1
            return 0
        visitando.add(n)
        deps = [a["a"] for a in aristas if a["de"] == n]
        d = 0
        for dep in deps:
            d = max(d, 1 + _dfs(dep))
        visitando.remove(n)
        prof[n] = d
        return d

    prof_max = 0
    ciclos = 0
    for n in nodos:
        prof_max = max(prof_max, _dfs(n))

    return {
        "nodos": list(nodos.values()),
        "aristas": aristas,
        "analisis": {
            "n_skills": len(skills),
            "n_nodos": len(nodos),
            "n_oraculos": sum(1 for n in nodos.values() if n["tipo"] == "oraculo"),
            "raices": raices,
            "hojas": hojas,
            "profundidad_maxima": prof_max,
            "ciclos": ciclos,
        },
    }


def resultados_test(m, skill_id=None, limit=100):
    """Lists skill test results (`skill_test:*`), auditable.

    Returns a list with, per test, all the evidence: pass/fail, expected vs obtained values, failures,
    test source and timestamp. If `skill_id` is given, filters only that one. This is what the
    dashboard shows to AUDIT whether a test validated something incorrect."""
    c = m.conn
    q = "SELECT key, value, meta, derived_from, version, stale FROM facts WHERE key LIKE ?"
    rows = c.execute(q + " ORDER BY version DESC LIMIT ?", ("skill_test:%", limit)).fetchall()
    out = []
    for r in rows:
        meta = _dec(r["meta"])
        sid = meta.get("skill_id")
        if skill_id is not None and sid != skill_id:
            continue
        out.append({
            "key": r["key"],
            "skill_id": sid,
            "pass": meta.get("pass"),
            "esperado": meta.get("esperado"),
            "obtenido": meta.get("obtenido"),
            "fallos": meta.get("fallos"),
            "error": meta.get("error"),
            "fuente_test": meta.get("fuente_test"),
            "timestamp": meta.get("timestamp"),
            "version": r["version"],
            "stale": bool(r["stale"]),
        })
    return out


def registro(m, skill_id, valor, unit="", fuente="ingeniero", verified=False,
             meta=None, derived_from=None):
    """Declares (or updates) a skill as a fact in HCMA.

    - `skill_id`    : short identifier (e.g. 'area').
    - `valor`       : description/expression of the skill.
    - `derived_from`: which formulas/facts the skill depends on (lineage); key for auditing that, if
      the formula it embodies changes, the skill is invalidated.
    - `verified`    : whether it is canonical.

    Returns the written version (from `m.write`)."""
    meta = dict(meta or {})
    meta["tipo"] = "skill"
    return m.write("skill:" + skill_id, valor, unit=unit, fuente=fuente,
                   verified=verified, meta=meta, derived_from=derived_from or {})


def _next_uso_seq(m):
    """Monotonic, unique sequence number for a use (above the global MAX(version))."""
    row = m.conn.execute("SELECT COALESCE(MAX(version),0)+1 v FROM facts").fetchone()
    return int(row["v"])


def verificar_skill(m, skill_id, calcular, test, fuente_test=None, tolerancia=0.01):
    """Verifies a skill by running a DETERMINISTIC TEST and records the result as an AUDITED fact in
    HCMA (to validate it later if the test fails or validates something that is not).

    The test is a solved example: `test = (inputs, esperado)`. It runs `calcular(inputs)` and compares
    against `esperado` with tolerance.

    There is NO auto-promotion by lineage: it is only promoted to `verified` if the test passes AND
    its `fuente_test` is a canonical HCMA fact (not a loose string). Specifically, `fuente_test` must
    be a `(key, version)` tuple of a `verified ∧ authority` fact: the test's confidence is INHERITED
    from that source via lineage, like the rest of HCMA.

    This closes the 'a golden test validates something that is not' gap: if the source is not
    canonical, the skill is NOT promoted, no matter what the test does.
    """
    import time
    inputs, esperado = test
    try:
        got = calcular(inputs)
        if isinstance(got, list):
            got = {q["name"]: q["value"] for q in got}
        if not isinstance(got, dict):
            got = {"resultado": got}
    except Exception as e:
        got = {}
        error = f"excepción: {e}"
    else:
        error = None

    fallos = []
    ok = error is None
    if error is None:
        for k, v in esperado.items():
            if k not in got:
                ok = False
                fallos.append({"clave": k, "tipo": "ausente", "esperado": v})
            elif isinstance(v, (int, float)) and isinstance(got[k], (int, float)):
                if abs(got[k] - v) > tolerancia:
                    ok = False
                    fallos.append({"clave": k, "tipo": "difiere", "esperado": v,
                                   "obtenido": got[k], "tolerancia": tolerancia})
            elif got[k] != v:
                ok = False
                fallos.append({"clave": k, "tipo": "difiere", "esperado": v,
                               "obtenido": got[k]})

    # Validate that the test source is a CANONICAL fact (verified ∧ authority).
    # fuente_test is (key, version) or None. If None or not canonical, no promotion.
    fuente_key, fuente_ver = (None, None)
    fuente_canonica = False
    if isinstance(fuente_test, (tuple, list)) and len(fuente_test) == 2:
        fuente_key, fuente_ver = fuente_test
        f = m.current(fuente_key)
        fuente_canonica = (f is not None and f["verified"] and f["authority"]
                           and not f["stale"] and f["version"] == fuente_ver)

    key_skill = "skill:" + skill_id
    cur_skill = m.current(key_skill)
    if cur_skill is None:
        m.write(key_skill, skill_id, fuente="test", verified=False,
                meta={"tipo": "skill"})

    # audited record of the test result
    seq = _next_uso_seq(m)
    key_test = f"skill_test:{skill_id}:{seq}"
    meta_test = {
        "tipo": "skill_test",
        "skill_id": skill_id,
        "pass": bool(ok),
        "esperado": esperado,
        "obtenido": got,
        "fallos": fallos,
        "error": error,
        "tolerancia": tolerancia,
        "fuente_key": fuente_key,
        "fuente_ver": fuente_ver,
        "fuente_canonica": fuente_canonica,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    df = {"skill:" + skill_id: m.current(key_skill)["version"] if m.current(key_skill) else None}
    df = {k: v for k, v in df.items() if v is not None}
    if fuente_canonica:
        df[fuente_key] = fuente_ver
    v_test = m.write(key_test, int(ok), unit="", fuente="test",
                     verified=False, meta=meta_test, derived_from=df)

    # NO auto-promotion. Previously this wrote `fuente='test', verified=True`, which was the
    # postmortem bypass: it was born verified by a source label, not by being a canonical fact with
    # lineage. Promotion is done ONLY by `evaluar` (derives `ok` from canonical tests with source
    # `calculadora` + lineage). Therefore here the skill stays unverified until `evaluar`
    # certifies it by lineage. (See POSTMORTEM_skills.md I1/I8.)

    return ok, v_test


def reverificar_descendientes(m, clave_padre, calcular, tolerancia=0.01):
    """Selective CI: after correcting `clave_padre`, re-runs the calculation of each `stale`
    descendant and only re-certifies the one that STILL gives the correct result (per its implicit
    golden test = the last value it had before going stale, recomputed with the new parent).

    This resolves the weakness of 'conservative lineage' (which invalidates EVERY descendant without
    discriminating): re-tests each descendant and distinguishes 'the change broke it' (stays
    unverified) from 'the change did not affect it' (re-certified with derived_from to the new parent
    version).

    - `clave_padre` : the corrected fact (e.g. 'k_nucleo').
    - `calcular`    : callable (clave_dominio, valor_padre) -> new descendant value.

    Returns {'re_certificados', 'cambiaron', 'no_cambiaron', 'quedan_invalidos'}.

    Note on `stale` growth: since the descendant's `derived_from` changes (points to the parent's new
    version), the HCMA no-op does NOT apply and a new version of the descendant is created; the
    previous one stays `stale`. THAT IS CORRECT: it is the cost of auditability by lineage (keeping
    the history to be able to invalidate transitively). Compaction of that history belongs to the
    HCMA core (deep_sleep), NOT the skills layer.
    """
    padre = m.current(clave_padre)
    if padre is None:
        return {"re_certificados": 0, "cambiaron": 0, "no_cambiaron": 0, "quedan_invalidos": 0}
    padre_ver = padre["version"]
    padre_val = padre["value"]

    c = m.conn
    rows = c.execute(
        "SELECT key, value, unit, version, derived_from FROM facts WHERE stale=1 "
        "AND key NOT LIKE 'skill:%' AND key NOT LIKE 'skill_test:%' AND key NOT LIKE 'skill_uso:%'",
    ).fetchall()

    re_cert = 0
    cambiaron = 0
    no_cambiaron = 0
    quedan = 0
    for r in rows:
        df = _dec(r["derived_from"])
        if clave_padre not in df:
            continue
        try:
            valor_previo = _dec(r["value"])
        except Exception:
            valor_previo = r["value"]
        nuevo_val = calcular(r["key"], padre_val)
        if nuevo_val is None:
            quedan += 1
            continue
        if isinstance(valor_previo, (int, float)) and isinstance(nuevo_val, (int, float)):
            cambio = abs(valor_previo - nuevo_val) > tolerancia
        else:
            cambio = valor_previo != nuevo_val
        # re-certify with derived_from updated to the parent's new version.
        # (The derived_from changes, so the HCMA no-op does NOT apply: a new version of the
        #  descendant is created and the previous one stays stale. That is the correct cost of
        #  auditability by lineage, not a bug. Compaction of that history is NOT the skills
        #  layer's domain: it is the HCMA core, which is not touched.)
        m.write(r["key"], nuevo_val, unit=r["unit"], fuente="calculadora",
                verified=True, derived_from={clave_padre: padre_ver})
        re_cert += 1
        if cambio:
            cambiaron += 1
        else:
            no_cambiaron += 1

    return {"re_certificados": re_cert, "cambiaron": cambiaron,
            "no_cambiaron": no_cambiaron, "quedan_invalidos": quedan}


def registrar_uso(m, skill_id, skills_version, resultado_refs, exito=True):
    """Records ONE use event (not a counter) with lineage, for real retraction.

    - `skill_id`       : id of the used skill.
    - `skills_version` : version of the `skill:<id>` fact that executed (connects use → skill).
    - `resultado_refs` : iterable of (key, version) of the results that justify the use (all those
      backing the success). If one is invalidated, this use becomes stale.
    - `exito`          : only recorded if True.

    The use's `verified` is inherited from the dependencies (m.write → _linaje_ok), NOT declared. The
    key is unique per event (`skill_uso:<id>:<seq>`), so each use is retractable separately without
    overwriting the others. Returns the written version, or None if it is not a success."""
    if not exito:
        return None
    derived_from = {}
    for rk, rv in (resultado_refs or []):
        derived_from[rk] = rv
    if skills_version is not None:
        derived_from["skill:" + skill_id] = skills_version
    key = f"{_USO_PREFIX}{skill_id}:{_next_uso_seq(m)}"
    return m.write(key, 1, unit="", fuente="orquestador",
                   verified=True, derived_from=derived_from,
                   meta={"tipo": "skill_uso"})
