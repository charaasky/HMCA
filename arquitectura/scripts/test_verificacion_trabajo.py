#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de verificacion_trabajo.py — verificador determinista del work product.

Casos:
  A. Astropulse: un artefacto saboteado NO cierra; el correcto SÍ.
  B. KeeperAI:   no se puede 'borrar feature' sin medir antes, y solo si la medición perjudica.
"""
import os
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import deontica  # noqa: E402
import verificacion_trabajo as VT  # noqa: E402


def _mk():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_vt_test_")
    os.close(fd)
    return HCMAMemoriaSQLite(path)


PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")
    return cond


# ── A. Astropulse: work product con checks deterministas ──
m = _mk()
spec = {"formula_hash": "abc123", "pasos_por_step": 1}
m.write("cota:trainer:spec", {"formula_hash": "abc123"}, fuente="tabla", verified=True)
VT.registrar_work_product(m, "trainer_v1", "trainer", spec,
                          ["formula_coincide", "embeddings_cacheados",
                           "condicionamiento_evoluciona", "sin_pasos_extra"],
                          requiere=[("cota:trainer:spec", "conforme")])

sabotaje = {"formula_hash": "xyz999", "embeddings_cache": False,
            "text_conditioning_congelado": True, "pasos_por_step": 5}
est, det = VT.verificar_y_cerrar(m, "trainer_v1", sabotaje)
check("A1. artefacto saboteado NO cierra (bloqueado)", est == "bloqueado", f"{est}: {det}")
check("A2. el motivo nombra los checks fallidos", "checks fallidos" in det and "formula" in det, det)

correcto = {"formula_hash": "abc123", "embeddings_cache": True,
            "text_conditioning_congelado": False, "pasos_por_step": 1}
est2, det2 = VT.verificar_y_cerrar(m, "trainer_v1", correcto)
check("A3. artefacto correcto SÍ cierra (satisfecho)", est2 == "satisfecho", f"{est2}: {det2}")

# ── B. KeeperAI: medir antes de borrar ──
m2 = _mk()
deontica.registrar_entregable(m2, "drop_skin_tone", "drop_feature",
                              "quitar preferencia skin_tone",
                              predicado_ref="medir_antes_de_borrar",
                              requiere=[("fuente:medicion:skin_tone", "conforme")])
pred = VT.invariante_medir_antes_de_borrar(m2, "skin_tone")

est3, det3 = deontica.cerrar_si_predicado(m2, "drop_skin_tone", pred)
check("B1. sin medición -> NO cierra (pendiente)", est3 == "pendiente", f"{est3}: {det3}")

m2.write("fuente:medicion:skin_tone", {"perjudica": False}, fuente="tabla", verified=True)
est4, det4 = deontica.cerrar_si_predicado(m2, "drop_skin_tone", pred)
check("B2. medición 'no perjudica' -> NO cierra (bloqueado)", est4 == "bloqueado", f"{est4}: {det4}")

m2.write("fuente:medicion:skin_tone", {"perjudica": True}, fuente="tabla", verified=True)
est5, det5 = deontica.cerrar_si_predicado(m2, "drop_skin_tone", pred)
check("B3. medición 'perjudica' -> SÍ cierra (satisfecho)", est5 == "satisfecho", f"{est5}: {det5}")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
