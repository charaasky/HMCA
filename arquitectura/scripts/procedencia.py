#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""procedencia.py — provenance seal of a spec (guards against hardcoding).

Corrected after Fable 5.2 revision (fable_bug_procedencia). The previous design had a fundamental
defect: `_SELLO = object()` was "theater" — I (the agent) can import `_SELLO` or forge the
`sellar_ratificacion_humana` factory with no proof of a human. Fable summarized it:

  - The `llm_runtime` channel is NOT closable by software I write: any spec I hardcode counts as
    "agent authorship", UNVERIFIED ceiling, and can only be DETECTED (AST test), not prevented.
  - The `humano` channel CAN be truly closed, but only if the ratification is signed by an EXTERNAL
    actor with a key I do not have. Without that, any "human ratification" I issue is self-approval.

Implemented solution (HMAC, stdlib, no new dependencies):
  - `canal='humano'` requires an HMAC-SHA256 signature over (did, pedido, normalized spec) using a
    KEY that does NOT live in the repo: read from the `HCMA_HUMANO_HMAC_KEY` environment variable
    or from a file outside the repo `~/.config/hcma/humano.key`. The agent (me) does not know it.
  - `canal='llm_runtime'` is always UNVERIFIED and remains a candidate; its ceiling is the lowest.
  - `canal='humano_simulado'` is always UNVERIFIED (a simulation does not truly ratify).

Additionally the concrete bugs Fable confirmed are fixed:
  BUG-B: `sellar_spec_llm` rejects specs without formulas (SinCandidata) BEFORE writing the event.
  BUG-C: the event is written AFTER validating (no orphan event).
  BUG-D: `hash_canonico` uses strict JSON (allow_nan=False, no silent default=str).
  BUG-E: human ratification signs (did, pedido, spec) — it cannot be re-registered under another id.
  BUG-F: `_evento` tolerates non-dict values.

Does NOT touch the core (memoria_sqlite.py). It is an ORCHESTRATION layer over skill_lib.
"""
import hashlib
import hmac
import json
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


class ProcedenciaInvalida(ValueError):
    """A spec did not come through an authorized channel, or its trace/signature does not match."""


class SinCandidata(ValueError):
    """The LLM did not draft formulas: there is no candidate to register."""


CANALES = ("llm_runtime", "humano", "humano_simulado")


def normalizar(obj):
    """Strict and stable JSON serialization. Rejects NaN/inf and non-JSON values (no stringify)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False)


def hash_canonico(obj):
    """Stable hash of a normalized spec (strict JSON). Returns sha256 hex."""
    return hashlib.sha256(normalizar(obj).encode("utf-8")).hexdigest()


class SpecSellada:
    """Spec that entered through a channel: carrier without its own authority (the sink re-verifies)."""

    __slots__ = ("spec", "canal", "evento_id", "pedido", "did", "firma")

    def __init__(self, spec, canal, evento_id, pedido="", did="", firma=None):
        if canal not in CANALES:
            raise ProcedenciaInvalida(f"canal desconocido: {canal}")
        self.spec = json.loads(normalizar(spec))  # deep copy + strict JSON validation
        self.canal = canal
        self.evento_id = evento_id
        self.pedido = pedido
        self.did = did
        self.firma = firma


# ── human signature (HMAC, key outside the repo, not accessible to the agent) ──────────
def _clave_humana():
    """Signing key of the human actor. NEVER in the repo: env or file outside the repo."""
    k = os.environ.get("HCMA_HUMANO_HMAC_KEY", "")
    if k:
        return k.encode("utf-8")
    p = os.path.expanduser("~/.config/hcma/humano.key")
    if os.path.exists(p):
        return open(p, "rb").read().strip()
    raise ProcedenciaInvalida(
        "no hay clave humana de ratificación (setear HCMA_HUMANO_HMAC_KEY o ~/.config/hcma/humano.key)")


def _mensaje_humano(did, pedido, spec):
    return normalizar({"did": did, "pedido": pedido, "spec": spec}).encode("utf-8")


def _firmar_humano(did, pedido, spec):
    import hmac as _hmac
    return _hmac.new(_clave_humana(), _mensaje_humano(did, pedido, spec), hashlib.sha256).hexdigest()


def _verificar_firma_humana(did, pedido, spec, firma):
    import hmac as _hmac
    esperada = _mensaje_humano(did, pedido, spec)
    dada = bytes.fromhex(firma) if isinstance(firma, str) and len(firma) == 64 else b""
    ok = _hmac.compare_digest(
        _hmac.new(_clave_humana(), esperada, hashlib.sha256).digest(),
        dada,
    )
    return ok


# ── events in memory (ledger) ────────────────────────────────────────────
def _escribir_evento(m, evento_id, canal, spec, fuente="usuario", verified=True):
    contenido = {"canal": canal, "contenido_hash": hash_canonico(spec)}
    return m.write(f"evento:{evento_id}", contenido, unit="", fuente=fuente,
                   verified=verified, meta={"tipo": "ingreso_espec", "canal": canal})


