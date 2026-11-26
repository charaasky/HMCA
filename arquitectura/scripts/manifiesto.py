#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""manifiesto.py — SINGLE human-signed manifest, covering ALL artifacts.

Corrects Fable's P0 (fable_revision_integral): 'the signature covers only the graph, but grades
3-4 depend on content nobody signed'.

The human does NOT sign each piece separately (that enabled raising the grade with a signature
that did not see the BOM). They sign ONE manifest covering: domain, request, spec, graph,
mechanisms, BOM, and the maximum level they authorize. Any edit to ANY of those pieces changes the
manifest hash and leaves the signature invalid.

The signature is HMAC-SHA256 (external key, reuses procedencia). It replaces the partial
signatures of `ratificacion_topo` for grades ≥ 2 (it is the only legitimate path to 'conectado'
onwards).
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import procedencia as P  # noqa: E402


def construir_manifiesto(did, pedido, spec, grafo=None, mecanismos=None, bom=None,
                         puntos_operacion=None, nivel_max="conectado", firmante=None, fecha=None,
                         version=0):
    """Builds the manifest (stable dict) that the human signs. Everything enters the hash.

    `version` is a MONOTONIC counter (anti-rollback, Fable P0): a re-inserted old manifest must
    not displace the current one. The human increments version on each signing.
    """
    return {
        "did": did,
        "pedido": pedido,
        "spec": spec,
        "grafo": grafo,
        "mecanismos": mecanismos,
        "bom": bom,
        "puntos_operacion": puntos_operacion,
        "nivel_max": nivel_max,
        "version": version,
        "firmante": firmante,
        "fecha": fecha,
    }


def firmar_manifiesto(manifiesto):
    """Signs the complete manifest with the external human key. Returns signature hex."""
    import hmac as _hmac
    import hashlib
    mensaje = P.normalizar(manifiesto).encode("utf-8")
    return _hmac.new(P._clave_humana(), mensaje, hashlib.sha256).hexdigest()


def verificar_manifiesto(manifiesto, firma):
    """Verifies the signature over the COMPLETE manifest. False on any edit."""
    import hmac as _hmac
    import hashlib
    if not isinstance(firma, str) or len(firma) != 64:
        return False
    mensaje = P.normalizar(manifiesto).encode("utf-8")
    try:
        dado = bytes.fromhex(firma)
        esperado = _hmac.new(P._clave_humana(), mensaje, hashlib.sha256).digest()
        return _hmac.compare_digest(esperado, dado)
    except (ValueError, P.ProcedenciaInvalida):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Manifest TRANSITION (anti-rollback, Fable P0)
# ─────────────────────────────────────────────────────────────────────────────
def _version_vigente(m, did):
    """Persisted current version of a domain's manifest, or 0 if none."""
    f = m.current(f"manifiesto:{did}:version")
    if f is None:
        return 0
    try:
        return int((f.get("value") or {}).get("version", 0))
    except (TypeError, ValueError):
        return 0


def instalar_manifiesto(m, manifiesto, firma):
    """Installs a signed manifest ONLY if its version is strictly greater than the current one
    (anti-rollback). Returns (ok, motivo, version_persistida).

    Fable P0: without this, an OLD, signed, unexpired and more permissive manifest can be
    re-inserted and displace the current one. Here we require monotonicity of the signed version
    (it enters the hash) and persist the current version in governed memory.
    """
    if not verificar_manifiesto(manifiesto, firma):
        return False, "firma de manifiesto inválida", None
    did = manifiesto.get("did")
    version = manifiesto.get("version", 0)
    try:
        version = int(version)
    except (TypeError, ValueError):
        return False, "version no entera", None
    vigente = _version_vigente(m, did)
    if version <= vigente:
        return False, f"version {version} <= vigente {vigente} (rollback rechazado)", vigente
    v = m.write(f"manifiesto:{did}:version", {"version": version, "firma": firma},
                unit="", fuente="usuario", verified=True, meta={"tipo": "manifiesto_version"})
    return True, f"instalado version {version}", v


if __name__ == "__main__":
    os.environ["HCMA_HUMANO_HMAC_KEY"] = "clave-manifiesto"
    m = construir_manifiesto("dom", "pedido", {"formulas": ["x"]}, grafo={"bloques": []},
                            nivel_max="funcional")
    f = firmar_manifiesto(m)
    print("verifica:", verificar_manifiesto(m, f))
    # editing the BOM (which the human signed) invalidates
    m_edit = construir_manifiesto("dom", "pedido", {"formulas": ["x"]}, grafo={"bloques": []},
                                  bom={"p": {"ratings": {"v": 10}}}, nivel_max="funcional")
    print("tras editar BOM, verifica:", verificar_manifiesto(m_edit, f))
