#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""slug_canon.py — multi-language deterministic canonical slug (stdlib). LEAF module.

Fable 5.2 ruling (fable_slug_multilingue_fable.md). A skill's id can NOT depend on the language nor
collapse. Rules:

  - `canon(s)`: NFKC + casefold + whitespace collapse. NFC==NFD, fullwidth and double spaces
    converge to the SAME string (hash base). Identical for "same text written differently".
  - `contiene_no_latino(s)`: is there NON-latin script content (CJK/arabic/devanagari/…)? Latin
    diacritics (é/ñ/ç) do NOT count as non-latin.
  - `slug(s)`: None if there is nothing identifiable (empty/emoji/punctuation) -> ABSTAIN, do not
    mint id; legacy ASCII slug (tokens [a-z0-9], max 8) if purely latin; `sk_<sha256(canon)>` if
    there is non-latin content (the MIXED case also goes to hash, so as not to collapse 'PDF 合并'
    to 'pdf').

Imported by router_skills.py (dashboard), monitor_fachada.py and memoria_sqlite.py (scripts) so as
NOT to duplicate the algorithm (Fable P0-2). The monitor does NOT import the router: it imports
this leaf.
"""
import hashlib
import re
import unicodedata

# Algorithm version. Persisted in meta.slug_algo of each skill to migrate if it changes.
SLUG_ALGO = 2

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")


def canon(s):
    """Canonical form: NFKC + casefold + whitespace collapse. Returns '' if nothing."""
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def contiene_no_latino(s):
    """True if there is at least one alphanumeric character of NON-latin script
    (CJK/arabic/devanagari/…).

    A latin diacritic (é/ñ/ç) is alphanumeric and non-ASCII but its Unicode name starts with
    'LATIN', so it does NOT count as non-latin (it is latin with an accent)."""
    for ch in (s or ""):
        if ch.isalnum() and not ch.isascii() and not unicodedata.name(ch, "").startswith("LATIN"):
            return True
    return False


def _slug_legacy_ascii(s):
    """Legacy slug: [a-z0-9] tokens of the string (max 8). Stable for latin scripts."""
    parts = _ASCII_TOKEN_RE.findall((s or "").lower())
    return "_".join(parts[:8]) if parts else ""


def slug(s):
    """Canonical slug: None (abstain) | legacy ASCII | 'sk_<sha256(canon)>'."""
    c = canon(s)
    if not any(ch.isalnum() for ch in c):
        return None
    if not contiene_no_latino(c):
        legacy = _slug_legacy_ascii(s)
        if legacy:
            return legacy
    return "sk_" + hashlib.sha256(c.encode("utf-8")).hexdigest()[:12]


def _es_script_sin_espacios(ch):
    """Is the character from a script without word separation (CJK/Thai)? It benefits from
    character bigrams for lexical matching."""
    try:
        n = unicodedata.name(ch, "")
    except Exception:
        return False
    return n.startswith("CJK") or "THAI" in n


def palabras(s):
    """Unicode words (\\w+) without bigrams: for EXACT lookup of alias surfaces (P1-1).
    NFKC + casefold first (via canon), so 'qué' and 'que\\u0301' converge to 'qué'."""
    c = canon(s)
    return set(re.findall(r"\w+", c, re.UNICODE))


def tokens(s):
    """Multi-language lexical tokens (Fable P1-2): NFKC + casefold, Unicode words (\\w+), and
    character bigrams for scripts without spaces (CJK/Thai). Allows lexical matching in the SAME
    language without depending on the embeddings model. Used for FUZZY matching (not for exact
    alias lookup: `palabras` is for that)."""
    c = canon(s)
    out = set()
    for w in re.findall(r"\w+", c, re.UNICODE):
        out.add(w)
        if len(w) > 1 and any(_es_script_sin_espacios(ch) for ch in w):
            for i in range(len(w) - 1):
                out.add(w[i:i + 2])
    return out
