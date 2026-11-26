#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""gobernanza_accion.py — `necesidad` and `autorizacion` primitives (Fable P0).

Closes the gap of a goal-executor WITHOUT a planner: what was missing was for the system to
DISTINGUISH between 'describing a gap' and 'ACTING on it', and for no effectful action to run
without an explicit, current, single-use human PERMISSION.

Names (Fable): the intentions/permissions namespace is SEPARATE from beliefs (claims), because they
are 'I want / I am allowed', not 'I know / such thing is true'.

  necesidad:<id>    modelo or capacidades.py  -> what is missing + RISK CLASS
  autorizacion:<cid>  ONLY human (HMAC signature) -> hash(action+params), single use, expiry

SIGNATURE (NOT an arbitrary string): the authorization is signed with Ed25519 (asymmetric
signature). The RUNTIME only holds the PUBLIC KEY (`firma_asimetrica.cargar_publica`), with which
it can VERIFY but NOT SIGN. The PRIVATE key lives outside the repo and outside the agent process
(`~/.config/hcma/humano_ed25519.key`), only touched by the human's CLI. Thus the LLM/runtime cannot
self-consent: it can verify signatures, never issue them.

Default-deny invariant: no effectful connector runs without a current `autorizacion` whose HMAC
signature is VALID and whose action hash matches EXACTLY the action and its parameters. The check
lives in the `conectores.py` dispatcher, not in the planner.

Risk classes (STATIC table per connector, written by a human, never by the LLM):
  0 = pure/sandbox (no effects)           -> does not ask for authorization
  1 = reversible/bounded (temp files)     -> groupable by objective
  2 = install/network/money/proprietary data/long compute -> individual, never remembered

Fable principles applied:
  - 'autorizar' is NOT 'promover': permission is orthogonal to epistemic grade.
  - The use destination is single-use + expiry; if the action changes, it asks again.
  - No answer -> blocked (does not self-consent).
