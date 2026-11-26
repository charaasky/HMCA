#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""cli_firma_humana.py — CLI for the HUMAN (outside the agent's runtime).

Two commands:

  generar-claves   generates the Ed25519 pair and stores the PRIVATE key in ~/.config/hcma/humano_ed25519.key
                   (600) and the PUBLIC key in ~/.config/hcma/humano_ed25519.pub. The runtime only reads
                   the public one.

  firmar           signs an action (deontic authorization) with the PRIVATE key. It receives by arguments
                   the need, tool_id, and the artifact (JSON), and emits the hex Ed25519 signature + the
                   absolute expiration. This is what the human pastes into the system.

IMPORTANT: this CLI runs in a process/user separate from the agent. The agent/runtime NEVER
executes `firmar` or `generar-claves` (it does not have the private key in scope). This is the P0-a closure.
"""
import json
import os
import sys
import time

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
import firma_asimetrica as FA  # noqa: E402

DEFAULT_TTL = 3600


# ─────────────────────────────────────────────────────────────────────────────
# CONTENT VALIDATION (P0 Fable 4th revision): 'what is shown = exactly what is signed'.
# ─────────────────────────────────────────────────────────────────────────────
# Fable: BLACKLIST sanitizing is insufficient (missing U+2066-2069, zero-width, tag chars,
# and the malicious line 4000). It is closed with two rules:
#   1. WHITELIST: we only accept characters of INNOCUOUS Unicode categories (letters, numbers,
#      space, ASCII punctuation). EVERYTHING else (C0/C1 control, bidi, zero-width, invisible,
#      tag chars, private-use, surrogate) is REJECTED, not 'cleaned'.
#   2. WHAT IS SHOWN IS WHAT IS SIGNED: we do not sanitize the view and sign the raw bytes;
#      we REJECT non-conforming content and only sign already-valid content.

def contenido_seguro(s):
    """Returns (seguro: bool, motivos: list[str]). True if `s` contains ONLY whitelisted
    characters (printables + innocuous Unicode), without control/bidi/zero-width/invisible/tag.

    The WHITELIST is by Unicode category: letters (L*), combining marks (Mn/Mc/Me),
    numbers (N*), space (Zs), ASCII punctuation (0x20-0x7E). It ALSO REJECTS:
      - Default_Ignorable (U+034F, var selectors U+FE00-FE0F/U+E0100-E01EF, Hangul filler
        U+115F/U+1160/U+3164, etc.) — they are invisible even if their category is 'letter/mark'.
      - Non-standard spaces (NBSP U+00A0, ideographic U+3000, etc.) — confusables.
    """
    import unicodedata
    if not isinstance(s, str):
        s = str(s)
    motivos = []
    seguro = True
    # confusable spaces outside ASCII space + \t\r\n
    ESPACIOS_PROHIBIDOS = {'\u00a0', '\u2000', '\u2001', '\u2002', '\u2003', '\u2004',
                           '\u2005', '\u2006', '\u2007', '\u2008', '\u2009', '\u200a',
                           '\u202f', '\u205f', '\u3000'}
    for ch in s:
        o = ord(ch)
        # printable ASCII + \n\t\r: always safe
        if 0x20 <= o <= 0x7E or ch in "\n\t\r":
            continue
        # confusable spaces
        if ch in ESPACIOS_PROHIBIDOS:
            seguro = False
            motivos.append(f"U+{o:04X} (espacio confusable)")
            continue
        # Default_Ignorable: invisible even with category 'letter/mark/number' (Hangul filler,
        # var selectors, joiners). Checked BEFORE the category whitelist, because
        # these points are 'letter'/'mark' but invisible.
        if _es_default_ignorable(ch):
            seguro = False
            motivos.append(f"U+{o:04X} (Default_Ignorable invisible)")
            continue
        cat = unicodedata.category(ch)
        # Unicode WHITELIST: letters, marks, numbers, separator space
        if cat[0] in ("L", "N") or cat in ("Mn", "Mc", "Me", "Zs"):
            continue
        # everything else is REJECTED (Cc/Cf control, bidi, zero-width, invisible, Cs surrogate,
        # Co private-use, etc.)
        seguro = False
        motivos.append(f"U+{o:04X} ({cat})")
    return seguro, sorted(set(motivos))


def _es_default_ignorable(ch):
    """Is it Default_Ignorable? Covers var selectors (FE00-FE0F, E0100-E01EF), U+034F, Hangul
    filler (115F/1160/3164), joiners, etc. Rejection based on known ranges of Unicode
    Default_Ignorable_Code_Point, not on general category (which classifies them as letter/mark).
    """
    o = ord(ch)
    # Default_Ignorable_Code_Point (narrow catalog we want to block for signing)
    if 0xFE00 <= o <= 0xFE0F:  # variation selectors
        return True
    if 0xE0100 <= o <= 0xE01EF:  # variation selectors supplement
        return True
    if o in (0x034F,):  # combining grapheme joiner
        return True
    if o in (0x200B, 0x200C, 0x200D):  # zero-width space/joiner/non-joiner
        return True
    if o in (0x115F, 0x1160, 0x3164):  # Hangul fillers
        return True
    if o in (0x2060, 0xFEFF):  # word joiner, BOM/ZWNBSP
        return True
    return False


def _sanear_para_mostrar(s):
    """Compatibility: returns (clean, alert).

    Now uses WHITELIST: if `s` is NOT safe, we return the readable representation of the
    rejected characters (so the human sees WHY it was rejected), but the real contract is
    `contenido_seguro()` — the signer REJECTS non-conforming content, does not sign it 'clean'.
    """
    seguro, motivos = contenido_seguro(s)
    if seguro:
        return s, ""
    # we show the reasons for auditing (nothing is signed with unsafe content)
    return s, "RECHAZADO: " + "; ".join(motivos)


def _ensure_dir():
    os.makedirs(FA.DIR_CFG, exist_ok=True)


def cmd_generar_claves():
    # UID boundary: keys are generated in the signing user's home, not the agent's ~
    import firma_asimetrica as FA
    _priv, _pub = FA._rutas_frontera()
    if os.path.exists(_priv):
        print(f"ERROR: ya existe {_priv}. Para no pisar la clave, no se sobrescribe.",
              file=sys.stderr)
        sys.exit(2)
    priv_pem, pub_hex = FA.generar_par()
    os.makedirs(os.path.dirname(_priv), exist_ok=True)
    with open(_priv, "wb") as f:
        f.write(priv_pem)
    os.chmod(_priv, 0o600)
    os.makedirs(os.path.dirname(_pub), exist_ok=True)
    with open(_pub, "w") as f:
        f.write(pub_hex + "\n")
    os.chmod(_pub, 0o644)
    print(f"par generado:\n  privada -> {_priv} (600)\n  pública -> {_pub} (644)")
    print("Guardá la privada en un lugar seguro. El runtime SOLO necesita la pública.")


def _firmar_nucleo(clave_necesidad, tool_id, artifacto, ttl_seg, nonce):
    """SINGLE signing CORE (Fable item 6): serializes, validates content, and signs.

    Both paths (test batch and production interactive) converge here. It binds the COMPLETE
    CONTEXT: need, tool_id, serialized artifact (content), hash, absolute expiration,
    and nonce. Everything enters the signed message.
    """
    exp_absoluta = int(time.time()) + int(ttl_seg)
    canon_art = json.dumps({"tool_id": tool_id, "params": artifacto},
                           sort_keys=True, ensure_ascii=False, default=str)
    payload = {
        "necesidad": clave_necesidad,
        "tool_id": tool_id,
        "hash_accion": _hash_accion(tool_id, artifacto),
        "artifacto": canon_art,
        "exp_absoluta": exp_absoluta,
        "nonce": nonce,
    }
    # WHAT IS SHOWN IS WHAT IS SIGNED: validate content BEFORE signing.
    seguro, motivos = contenido_seguro(canon_art)
    if not seguro:
        return None, {"error": "contenido_no_seguro_rechazado", "motivos": motivos,
                       "tool_id": tool_id, "necesidad": clave_necesidad}
    firma = FA.firmar("autorizacion", payload)
    return ({"firma": firma, "nonce": nonce, "exp_absoluta": exp_absoluta,
             "payload": payload}, None)


def cmd_firmar(clave_necesidad, tool_id, artifacto_json, ttl_seg=DEFAULT_TTL):
    """Batch signing (TESTS ONLY). It is not the production path.

    Production uses `cmd_firmar_interactivo` (requires TTY + human confirmation). This function
    stays for tests and is not exposed as a CLI command (see __main__).
    """
    import uuid
    nonce = uuid.uuid4().hex
    artifacto = json.loads(artifacto_json)
    out, err = _firmar_nucleo(clave_necesidad, tool_id, artifacto, ttl_seg, nonce)
    if err:
        print(json.dumps(err, sort_keys=True, ensure_ascii=False), file=sys.stderr)
        sys.exit(3)
    print(json.dumps(out, sort_keys=True, ensure_ascii=False))


def _hash_accion(tool_id, artifacto):
    import hashlib
    canon = json.dumps({"tool_id": tool_id, "params": artifacto},
                       sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE SIGNING (P0 Fable 4th revision): 'human confirmation must happen
# on the signer's side, in a TTY that the agent does not control'.
# ─────────────────────────────────────────────────────────────────────────────
def _es_tty(fd=0):
    """Is stdin an interactive TTY? If not, it CANNOT sign (not injectable via pipe)."""
    import os
    try:
        return os.isatty(fd)
    except (OSError, ValueError):
        return False


def cmd_firmar_interactivo(clave_necesidad, tool_id, artifacto_json, ttl_seg=DEFAULT_TTL):
    """Signs an action WITH HUMAN CONFIRMATION in a TTY that the agent does not control.

    Fable P0: the runtime/agent must NOT be able to ask the signer to sign via IPC/sudo. Here:
      1. We require stdin TTY (if not a TTY, we REJECT: cannot be injected via pipe).
      2. We show the readable content (canon of the artifact) to the human.
      3. We ask for explicit confirmation; we ONLY sign if the human types 'si'/'sí'/'s'.
    It does not sign in batch mode: there is always a human confirmation at the moment.
    """
    import uuid
    if not _es_tty(0):
        print(json.dumps({"error": "requiere TTY interactiva (no se puede firmar por pipe)",
                          "tool_id": tool_id, "necesidad": clave_necesidad},
                         sort_keys=True, ensure_ascii=False), file=sys.stderr)
        sys.exit(4)

    artifacto = json.loads(artifacto_json)

    # Signing ceremony (Fable ruling §3/§5): if the artifact is an egress INTENT
    # (http/mcp), it is NORMALIZED to the canonical intent and rendered LEGIBLE (registrable domain
    # highlighted, punycode, full query). What is shown = what is signed: the render is of the SAME
    # canonical object that is signed, with no hidden, summarized, or truncated fields.
    es_intent = isinstance(artifacto, dict) and artifacto.get("tipo") in ("http", "mcp")
    if es_intent:
        import render_firma
        try:
            artifacto = render_firma.normalizar_intent(artifacto)
        except Exception as e:
            print(json.dumps({"error": "intent_invalido", "detalle": str(e),
                              "tool_id": tool_id, "necesidad": clave_necesidad},
                             sort_keys=True, ensure_ascii=False), file=sys.stderr)
            sys.exit(3)
        mostrar = render_firma.render_ceremonia(clave_necesidad, tool_id, artifacto)
    else:
        canon_art = json.dumps({"tool_id": tool_id, "params": artifacto},
                               sort_keys=True, ensure_ascii=False, default=str)
        seguro, motivos = contenido_seguro(canon_art)
        if not seguro:
            print(json.dumps({"error": "contenido_no_seguro_rechazado", "motivos": motivos,
                              "tool_id": tool_id, "necesidad": clave_necesidad},
                             sort_keys=True, ensure_ascii=False), file=sys.stderr)
            sys.exit(3)
        mostrar = (f"=== FIRMA HUMANA (confirme lo que firma) ===\n"
                   f"necesidad : {clave_necesidad}\n"
                   f"tool_id   : {tool_id}\n"
                   f"contenido : {canon_art}\n"
                   "===========================================")

    # show the human WHAT it will sign (what is shown = what is signed)
    print(mostrar, file=sys.stderr)
    # read confirmation from the controlling TTY (extra defense: not just stdin)
    try:
        with open("/dev/tty", "r") as tty:
            tty.write("¿Firmar esta acción? (sí/no): ")
            tty.flush()
            confirmacion = tty.readline().strip().lower()
    except (OSError, EOFError):
        confirmacion = ""
    if confirmacion not in ("si", "sí", "s", "y", "yes"):
        print(json.dumps({"error": "firma cancelada por el humano", "tool_id": tool_id,
                          "necesidad": clave_necesidad}, sort_keys=True, ensure_ascii=False),
              file=sys.stderr)
        sys.exit(5)

    # SINGLE CORE: sign with the complete context (what was shown is what is signed)
    nonce = uuid.uuid4().hex
    out, err = _firmar_nucleo(clave_necesidad, tool_id, artifacto, ttl_seg, nonce)
    if err:
        print(json.dumps(err, sort_keys=True, ensure_ascii=False), file=sys.stderr)
        sys.exit(3)
    print(json.dumps(out, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("uso: cli_firma_humana.py generar-claves | firmar-interactivo <necesidad> <tool_id> <artifacto_json> [ttl_seg]",
              file=sys.stderr)
        sys.exit(2)
    if args[0] == "generar-claves":
        cmd_generar_claves()
    elif args[0] == "firmar-interactivo":
        if len(args) < 4:
            print("uso: ... firmar-interactivo <necesidad> <tool_id> <artifacto_json> [ttl_seg]", file=sys.stderr)
            sys.exit(2)
        ttl = int(args[4]) if len(args) > 4 else DEFAULT_TTL
        cmd_firmar_interactivo(args[1], args[2], args[3], ttl)
    else:
        # the 'firmar' batch path is NOT exposed as a production command (Fable item 3).
        print(f"comando desconocido (solo generar-claves y firmar-interactivo): {args[0]}", file=sys.stderr)
        sys.exit(2)
