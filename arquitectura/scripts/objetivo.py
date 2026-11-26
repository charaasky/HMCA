#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""objetivo.py — governed OBJECTIVE and SUBOBJECTIVE (Fable P0-2/P0-3).

Closes the 'objective-driven' piece WITHOUT a planner: the OBJECTIVE (interpretation + acceptance
criterion) is signed by the HUMAN with the SAME HMAC key as the manifest. The SUBOBJECTIVES are
MODEL proposals (grade <=1), with `depende_de` and a DETERMINISTIC closure check.

Fable rules applied:
  - FROZEN CRITERION before executing: if the model defines success a posteriori, it is a
    self-proclaimed oracle. Here the criterion is signed BEFORE.
  - DERIVED STATE, not stored: 'bloqueado' = there is an open need without authorization;
    'resuelto' = there is an execution whose output passes the check. The LLM NEVER writes
    'resuelto'.
  - 'OBJECTIVE ACHIEVED' != 'TRUE RESULT': the output remains a candidate <=1.
  - DECLARED BLOCK is a FIRST-CLASS result (success, not failure).

Structure:
  objetivo:<id>:interpretacion   model: proposes {interpretacion, criterio, subobjetivos}
  objetivo:<id>:firma            human: HMAC over the interpretation (reuses procedencia._clave_humana)
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


# ─────────────────────────────────────────────────────────────────────────────
# objective construction and signing (human HMAC, same as manifest)
# ─────────────────────────────────────────────────────────────────────────────
def construir_objetivo(id_obj, pedido, interpretacion, criterio, subobjetivos=None,
                       presupuesto=None, firmante=None, fecha=None):
    """Builds the stable objective dict (what the human signs). Everything enters the hash."""
    return {
        "id": id_obj,
        "pedido_verbatim": pedido,
        "interpretacion": interpretacion,   # "simular espumado", not "describir fórmula"
        "criterio": criterio,               # FROZEN acceptance criterion
        "subobjetivos": subobjetivos or [],  # proposed by the model, grade <=1
        "presupuesto": presupuesto,
        "firmante": firmante,
        "fecha": fecha,
    }


def construir_objetivo_v2(id_obj, pedido, interpretacion, criterio, subobjetivos, merge_id,
                          presupuesto=None, firmante=None, fecha=None):
    """Builds a schema v2 OBJECTIVE: subobjectives as SIGNED TASK SPECS.

    Each subobjective is {"id", "descripcion", "depends_on": [ids]} — the DAG enters the signature
    (frozen by the human). `merge_id` is the SINK node. `criterio` is the acceptance criterion of
    the FINAL RESULT (the merge output).

    Backward compatibility (Fable option B ruling): v1 (flat subobjectives) does NOT carry
    `schema`; the ABSENCE of `schema` is v1 and its previous signatures keep verifying unchanged.
    v2 carries explicit `schema: 2`. `firmar_objetivo`/`verificar_objetivo` are schema-agnostic
    (they hash the complete dict)."""
    return {
        "schema": 2,
        "id": id_obj,
        "pedido_verbatim": pedido,
        "interpretacion": interpretacion,
        "criterio": criterio,
        "subobjetivos": subobjetivos,       # [{id, descripcion, depends_on}], SIGNED
        "merge_id": merge_id,                # sink, SIGNED
        "presupuesto": presupuesto,
        "firmante": firmante,
        "fecha": fecha,
    }


def firmar_objetivo(objetivo):
    """Signs the objective with the HUMAN KEY (same as manifest). Returns signature hex."""
    import hmac as _hmac
    import hashlib
    mensaje = P.normalizar(objetivo).encode("utf-8")
    return _hmac.new(P._clave_humana(), mensaje, hashlib.sha256).hexdigest()


def verificar_objetivo(objetivo, firma):
    """Verifies the objective signature. False on any criterion/plan edit."""
    import hmac as _hmac
    import hashlib
    if not isinstance(firma, str) or len(firma) != 64:
        return False
    mensaje = P.normalizar(objetivo).encode("utf-8")
    try:
        dado = bytes.fromhex(firma)
        esperado = _hmac.new(P._clave_humana(), mensaje, hashlib.sha256).digest()
        return _hmac.compare_digest(esperado, dado)
    except (ValueError, P.ProcedenciaInvalida):
        return False


