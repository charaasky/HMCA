#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_objetivo.py — test de falsación de OBJETIVO/SUB-OBJETIVO (P0-2/P0-3 de Fable).

QUÉ LO FALSARÍA:
  - Si editar el CRITERIO de aceptación no invalidara la firma (oráculo autoproclamado).
  - Si el estado se pudiera marcar 'resuelto' sin check determinista.
  - Si una necesidad sin autorización no bloqueara.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

os.environ.setdefault("HCMA_HUMANO_HMAC_KEY", "clave-test-objetivo")

import objetivo as OBJ  # noqa: E402
import gobernanza_accion as GA  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402

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


def mem():
    return HCMAMemoriaSQLite(":memory:")


print("=" * 74)
print("[1] criterio CONGELADO: se firma antes; editar lo invalida")
print("=" * 74)
obj = OBJ.construir_objetivo(
    "eva", "simular espumado", "producir sim verificada", "densidad ±10%",
    subobjetivos=["modelo", "datos"])
f = OBJ.firmar_objetivo(obj)
check("1a. firma válida sobre criterio original", OBJ.verificar_objetivo(obj, f))
obj_edit = OBJ.construir_objetivo(
    "eva", "simular espumado", "producir sim verificada", "densidad ±5%",
    subobjetivos=["modelo", "datos"])
check("1b. editar criterio invalida la firma", not OBJ.verificar_objetivo(obj_edit, f))

print("\n[2] sin firma => bloqueado (criterio no congelado)")
m = mem()
e = OBJ.estado_objetivo(m, obj, "firma_invalida", lambda sid: {"paso": False})
check("2a. sin firma válida => bloqueado", e["estado"] == OBJ.BLOQUEADO, e)

print("\n[3] estado DERIVADO: 'resuelto' SOLO con check determinista + EVIDENCIA atada")
# P0 fail-open corregido (Fable): paso=True SIN evidencia => NO resuelto (en_curso)
e_no_evid = OBJ.estado_objetivo(m, obj, f, lambda sid: {"paso": True})
check("3a. paso=True SIN evidencia_hash => NO resuelto (fail-open cerrado)",
      e_no_evid["estado"] == OBJ.EN_CURSO, e_no_evid)
# con evidencia atada => resuelto
for sid in ("modelo", "datos"):
    OBJ.registrar_evidencia(m, obj, f, sid, "evid1")
e_res = OBJ.estado_objetivo(m, obj, f, lambda sid: {"paso": True, "evidencia_hash": "evid1"})
check("3b. con evidencia atada => resuelto", e_res["estado"] == OBJ.RESUELTO, e_res)
e_cur = OBJ.estado_objetivo(m, obj, f, lambda sid: {"paso": False})
check("3c. check False => en_curso", e_cur["estado"] == OBJ.EN_CURSO, e_cur)

print("\n[4] necesidad sin autorizar => bloqueado (resultado de PRIMERA CLASE)")
m2 = mem()
GA.declarar_necesidad(m2, "modelo", "openfoam", "simular", GA.RIESGO_EXTERNO)
e_bloq = OBJ.estado_objetivo(m2, obj, f, lambda sid: {"paso": True})
check("4a. necesidad abierta => bloqueado", e_bloq["estado"] == OBJ.BLOQUEADO, e_bloq)
check("4b. lista qué falta", len(e_bloq["faltan_autorizacion"]) > 0, e_bloq["faltan_autorizacion"])

print("\n[5] P1: evidencia ATADA al objetivo firmado (anti reuso de evidencia de otra corrida)")
m3 = mem()
# el verificador devuelve un evidencia_hash: el cierre SOLO vale si está atada a este objetivo
check("5a. verificar() devuelve evidencia_hash sin registrar => NO cierra (en_curso)",
      OBJ.estado_objetivo(m3, obj, f, lambda sid: {"paso": True, "evidencia_hash": "abc"})["estado"] == OBJ.EN_CURSO)
# registrar la evidencia atada -> ahora sí cierra
OBJ.registrar_evidencia(m3, obj, f, "modelo", "abc", herramienta="ngspice")
OBJ.registrar_evidencia(m3, obj, f, "datos", "abc", herramienta="ngspice")
check("5b. evidencia ATADA => cierra",
      OBJ.estado_objetivo(m3, obj, f, lambda sid: {"paso": True, "evidencia_hash": "abc"})["estado"] == OBJ.RESUELTO)

print("\n[5-c] evidencia de OTRO objetivo NO sirve para este (anti reuso)")
m4 = mem()
# objetivo con criterio DISTINTO (mismo id, distinto criterio) -> distinto objetivo_hash
obj2 = OBJ.construir_objetivo("eva", "simular espumado", "producir sim verificada", "densidad ±20%",
                               subobjetivos=["modelo", "datos"])
f2 = OBJ.firmar_objetivo(obj2)
# registramos evidencia bajo obj2
OBJ.registrar_evidencia(m4, obj2, f2, "modelo", "abc", herramienta="ngspice")
# pero evaluamos con obj (el original): la evidencia NO está atada al hash de obj
check("5c. evidencia registrada bajo OTRO criterio no cierra ESTE objetivo",
      OBJ.estado_objetivo(m4, obj, f, lambda sid: {"paso": True, "evidencia_hash": "abc"})["estado"] == OBJ.EN_CURSO)

print("\n[5-d] registrar_evidencia con firma INVÁLIDA => None (no registra)")
r = OBJ.registrar_evidencia(m4, obj, "firma_mala", "modelo", "abc")
check("5d. firma inválida => registrar_evidencia devuelve None", r is None, r)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
