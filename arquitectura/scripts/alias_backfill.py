#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""alias_backfill.py — backfill of alias:<surface> -> target edges from the LEGACY format.

Fable ruling 5.2 (fable_olvido_alias_fable.md): the change from "loop by VALUE" to
"materialized edges" (crear_alias) was deployed WITHOUT backfill. This module DETECTS surfaces in
the old format and emits CANDIDATES (not edges): if the target is unanimous it is auto-created; if
there is conflict, it goes to a human queue.

Legacy forms detected (inconsistent direction):
  - bare S = 'apodo de [mi] E'   -> S -> E
  - S:apodo = 'de [mi] E'        -> S -> E
  - E:apodo = 'S' (inverted)     -> S -> E
  - S:es_E = 'sí'                -> S -> E
"""
import json
import re

from memoria_sqlite import _norm_slug, _es_centinela  # noqa: E402

# Form 1 (bare S): "apodo de [mi] E" -> E
_RE_APODO_DE = re.compile(
    r"^(?:apodo|alias|sobrenombre)\s+de\s+(?:mi\s+|la\s+|el\s+|su\s+)?([a-záéíóúñü]+)$",
    re.IGNORECASE)
# Form 2 (S:apodo): "de [mi] E" -> E
_RE_DE = re.compile(
    r"^de\s+(?:mi\s+|la\s+|el\s+|su\s+)?([a-záéíóúñü]+)$", re.IGNORECASE)
# Form 4 (S:es_E)
_RE_ES_ENTIDAD = re.compile(r"^(.+):es_(.+)$")


def _valor(raw):
    """Decodes the raw value (JSON) into a Python value (primitive string)."""
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        v = raw
    if isinstance(v, (dict, list)):
        return " ".join(str(x) for x in (v.values() if isinstance(v, dict) else v))
    return "" if v is None else str(v)


def detectar_aliases_legacy(m):
    """Returns sorted candidates: [{surface, votos, target_mayoritario, conflicto, formas}]."""
    votos = {}
    formas = {}
    for r in m.conn.execute(
            "SELECT key, value FROM facts WHERE authority=1 AND stale=0").fetchall():
        key = r["key"] or ""
        val = _valor(r["value"]).strip()
        v = val.lower()

        # form 4: S:es_E = 'sí' (e.g. 'guiya:es_perra' = 'sí')
        m_es = _RE_ES_ENTIDAD.match(key)
        if m_es and v in ("sí", "si", "yes"):
            s, e = _norm_slug(m_es.group(1)), _norm_slug(m_es.group(2))
            if s and e:
                votos.setdefault(s, {}).setdefault(e, 0)
                votos[s][e] += 1
                formas.setdefault(s, []).append(f"{key} = 'sí'")
                continue

        # form 3: E:apodo = 'S' (inverted; S is a single word, no spaces)
        if key.endswith(":apodo") and val.isalpha():
            e = _norm_slug(key[:-len(":apodo")])
            s = _norm_slug(val)
            if s and e:
                votos.setdefault(s, {}).setdefault(e, 0)
                votos[s][e] += 1
                formas.setdefault(s, []).append(f"{key} = '{val}' (invertida)")
                continue

        # form 1: bare S = 'apodo/alias/sobrenombre de [mi] E'
        if ":" not in key:
            m_ap = _RE_APODO_DE.match(v)
            if m_ap:
                s, e = _norm_slug(key), _norm_slug(m_ap.group(1))
                if s and e:
                    votos.setdefault(s, {}).setdefault(e, 0)
                    votos[s][e] += 1
                    formas.setdefault(s, []).append(f"{key} = '{val}'")
                    continue

        # form 2: S:apodo = 'de [mi] E'
        if key.endswith(":apodo"):
            m_de = _RE_DE.match(v)
            if m_de:
                s = _norm_slug(key.split(":")[0])
                e = _norm_slug(m_de.group(1))
                if s and e:
                    votos.setdefault(s, {}).setdefault(e, 0)
                    votos[s][e] += 1
                    formas.setdefault(s, []).append(f"{key} = '{val}'")

    out = []
    for s, vs in sorted(votos.items()):
        top = max(vs.items(), key=lambda kv: kv[1])
        out.append({"surface": s, "votos": vs, "target_mayoritario": top[0],
                    "conflicto": len(vs) > 1, "formas": formas.get(s, [])})
    return out


def clasificar_alias(key, value):
    """Classifies a (key, value) as one of the 4 legacy alias forms (Fable P0.1).

    Returns (surface, target) if alias-shaped, or None. Used in the monitor's WRITE PATH to route