def _hash_objetivo(objetivo):
    """Canonical hash of the objective (the one evidence must reference)."""
    import hashlib
    return hashlib.sha256(P.normalizar(objetivo).encode("utf-8")).hexdigest()


def registrar_evidencia(m, objetivo, firma, subobjetivo_id, evidencia_hash, herramienta=""):
    """Binds EVIDENCE (result of an execution, e.g. a `sim:*` extract_hash) to a SUBOBJECTIVE of
    a SIGNED OBJECTIVE.

    Fable P1 ('bind check/evidence to the signed objective'): today the signature authorizes an
    execution but NOTHING binds its result to it. Here we link evidence -> (objective, subobjective)
    so an evidence from ANOTHER run or ANOTHER objective cannot be reused.

    The recorded value includes `objetivo_hash` (canonical hash of the signed objective), so if
    the criterion/plan changes, previous evidence stops being valid for the new objective.
    Returns (key, version) or None if the signature is invalid.
    """
    if not verificar_objetivo(objetivo, firma):
        return None
    oid = objetivo.get("id")
    obj_hash = _hash_objetivo(objetivo)
    clave = f"evidencia:{oid}:{subobjetivo_id}"
    import time
    v = m.write(clave, {
        "objetivo_hash": obj_hash,
        "subobjetivo_id": subobjetivo_id,
        "evidencia_hash": evidencia_hash,
        "herramienta": herramienta,
        "ts": time.time(),
    }, unit="", fuente="calculadora", verified=False, meta={"tipo": "evidencia_objetivo"})
    return clave, v


def evidencia_atada(m, objetivo, firma, subobjetivo_id, evidencia_hash):
    """Is there recorded evidence that BINDS `evidencia_hash` to this subobjective of THIS signed
    objective? Returns True only if it matches (1) the subobjective, (2) the evidencia_hash, and
    (3) the objetivo_hash (canonical hash of the CURRENT signed objective). Pure read.
    """
    if not verificar_objetivo(objetivo, firma):
        return False
    oid = objetivo.get("id")
    obj_hash = _hash_objetivo(objetivo)
    clave = f"evidencia:{oid}:{subobjetivo_id}"
    # evidence is an internal ACCOUNTING record (grade <=1); read by last version WITHOUT
    # authority filter (current_raw), like needs. The objective signs it.
    f = m.current_raw(clave)
    if f is None:
        return False
    val = f.get("value") or {}
    return (val.get("objetivo_hash") == obj_hash and
            val.get("evidencia_hash") == evidencia_hash)


# ─────────────────────────────────────────────────────────────────────────────
# DERIVED STATE (not stored; computed via deterministic check)
# ─────────────────────────────────────────────────────────────────────────────
BLOQUEADO = "bloqueado"
RESUELTO = "resuelto"
EN_CURSO = "en_curso"
ABIERTO = "abierto"