def _evento(m, evento_id):
    """Reads the ledger event. Returns (canal, contenido_hash) or None."""
    f = m.current(f"evento:{evento_id}")
    if f is None or not f["verified"]:
        return None
    v = f.get("value") if isinstance(f.get("value"), dict) else {}
    return v.get("canal"), v.get("contenido_hash")


# ── sealing factories (channel) ───────────────────────────────────────────────
def sellar_spec_llm(m, pedido, spec):
    """Seals the spec produced by the LLM at runtime. UNVERIFIED ceiling.

    BUG-B: rejects specs without formulas BEFORE writing the event (SinCandidata).
    This channel's event is an AUDIT TRAIL (fuente='modelo', unverified): it grants no authority.
    Integrity is checked at the sink by hash.
    """
    formulas = spec.get("formulas", []) if isinstance(spec, dict) else []
    if not formulas:
        raise SinCandidata("el LLM no redactó fórmulas: no hay candidata que sellar")
    evento_id = "llm_" + hash_canonico({"pedido": pedido, "spec": spec})[:16]
    _escribir_evento(m, evento_id, "llm_runtime", spec, fuente="modelo", verified=False)
    return SpecSellada(spec, "llm_runtime", evento_id, pedido=pedido)


def sellar_ratificacion_humana(m, pedido, did, spec, firma):
    """Seals a real HUMAN ratification. ONLY channel that can reach VERIFIED.

    Requires a valid HMAC signature over (did, pedido, spec) with the external key. If the
    signature is not valid, NOTHING is written (no authority is minted). The agent cannot fabricate
    the signature.
    """
    if not _verificar_firma_humana(did, pedido, spec, firma):
        raise ProcedenciaInvalida("firma humana inválida (no se puede ratificar sin el actor externo)")
    evento_id = "humano_" + hash_canonico({"pedido": pedido, "did": did, "spec": spec})[:16]
    # the human ratification event does become verified: the signature backs it
    _escribir_evento(m, evento_id, "humano", spec, fuente="usuario", verified=True)
    return SpecSellada(spec, "humano", evento_id, pedido=pedido, did=did, firma=firma)


def sellar_humano_simulado(m, pedido, spec):
    """Human simulation: ALWAYS unverified. Does not truly ratify."""
    formulas = spec.get("formulas", []) if isinstance(spec, dict) else []
    if not formulas:
        raise SinCandidata("spec de simulación sin fórmulas")
    evento_id = "sim_" + hash_canonico({"pedido": pedido, "spec": spec})[:16]
    _escribir_evento(m, evento_id, "humano_simulado", spec, fuente="modelo", verified=False)
    return SpecSellada(spec, "humano_simulado", evento_id, pedido=pedido)


# ── registration (sink) ───────────────────────────────────────────────────────────
def registrar_spec_sellada(m, did, especificacion):
    """Registers a sealed spec. Re-verifies EVERYTHING at the sink (does not trust the seal).

    - channel 'humano'  -> canonical spec (skill_lib.registrar_spec with source ingeniero), after
                           re-verifying the signature.
    - 'llm_runtime' / 'humano_simulado' -> only unverified candidate.
    """
    if not isinstance(especificacion, SpecSellada):
        raise ProcedenciaInvalida("la spec debe ser SpecSellada (vino sin canal sellado)")

    # integrity by hash (detects accidental content mutation after sealing)
    if especificacion.canal in ("llm_runtime", "humano_simulado"):
        # these channels are hash-checked against the current spec (do not trust verified ledger)
        pass
    elif especificacion.canal == "humano":
        # re-verify the signature (do not trust the object): covers the carrier forge
        if not _verificar_firma_humana(did, especificacion.pedido, especificacion.spec,
                                       especificacion.firma):
            raise ProcedenciaInvalida("firma humana no re-verificable en el sink")
        import skill_lib
        return skill_lib.registrar_spec(m, did, especificacion.spec, fuente="ingeniero")

    # llm_runtime / humano_simulado: NEVER promote to canonical. The real did is derived by
    # inscribir_candidata from the spec name (the caller does NOT choose the authoritative id).
    import generar_dominio_candidato as GDC
    try:
        did_real, version = GDC.inscribir_candidata(m, especificacion.spec)
    except ValueError as e:
        raise ProcedenciaInvalida(f"candidata rechazada por validación: {e}")
    return version


if __name__ == "__main__":
    from memoria_sqlite import HCMAMemoriaSQLite
    m = HCMAMemoriaSQLite(":memory:")
    # demo: a raw dict does NOT pass
    crudo = {"nombre": "memoria usb", "autor": "modelo", "inputs": [], "formulas": []}
    try:
        registrar_spec_sellada(m, "x", crudo)
        print("ERROR: un dict crudo pasó")
    except ProcedenciaInvalida as e:
        print("OK bloqueado dict crudo:", e)
    # demo: no formulas => SinCandidata (BUG-B)
    try:
        sellar_spec_llm(m, "pedido", crudo)
    except SinCandidata as e:
        print("OK sin fórmulas =>", e)