"""
import hashlib
import json
import os
import sys
import time

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import firma_asimetrica as FA  # noqa: E402  (Ed25519: el runtime SOLO verifica)
from firma_asimetrica import generar_par  # noqa: E402  (solo para testing/demo)


# Risk classes (increasing danger order)
RIESGO_PURO = 0
RIESGO_REVERSIBLE = 1
RIESGO_EXTERNO = 2

RIESGO_VALIDO = (RIESGO_PURO, RIESGO_REVERSIBLE, RIESGO_EXTERNO)


# ─────────────────────────────────────────────────────────────────────────────
# Canonicalization of an action (for the authorization hash)
# ─────────────────────────────────────────────────────────────────────────────
def canon_accion(tool_id, artifacto):
    """Stable serialization of the action and its parameters, for hashing."""
    return json.dumps({"tool_id": tool_id, "params": artifacto},
                      sort_keys=True, ensure_ascii=False, default=str)


def hash_accion(tool_id, artifacto):
    """Deterministic hash of the exact action (tool_id + params)."""
    return hashlib.sha256(canon_accion(tool_id, artifacto).encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Ed25519 signature of the action (asymmetry: the runtime ONLY verifies, does not sign)
# ─────────────────────────────────────────────────────────────────────────────
def _payload_accion(clave_necesidad, tool_id, artifacto, exp_absoluta, nonce, epoca=None):
    """Canonical signed payload. EVERYTHING enters the signature: the ABSOLUTE EXPIRATION, the
    SERIALIZED ARTIFACT (real content, not just its hash), its hash (for convenience), the NONCE
    and the EPOCH (anti-replay binding against host restore). Including the artifact breaks the
    'blind signature' (Fable P0).

    ABLATION: if HASH_ARTEFACTO is off, the artifact and the hash are NOT included.
    If `epoca` is passed, it enters the signed payload (anti-restore binding); if None, it is not
    bound.
    """
    import ablacion as AB
    payload = {
        "necesidad": clave_necesidad,
        "tool_id": tool_id,
        "exp_absoluta": int(exp_absoluta),
        "nonce": nonce,
    }
    if AB.activo(AB.HASH_ARTEFACTO):
        # ACTIVE defense: the signature covers the content (hash + artifact)
        payload["hash_accion"] = hash_accion(tool_id, artifacto)
        payload["artifacto"] = canon_accion(tool_id, artifacto)
    if epoca is not None:
        payload["epoca"] = int(epoca)
    return payload


def firmar_accion(clave_necesidad, tool_id, artifacto, ttl_seg=3600, nonce=None, epoca=None):
    """Ed25519 signature of an action. Used ONLY by the HUMAN CLI (it has the private key).
    The runtime can NOT call it (it does not hold the private key). Returns (firma, nonce,
    exp_absoluta). `nonce` = uuid4 hex (generated by the human CLI); `epoca` (optional) binds the
    signature to the current epoch (anti-restore binding)."""
    if nonce is None:
        import uuid
        nonce = uuid.uuid4().hex
    exp_absoluta = int(time.time()) + int(ttl_seg)
    payload = _payload_accion(clave_necesidad, tool_id, artifacto, exp_absoluta, nonce, epoca)
    return FA.firmar("autorizacion", payload), nonce, exp_absoluta


def verificar_accion(clave_necesidad, tool_id, artifacto, exp_absoluta, firma, nonce, epoca=None):
    """Cryptographically verifies the Ed25519 signature OF THE ACTION with the PUBLIC key.
    False on any payload/expiration/nonce/epoch change, or a forged signature."""
    payload = _payload_accion(clave_necesidad, tool_id, artifacto, exp_absoluta, nonce, epoca)
    return FA.verificar("autorizacion", payload, firma)


# ─────────────────────────────────────────────────────────────────────────────
# necesidad (can be written by the model or capacidades.py)
# ─────────────────────────────────────────────────────────────────────────────
def declarar_necesidad(m, id_objetivo, herramienta, descripcion, riesgo, detalle=None,
                      session_id=None):
    """Writes a CREATE-ONLY `necesidad` as a grade <=1 proposal. Returns (key, version).

    Fable RULING (session/authorization): the need must be IMMUTABLE and have a UNIQUE id minted by
    the monitor (a second agent can NOT redeclare the same key and overwrite the first one's
    session). Therefore:
      - `session_id` is OPTIONAL and injected by the transport (the event loop), NEVER declared by
        the agent in the value. If the agent tries to set its own session, it is ignored here (there
        is no field in the value for it).
      - The key is UNIQUE per declaration (a uuid minted by the monitor), not collidable by
        `id_objetivo:herramienta`.
      - If the key already exists, it is REJECTED (create-only): a previous need is not overwritten.

    `riesgo` ∈ {0,1,2} according to the static table. It is written by the model or capacidades.py,
    NOT the human. It is an OPEN PRECONDITION, not a permission.
    """
    if riesgo not in RIESGO_VALIDO:
        raise ValueError(f"clase de riesgo inválida: {riesgo}")
    import uuid as _uuid
    id_necesidad = _uuid.uuid4().hex  # minted by the monitor (unique and unpredictable)
    key = f"necesidad:{id_necesidad}"
    # CREATE-ONLY: if it already exists (extremely improbable collision or redeclare), reject.
    if m.current(key) is not None:
        raise ValueError(f"necesidad ya existe (create-only): {key}")
    v = m.write(key, {
        "id_necesidad": id_necesidad,
        "id_objetivo": id_objetivo,
        "herramienta": herramienta,
        "descripcion": descripcion,
        "riesgo": riesgo,
        "detalle": detalle or "",
        "estado": "abierta",
        # session_id minted by the transport (never from the agent's value). None if absent.
        # It is NOT part of the signature: it is declaration metadata, not authorization metadata.
        "session_id": session_id,
    }, unit="", fuente="modelo", verified=False)
    return key, v


def necesidades_abiertas(m, id_objetivo=""):
    """Returns the open needs for an objective (or all if id is empty).

    The need is unverified (model proposal), so we use `keys_crudas` (internal audit) to LIST them
    and `current_raw` (LAST version without authority filter) to read them. The objective→need link
    lives in the VALUE (`id_objetivo`), not in the key (which is `necesidad:<uuid>` since P0#3
    milestone 3a)."""
    out = []
    for k in m.keys_crudas("necesidad:"):
        f = m.current_raw(k)
        if f is None:
            continue
        val = f.get("value") or {}
        if id_objetivo and val.get("id_objetivo") != id_objetivo:
            continue
        if val.get("estado") == "abierta":
            out.append({"key": k, "herramienta": val.get("herramienta"),
                        "riesgo": val.get("riesgo"),
                        "descripcion": val.get("descripcion")})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# autorizacion (ONLY human, Ed25519 signature verifiable with public key)
# ─────────────────────────────────────────────────────────────────────────────
def autorizar(m, clave_necesidad, tool_id, artifacto, firma, ttl_seg=3600,
              canal="humano", nonce=None, epoca=None):
    """Issues a SINGLE-use `autorizacion`, bound to the action hash, the NONCE, the EPOCH
    (optional, anti-restore binding) and the Ed25519 signature. If `nonce` is None, a deterministic
    nonce is derived as a fallback (testing only).

    Fable RULING (session/authorization): the SESSION is copied from the NEED (not received as an
    agent parameter), and stays FIXED in the authorization. The authorization→need link remains
    AUTHENTICATED because the signature covers `clave_necesidad` (which is
    `necesidad:<id_acuñado>`, unique and immutable). This avoids the TOCTOU of dereferencing the
    need at consumption.

    `firma` is VERIFIED against the PUBLIC key. Returns (key, version) or None.
    """
    if canal != "humano":
        return None  # only the human can authorize
    exp_absoluta = int(time.time()) + int(ttl_seg)
    if nonce is None:
        # stable fallback: the nonce was not in the payload, we derive it from the hash (test only)
        nonce = hashlib.sha256(firma.encode()).hexdigest()[:24]
    if not verificar_accion(clave_necesidad, tool_id, artifacto, exp_absoluta, firma, nonce, epoca):
        return None  # invalid/forged signature -> default-deny
    # session: copied from the NEED (immutable), not received from the agent. If the need does not
    # exist or has no session, the authorization remains WITHOUT session (fail-closed at consumption).
    session_id = _session_de_necesidad(m, clave_necesidad)
    cid = hashlib.sha256(
        (clave_necesidad + "|" + hash_accion(tool_id, artifacto)).encode()).hexdigest()[:16]
    key = f"autorizacion:{cid}"
    v = m.write(key, {
        "necesidad": clave_necesidad,
        "tool_id": tool_id,
        "hash_accion": hash_accion(tool_id, artifacto),
        "firma": firma,
        "nonce": nonce,
        "epoca": epoca,
        "exp_absoluta": exp_absoluta,
        "emitida": time.time(),
        "ttl_seg": ttl_seg,
        "usada": 0,
        # FIXED session in the authorization (copied from the need, not from the agent).
        "session_id": session_id,
    }, unit="", fuente="usuario", verified=True, meta={"tipo": "autorizacion"})
    return key, v


def _session_de_necesidad(m, clave_necesidad):
    """Reads the FIXED session_id in the need (by its last version), or None if it does not exist.

    The need is create-only (milestone 3a): its session_id is immutable. If the key does not exist
    or has no session_id, returns None (the authorization remains without session, and consumption
    will be fail-closed)."""
    if m is None or clave_necesidad is None:
        return None
    row = m.conn.execute(
        "SELECT value FROM facts WHERE key=? ORDER BY version DESC LIMIT 1",
        (clave_necesidad,)).fetchone()
    if row is None:
        return None
    try:
        val = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    except (ValueError, TypeError):
        return None
    return (val or {}).get("session_id")


def autorizacion_vigente(m, tool_id, artifacto, epoca_vigente=None, session_id=None):
    """Returns the current and unused authorization key for the action, or None.

    Re-verifies the Ed25519 signature (does not trust stored data), checks the signed ABSOLUTE
    expiration, and applies EPOCH BINDING by STRICT EQUALITY (Fable 7th revision): an authorization
    issued in an epoch different from the current one is rejected.

    Fable RULING (session/authorization): filters by SESSION_ID (session binding). The session is
    FIXED in the authorization (copied from the need at authorization time). If `session_id` is
    None (v1, backward compatibility), it is NOT filtered (legacy behavior); once P0 #3 is fully
    active, the caller ALWAYS passes the transport session.

    Fail-closed rule: if the authorization has an epoch but NO current epoch is available
    (epoca_vigente=None and the epoch file does not exist), it is REJECTED (it is not let through
    without verifying). default-deny.
    """
    import ablacion as AB
    ha = hash_accion(tool_id, artifacto)
    keys = m.keys("autorizacion:") or []
    if not keys:
        return None
    now = time.time()
    for k in keys:
        f = m.current(k)
        if f is None:
            continue
        val = f.get("value") or {}
        if val.get("tool_id") != tool_id:
            continue
        # SESSION BINDING (P0 #3): the authorization is bound to a FIXED session. If the caller
        # asks to filter by session (session_id not-None) and it does not match, it is discarded.
        if session_id is not None and val.get("session_id") != session_id:
            continue
        # hash_accion ABLATION: if off, the hash match is IGNORED -> an authorization signed for
        # ONE artifact is valid for ANOTHER (artefacto_cambiado attack).
        if AB.activo(AB.HASH_ARTEFACTO):
            if val.get("hash_accion") != ha:
                continue
        if val.get("usada"):
            continue
        # signed ABSOLUTE expiration (not extensible 'emitida+ttl'). ABLATION: switchable off.
        if AB.activo(AB.EXPIRACION_ABSOLUTA):
            exp_absoluta = val.get("exp_absoluta", 0)
            if now > exp_absoluta:
                continue
        # re-verify the signature cryptographically against the current public key
        if not verificar_accion(val.get("necesidad", ""), tool_id, artifacto,
                                val.get("exp_absoluta", 0), val.get("firma", ""),
                                val.get("nonce", ""), val.get("epoca")):
            continue
        # epoch BINDING by STRICT EQUALITY (Fable 7th + milestone 3 P1):
        #   - The authorization MUST carry the exact current epoch when there is a current epoch.
        #   - P1 (Fable milestone 3): an authorization WITHOUT epoch (epoca=None) was a wildcard
        #     valid in all epochs. Closure: IF there is a READABLE current epoch (production), an
        #     auth. without epoch is REJECTED. If there is NO epoch (missing file = dev), it is
        #     allowed (legacy).
        epoca_auth = val.get("epoca")
        # determine the current epoch (if the caller did not pass it, try reading it fresh)
        if epoca_vigente is None:
            import epoca as EP
            try:
                epoca_vigente = EP.epoca_actual()
            except EP.EpocaError:
                # no readable current epoch => dev mode without epoch binding (legacy allowed)
                epoca_vigente = None
        if epoca_vigente is not None:
            # there is a current epoch: the auth. MUST carry the same one (and not None)
            if epoca_auth is None:
                continue  # auth. without epoch + current epoch present => rejection (P1 closure)
            if int(epoca_auth) != int(epoca_vigente):
                continue
        # anti-replay by NONCE: if the nonce was already consumed (even if usada=0 due to restore),
        # we reject. The nonce is the real spending unit, not the mutable flag. ABLATION: switchable
        # off.
        if AB.activo(AB.NONCE_CAS):
            if m.nonce_consumido(val.get("nonce", "")):
                continue
        return k
    return None


def autorizacion_consumida(m, tool_id, artifacto):
    """Returns True if there is an authorization for this action that was ALREADY consumed
    (usada=1).

    Useful to distinguish 'there was never permission' from 'the permission was already spent'
    (dashboard diagnostic). Pure read, does not change state.
    """
    ha = hash_accion(tool_id, artifacto)
    keys = m.keys("autorizacion:") or []
    for k in keys:
        f = m.current(k)
        if f is None:
            continue
        val = f.get("value") or {}
        if val.get("tool_id") == tool_id and val.get("hash_accion") == ha and val.get("usada"):
            return True
    return False


def marcar_usada(m, clave_autorizacion, session_id=None):
    """Marks an authorization as consumed (single use) ATOMICALLY (flag + nonce).

    Fable P (consume/effect order + atomicity): the `usada` flag and the nonce are consumed in a
    SINGLE transaction (`consumir_autorizacion_atomica`). Returns True only if BOTH passed (neither
    flag already used, nor nonce already seen). The CAS is the GATE: it is consumed BEFORE running
    the effect, and if it fails the effect does not run (no double spend, no window between verify
    and consume).

    Fable RULING (session/authorization): if `session_id` is not None, the session is a PREDICATE
    of the SAME consumption CAS. An attempt from a non-matching session consumes NOTHING (the
    conditional UPDATE fails and there is total ROLLBACK).
    """
    f = m.current(clave_autorizacion)
    if f is None:
        return False
    val = f.get("value") or {}
    nonce = val.get("nonce", "")
    tool_id = val.get("tool_id", "")
    ha = val.get("hash_accion", "")
    # consumir_autorizacion_atomica ABLATION (CAS): if off, reuse (replay) is allowed by returning
    # True whenever the authorization exists (without setting the usada flag).
    import ablacion as AB
    if not AB.activo(AB.CAS_ATOMICO):
        return True  # reuse allowed: enables the replay attack
    return m.consumir_autorizacion_atomica(clave_autorizacion, nonce, tool_id, ha,
                                           session_id=session_id)


if __name__ == "__main__":
    from memoria_sqlite import HCMAMemoriaSQLite
    import uuid
    # self-contained demo: generates an ephemeral pair and exposes it via test env, then signs/verifies
    priv_pem, pub_hex = generar_par()
    os.environ["HCMA_HUMANO_ED25519_PUB"] = pub_hex
    # (in production the private key is outside the runtime; here we simulate the human's CLI)
    import firma_asimetrica as FA
    def _firmar_demo(payload):
        priv = FA._cargar_privada_ed25519(priv_pem)
        return priv.sign(FA.mensaje_accion("autorizacion", payload)).hex()

    m = HCMAMemoriaSQLite(":memory:")
    k, v = declarar_necesidad(m, "eva", "openfoam", "simular espumado", RIESGO_EXTERNO)
    print("necesidad:", k)
    art = {"netlist": {}}
    print("autorización vigente (sin autorizar):", autorizacion_vigente(m, "openfoam", art))
    nonce = uuid.uuid4().hex
    exp = int(time.time()) + 3600
    payload = _payload_accion(k, "openfoam", art, exp, nonce)
    firma = _firmar_demo(payload)
    res = autorizar(m, k, "openfoam", art, firma, ttl_seg=3600, nonce=nonce)
    ak = res[0] if res else None
    print("autorización vigente (firma Ed25519 válida):", autorizacion_vigente(m, "openfoam", art))
    # forged signature => default-deny
    m2 = HCMAMemoriaSQLite(":memory:")
    k2, _ = declarar_necesidad(m2, "eva", "openfoam", "simular espumado", RIESGO_EXTERNO)
    print("autorización con firma forjada:", autorizar(m2, k2, "openfoam", art, "f" * 128, nonce="x"*24))
    marcar_usada(m, ak)
    print("tras usar (un solo uso):", autorizacion_vigente(m, "openfoam", art))