def estado_objetivo(m, objetivo, firma, verificador_outputs):
    """Derives the objective state. The LLM does NOT write it.

    Args:
      m: governed memory.
      objetivo: objective dict (with subobjectives and criterion).
      firma: human HMAC signature (must be valid and frozen).
      verificador_outputs: callable(subobjetivo_id) -> {paso: bool, evidencia_hash: str}.
          DETERMINISTIC closure check + the EVIDENCE backing it. `evidencia_hash` is REQUIRED to
          close (P0 fail-open corrected by Fable): without bound evidence, the subobjective is NOT
          resolved (it stays en_curso). Evidence is produced by the RUNTIME (e.g. a `sim:*`
          extract_hash), not the agent.

    Returns {estado, faltan_autorizacion, subobjetivos_estado}.
    """
    if not verificar_objetivo(objetivo, firma):
        return {"estado": BLOQUEADO, "motivo": "objetivo sin firma humana válida (criterio no congelado)",
                "faltan_autorizacion": [], "subobjetivos_estado": {}}

    sub_estado = {}
    faltan = []
    todos_resueltos = True

    for so in objetivo.get("subobjetivos", []):
        so_id = so.get("id") if isinstance(so, dict) else so
        # 1. open needs without authorization -> blocked
        nes = _necesidades_abiertas_para(m, so_id)
        if nes:
            faltan.extend(nes)
            sub_estado[so_id] = BLOQUEADO
            todos_resueltos = False
            continue
        # 2. deterministic closure check + mandatory EVIDENCE (P0 fail-open corrected)
        import ablacion as AB
        res = verificador_outputs(so_id) if callable(verificador_outputs) else {"paso": False}
        if not isinstance(res, dict):
            res = {}
        paso = bool(res.get("paso", False))
        evid = res.get("evidencia_hash")
        # Anti fail-open: if there is no evidencia_hash, the subobjective does NOT close (even if
        # paso=True). ABLATION: if EVIDENCIA_OBLIGATORIA is off, closing without evidence is
        # allowed.
        if AB.activo(AB.EVIDENCIA_OBLIGATORIA):
            if not paso or not evid:
                sub_estado[so_id] = EN_CURSO
                todos_resueltos = False
                continue
            # Evidence ONLY counts if BOUND to this subobjective of this signed objective.
            if not evidencia_atada(m, objetivo, firma, so_id, evid):
                sub_estado[so_id] = EN_CURSO
                todos_resueltos = False
                continue
        else:
            # without defense: with paso=True (even without evidence) it closes
            if not paso:
                sub_estado[so_id] = EN_CURSO
                todos_resueltos = False
                continue
        sub_estado[so_id] = RESUELTO
        # todos_resueltos stays True only if ALL subobjectives passed

    if faltan:
        return {"estado": BLOQUEADO,
                "motivo": "hay subobjetivos bloqueados por necesidades sin autorizar",
                "faltan_autorizacion": faltan, "subobjetivos_estado": sub_estado}
    if todos_resueltos:
        return {"estado": RESUELTO, "motivo": "todos los subobjetivos cierran por check determinista",
                "faltan_autorizacion": [], "subobjetivos_estado": sub_estado}
    return {"estado": EN_CURSO, "motivo": "al menos un subobjetivo aún en curso",
            "faltan_autorizacion": [], "subobjetivos_estado": sub_estado}


def _necesidades_abiertas_para(m, so_id):
    """Returns the open need keys bound to a subobjective.

    The need is unverified (model proposal), so we use `keys_crudas` (internal audit) to LIST them
    and `current_raw` (LAST version without authority filter) to read them. The objective→need link
    lives in the VALUE (`id_objetivo`), NOT in the key: since P0#3 milestone 3a the key is
    `necesidad:<uuid>` minted by the monitor, not `necesidad:<id_objetivo>:...`.
    """
    out = []
    for k in m.keys_crudas("necesidad:"):
        f = m.current_raw(k)
        if f is None:
            continue
        val = f.get("value") or {}
        if val.get("id_objetivo") == so_id and val.get("estado") == "abierta":
            out.append(k)
    return out


if __name__ == "__main__":
    from memoria_sqlite import HCMAMemoriaSQLite
    os.environ["HCMA_HUMANO_HMAC_KEY"] = "clave-objetivo-demo"
    obj = construir_objetivo(
        "eva", "simular el espumado de goma EVA",
        "producir una simulación verificada del espumado", "densidad final dentro de ±10%",
        subobjetivos=["modelo_espumado", "datos_reologia", "mesh_molde"])
    f = firmar_objetivo(obj)
    print("objetivo firmado:", verificar_objetivo(obj, f))

    m = HCMAMemoriaSQLite(":memory:")
    # no need -> subobjectives resolved if the check passes
    print("estado (todo cierra):",
          estado_objetivo(m, obj, f, lambda sid: {"paso": True})["estado"])
    # edit the criterion -> invalid signature (blocked)
    obj_edit = construir_objetivo("eva", "simular el espumado de goma EVA",
                                  "producir una simulación verificada del espumado",
                                  "densidad final dentro de ±5%",  # criterion changed
                                  subobjetivos=["modelo_espumado"])
    print("estado (criterio editado, sin re-firmar):",
          estado_objetivo(m, obj_edit, f, lambda sid: {"paso": True})["estado"])
