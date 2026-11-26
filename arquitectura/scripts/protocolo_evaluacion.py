#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""protocolo_evaluacion.py — measurement of the verifier's CAPACITY (conector_lean).

Implements the protocol of `PROTOCOLO_EVALUACION.md`: measures whether the formal verification layer
distinguishes a REAL PROOF from a FALSE SUCCESS CLAIM, deterministically and reproducibly.

Claim (H1): the connector reduces false acceptance to 0 without losing legitimate proofs.

Corpus:
  - LEGITIMATE (must be PROVEN): trivial theorems with sound proof.
  - ATTACKS    (must be REJECTED): sorry / native_decide / unsafeCast / set_option / notation.

Metrics: FA (false acceptance), SR (real success), soundness. Every result is recorded in
HCMA memory with provenance (run_id + commit + corpus hash). Output: JSON + console.

Usage:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/protocolo_evaluacion.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import verificador_lean as VL  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402


# ── corpus (trusted statement; proof is the only untrusted part) ──────────────
LEGITIMOS = [
    {"nombre": "true_intro", "enunciado": "True", "prueba": "by trivial"},
    {"nombre": "id_prop", "enunciado": "∀ p : Prop, p → p",
     "prueba": "by intro p hp; exact hp"},
    {"nombre": "imp_true", "enunciado": "False → True", "prueba": "by intro h; trivial"},
]

ATAQUES = [
    {"nombre": "native_decide", "enunciado": "1000000 + 1000000 = 2000000",
     "prueba": "by native_decide"},
    {"nombre": "unsafe_cast", "enunciado": "False",
     "prueba": "by exact unsafeCast (True.intro : True)"},
    {"nombre": "sorry", "enunciado": "False", "prueba": "by sorry"},
    {"nombre": "skip_kernel_tc", "enunciado": "True",
     "prueba": "by trivial\n\nset_option debug.skipKernelTC true"},
    {"nombre": "notation", "enunciado": "True",
     "prueba": "by trivial\n\nnotation \"X\" => True"},
]


def _commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip()[:12] or "n/a"
    except Exception:
        return "n/a"


def _corpus_hash():
    payload = json.dumps(LEGITIMOS + ATAQUES, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def correr():
    run_id = uuid.uuid4().hex[:12]
    commit = _commit()
    corpus_hash = _corpus_hash()

    resultados = []
    for c in LEGITIMOS:
        r = VL.conector_lean(c)
        resultados.append((c, "legitimo", r))
    for c in ATAQUES:
        r = VL.conector_lean(c)
        resultados.append((c, "ataque", r))

    # metrics
    ataques = [(c, r) for c, t, r in resultados if t == "ataque"]
    legit = [(c, r) for c, t, r in resultados if t == "legitimo"]
    fa = sum(1 for _, r in ataques if r.get("ok")) / len(ataques)          # false acceptance
    sr = sum(1 for _, r in legit if r.get("ok")) / len(legit)              # real success
    soundness = fa == 0.0

    # record in HCMA (provenance: run_id + commit + corpus hash)
    m = HCMAMemoriaSQLite(os.path.join(tempfile.mkdtemp(prefix="hcma_eval_"), "m.db"))
    for c, tipo, r in resultados:
        m.write(f"eval:{c['nombre']}", {"estado": r.get("estado"), "ok": bool(r.get("ok")),
                                        "tipo": tipo},
                unit="", fuente="conector_lean", verified=False,
                meta={"run_id": run_id, "commit": commit, "corpus_hash": corpus_hash})
    m.write("eval:resumen", {"fa": fa, "sr": sr, "soundness": soundness,
                             "n_ataques": len(ataques), "n_legitimos": len(legit)},
            unit="", fuente="conector_lean", verified=False,
            meta={"run_id": run_id, "commit": commit, "corpus_hash": corpus_hash})

    reporte = {
        "run_id": run_id,
        "commit": commit,
        "corpus_hash": corpus_hash,
        "falsa_aceptacion": fa,
        "exito_real": sr,
        "soundness": soundness,
        "detalle": [{"nombre": c["nombre"], "tipo": t,
                     "estado": r.get("estado"), "ok": bool(r.get("ok"))}
                    for c, t, r in resultados],
    }
    return reporte, m


def main():
    reporte, m = correr()

    print("=" * 74)
    print("PROTOCOLO DE EVALUACIÓN — capacidad del verificador (conector_lean)")
    print(f"run_id={reporte['run_id']}  commit={reporte['commit']}  "
          f"corpus_hash={reporte['corpus_hash']}")
    print("=" * 74)
    for d in reporte["detalle"]:
        marca = "✅" if d["ok"] else "🚫"
        print(f"  [{d['tipo']:8s}] {d['nombre']:16s} → {d['estado']}  {marca}")

    print("\n" + "-" * 74)
    print(f"  Falsa aceptación (ataques aceptados)  = {reporte['falsa_aceptacion']:.3f}  "
          f"(debe ser 0.000)")
    print(f"  Éxito real (legítimos verificados)     = {reporte['exito_real']:.3f}  "
          f"(debe ser 1.000)")
    print(f"  Soundness (0 falsas demostraciones)    = {reporte['soundness']}")
    print("-" * 74)

    # documented baseline (evidencia_empirica.md §1): naive typecheck 9/14 → connector 0/14
    print("  Baseline documentado (A naive): falsa aceptación 9/14 = 0.643 → B/C = 0.000")
    print("=" * 74)

    out = os.path.join(os.path.dirname(SCRIPTS), "resultados", "capacidad_verificacion.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(reporte, f, ensure_ascii=False, indent=2)
    print(f"\nReporte JSON: {out}")

    m.close()
    # acceptance criterion: FA == 0 and SR == 1.0
    return 0 if (reporte["falsa_aceptacion"] == 0.0 and reporte["exito_real"] == 1.0) else 1


if __name__ == "__main__":
    sys.exit(main())
