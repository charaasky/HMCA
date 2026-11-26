#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""atribucion_enunciado.py — Arm C: STATEMENT integrity through attestation.

The conector_lean (Arm B) verifies `prueba : enunciado`, but TRUSTS that the `enunciado` it
receives is the one the user requested. The kernel CANNOT detect a weakened statement: if the
LLM changes the theorem to a trivial one and gives a valid proof of that trivial one, the kernel says
DEMOSTRADO. (Fable 5.2 ruling: "what survives the kernel is the statement or the altered
definitions and the environment exploits".)

Arm C binds the statement to an ATTESTED ASSERTION (hash + frozen provenance). Before verifying,
it checks that the current statement/context match the attestation. If not, it rejects
(ENUNCIADO_NO_ATESTADO) EVEN IF the proof compiles.

This is HCMA's REAL contribution versus the connector: not the correctness of the proof (already from the
kernel), but the INTEGRITY OF THE ASSERTION and the reduction of the audit surface.
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import verificador_lean as VL  # noqa: E402


def _hash(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def hash_enunciado(enunciado):
    """Identity hash of the statement (same canon as the connector: sha256 of the text)."""
    return _hash((enunciado or "").strip())


def atestar(procedencia, enunciado, contexto=""):
    """Freezes the assertion (statement + context) with its run provenance.

    Returns the attestation {procedencia, hash_enunciado, hash_contexto}."""
    return {
        "procedencia": procedencia or {},
        "hash_enunciado": hash_enunciado(enunciado),
        "hash_contexto": hash_enunciado(contexto) if contexto else "",
    }


def verificar_atestado(atestado, enunciado, contexto=""):
    """Verifies that the current statement/context match the attestation.

    Returns (ok, motivo). The failure reason is the weakening/substitution of the statement."""
    if not isinstance(atestado, dict):
        return False, "sin atestado (Arm C exige aserción atestada)"
    if atestado.get("hash_enunciado") != hash_enunciado(enunciado):
        return False, "el enunciado no coincide con la aserción atestada (posible debilitamiento)"
    if atestado.get("hash_contexto", "") != (hash_enunciado(contexto) if contexto else ""):
        return False, "el contexto no coincide con la aserción atestada"
    return True, ""


def arm_c(atestado, artifacto):
    """Arm C = conector_lean + integrity of the attested statement.

    `atestado` = atestar(procedencia, enunciado_gold, contexto). The artifacto brings the statement
    that the LLM proposed to verify; if it does not match the attested gold, it is rejected."""
    enunciado = (artifacto.get("enunciado") or "").strip()
    contexto = (artifacto.get("contexto") or "").strip()
    ok, motivo = verificar_atestado(atestado, enunciado, contexto)
    if not ok:
        return {"ok": False, "estado": "ENUNCIADO_NO_ATESTADO", "axiomas": None,
                "detalle": motivo, "herramienta": "lean",
                "observables": {"procedencia": atestado.get("procedencia", {})}}
    r = VL.conector_lean(artifacto)
    if r.get("observables") is not None:
        r["observables"]["procedencia"] = atestado.get("procedencia", {})
    return r


if __name__ == "__main__":
    import json
    # demo: attest a gold statement and verify a weakened statement
    gold = "∀ p q : Prop, p ∧ q → q ∧ p"
    at = atestar({"run_id": "demo", "modelo": "deterministico"}, gold)
    print("atestado:", json.dumps(at, ensure_ascii=False))
    print("debilitado rechazado:", arm_c(at, {"nombre": "t", "enunciado": "True", "prueba": "trivial"})["estado"])
    print("gold aceptado:", arm_c(at, {"nombre": "t", "enunciado": gold,
                                       "prueba": "fun p q h => And.intro h.right h.left"})["estado"])