a write shaped like an alias to `crear_alias` (edge) instead of a legacy fact."""
    key = key or ""
    val = value.strip() if isinstance(value, str) else ""
    v = val.lower()

    m_es = _RE_ES_ENTIDAD.match(key)
    if m_es and v in ("sí", "si", "yes"):
        s, e = _norm_slug(m_es.group(1)), _norm_slug(m_es.group(2))
        if s and e:
            return (s, e)

    if key.endswith(":apodo") and val.isalpha():
        e = _norm_slug(key[:-len(":apodo")])
        s = _norm_slug(val)
        if s and e:
            return (s, e)

    if ":" not in key:
        m_ap = _RE_APODO_DE.match(v)
        if m_ap:
            s = _norm_slug(key)
            e = _norm_slug(m_ap.group(1))
            if s and e:
                return (s, e)

    if key.endswith(":apodo"):
        m_de = _RE_DE.match(v)
        if m_de:
            s = _norm_slug(key.split(":")[0])
            e = _norm_slug(m_de.group(1))
            if s and e:
                return (s, e)
    return None


def backfill(m, confirmaciones, fuente="usuario", dry_run=False):
    """Creates the confirmed edges via crear_alias. `confirmaciones` = {surface: target}.

    `fuente` defaults to 'usuario': the edge is a human CONFIRMATION (not raw SQL), so it
    inherits authority and becomes verified. To audit the migration it can be distinguished by
    meta.tipo='alias'. Returns [{'surface','target','version'}] (version=None in dry_run)."""
    creadas = []
    for s, t in confirmaciones.items():
        if dry_run:
            creadas.append({"surface": s, "target": t, "version": None})
            continue
        v = m.crear_alias(s, t, fuente=fuente, verified=True)
        creadas.append({"surface": s, "target": t, "version": v})
    return creadas


def retirar_legacy(m, surfaces, dry_run=False):
    """Fable P0.3: retires the legacy namespace (bare S and S:*) superseded by alias edges.

    For each surface S, marks stale=1 the verified facts with key = S or key LIKE 'S:%'.
    Returns [{'key','retirados'}] of the affected keys."""
    afectados = []
    for s in surfaces:
        claves = [r["key"] for r in m.conn.execute(
            "SELECT key FROM facts WHERE (key=? OR key LIKE ?) AND authority=1 AND stale=0",
            (s, s + ":%")).fetchall()]
        for k in claves:
            if dry_run:
                afectados.append({"key": k, "retirados": 0})
                continue
            n = m.retirar(k, motivo=f"superseded por alias:{s}", autor="migracion:alias_v2")
            afectados.append({"key": k, "retirados": n})
    return afectados


def detectar_alias_por_nombre(m):
    """Fable P2: facts `E:nombre = V` (verified, a single word) are alias:<v> -> E candidates.

    Returns [{'surface','target','origen'}] for human confirmation (does NOT auto-create)."""
    out = []
    for r in m.conn.execute(
            "SELECT key, value FROM facts WHERE key LIKE '%:nombre' AND authority=1 AND stale=0"
    ).fetchall():
        e = _norm_slug(r["key"].rsplit(":nombre", 1)[0])
        v = _valor(r["value"]).strip()
        s = _norm_slug(v)
        # P1.4: sentinels ('desconocido', 'no sé', ...) are NOT names nor alias surfaces.
        if e and s and v.isalpha() and not _es_centinela(v):
            out.append({"surface": s, "target": e, "origen": r["key"]})
    return out


def validar_invariantes_alias(m):
    """Fable P1.2: returns the list of alias invariant violations.

    I1: every detected legacy surface has its alias:<S> edge.
    I2: if alias:X exists, there are no live X nor X:* (authority=1).
    I3: every target exists with at least one verified fact.
    I4: there are legacy surfaces and count(alias:*)=0."""
    violaciones = []
    cands = detectar_aliases_legacy(m)
    for c in cands:
        if m.alias_target(c["surface"]) is None:
            violaciones.append({"invariante": "I1", "surface": c["surface"],
                                "detalle": "surface legacy sin arista alias:*"})
    alias_rows = [r for r in m.conn.execute(
        "SELECT key, value FROM facts WHERE key LIKE 'alias:%' AND authority=1 AND stale=0"
    ).fetchall()]
    if cands and not alias_rows:
        violaciones.append({"invariante": "I4",
                            "detalle": "hay superficies legacy y cero aristas alias:*"})
    for r in alias_rows:
        s = r["key"][len("alias:"):]
        vivos = m.conn.execute(
            "SELECT COUNT(*) FROM facts WHERE (key=? OR key LIKE ?) AND authority=1 AND stale=0",
            (s, s + ":%")).fetchone()[0]
        if vivos:
            violaciones.append({"invariante": "I2", "surface": s,
                                "detalle": f"existe alias:{s} y hay {vivos} claves {s}/{s}:* vivas"})
        t = _valor(r["value"])
        if not m.conn.execute(
                "SELECT COUNT(*) FROM facts WHERE key LIKE ? AND authority=1 AND stale=0",
                (t + ":%",)).fetchone()[0]:
            violaciones.append({"invariante": "I3", "surface": s, "target": t,
                                "detalle": f"target {t} sin hechos verified"})
    return violaciones
