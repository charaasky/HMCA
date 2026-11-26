#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_autoratificacion.py — test del ciclo de auto-ratificación con reintentos.

Verifica los invariantes de Fable 5.2 (fable_autoratificacion) con el contrato CORREGIDO tras
la revisión de Fable (fable_revision_candidata):

  [1] Clasificación (P0-3 corregido): default contingente; definicional solo si derivable_por
      (no-LLM) + `de` NO vacío + premisas RATIFICADAS EN EL HCMA (no autodeclaradas).
  [2] Oráculo congelado (I0): mismo contenido => mismo oraculo_id; mutarlo cambia el hash.
  [3] Separación de funciones (I2): evidencia con autor_raíz = autor candidata no ratifica.
  [4] Verificador anti-tautológico (P0-2 corregido): EJECUTA fn contra casos del oráculo;
      una candidata que miente (fórmula mala con obtenido autodeclarado) NO pasa.
  [5] Ciclo con dato contingente SIN fuente => BLOQUEADA_SIN_ORACULO.
  [6] Ciclo con oráculo pre-registrado y fn correcta => VERIFICADA_MEC.
  [7] Entregar SIEMPRE con etiqueta epistémica; el borrador no se muestra.
  [8] Ratificación sin veredicto => rechazada (compuerta anti-P0).
  [9] Reintentos agotados sin verificar => AGOTADA.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import autoratificacion as A  # noqa: E402
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


# ── helpers de candidata/oráculo con el contrato CORREGIDO ───────────────────
def _fn_correcta(inputs):
    """fn determinista: P = flujo/eficacia (que con 100 lm / 100 lm/W da 1.0 W)."""
    return {"potencia_W": float(inputs.get("flujo_lm", 0.0)) /
            float(inputs.get("eficacia_lm_por_W", 1.0))}


def _fn_mentirosa(inputs):
    """fn que miente: siempre devuelve 999 aunque la fórmula real dé 1.0."""
    return {"potencia_W": 999.0}


_DOM = {
    "nombre": "luminaria_led",
    "autor": "modelo",
    "fn": _fn_correcta,
    "afirmaciones": {
        "P_igual_phi_eta": {
            "clase": "definicional", "derivable_por": "chequeo_dimensional",
            "de": ["premisa_flujo"],  # NO vacío (ley derivable desde premisa ratificada)
        },
        "eta_lm_W": {"clase": "contingente", "fuente_requerida": True,
                     "fuente_id": "led_eficacia_lm_w"},
    },
}

# casos del oráculo (verdad gobernada, NO autodeclarada por la candidata)
_CASOS = [
    {"id": "c1", "inputs": {"flujo_lm": 100.0, "eficacia_lm_por_W": 100.0},
     "esperado": {"potencia_W": 1.0}, "tolerancia": 1e-6},
]


print("=" * 74)
print("[1] clasificación (P0-3 corregido): premisas en HCMA, 'de' no vacío")
print("=" * 74)
m1 = mem()
# premisa ratificada en el HCMA
m1.write("premisa_flujo", 100.0, unit="lm", fuente="usuario", verified=True)
c = A.clasificar_afirmaciones(_DOM, m=m1)
check("1a. definicional con premisa ratificada en HCMA => definicional",
      c["P_igual_phi_eta"] == A.CLASE_DEFINICIONAL, c.get("P_igual_phi_eta"))

# premisa NO ratificada => contingente (no autodeclarable)
m2 = mem()  # vacía: no hay premisa_flujo
c2 = A.clasificar_afirmaciones(_DOM, m=m2)
check("1b. premisa no ratificada en HCMA => contingente (anti-autoprocla)",
      c2["P_igual_phi_eta"] == A.CLASE_DATO_CONTINGENTE, c2.get("P_igual_phi_eta"))

# 'de' vacío => contingente (una ley sin premisa no es derivable de nada)
auto = {"nombre": "x", "autor": "modelo",
        "afirmaciones": {"f": {"clase": "definicional", "derivable_por": "x", "de": []}}}
m3 = mem()
check("1c. de=[] => contingente", A.clasificar_afirmaciones(auto, m=m3)["f"] == A.CLASE_DATO_CONTINGENTE)

print("\n" + "=" * 74)
print("[2] oráculo congelado (I0): hash determinista, mutación = otro hash")
print("=" * 74)
o1 = A.congelar_oraculo({"nombre": "luminaria_led"}, {"a": {"valor": 1.0}}, casos=_CASOS)
o2 = A.congelar_oraculo({"nombre": "luminaria_led"}, {"a": {"valor": 1.0}}, casos=_CASOS)
o3 = A.congelar_oraculo({"nombre": "luminaria_led"}, {"a": {"valor": 10.0}}, casos=_CASOS)
check("2a. mismo contenido => mismo oraculo_id", o1["oraculo_id"] == o2["oraculo_id"])
check("2b. mutar el oráculo => distinto oraculo_id", o1["oraculo_id"] != o3["oraculo_id"])

print("\n" + "=" * 74)
print("[4] verificador anti-tautológico (P0-2 corregido): ejecuta fn contra oráculo")
print("=" * 74)
oraculo = A.congelar_oraculo({"nombre": "luminaria_led"}, {}, casos=_CASOS)
# fn correcta => pasa
v_ok = A.verificar(_DOM, oraculo)
check("4a. fn correcta contra oráculo => verificada_mec", v_ok["veredicto"] == "verificada_mec",
      v_ok["veredicto"])
# fn mentirosa => falla (no se cree en lo que declara la candidata)
dom_mentiroso = dict(_DOM, fn=_fn_mentirosa)
v_mentiroso = A.verificar(dom_mentiroso, oraculo)
check("4b. fn mentirosa => fallo (no tautología)", v_mentiroso["veredicto"] == "fallo",
      v_mentiroso["veredicto"])
# oráculo sin casos => fallo (no hay verdad con qué comparar)
oraculo_vacio = A.congelar_oraculo({"nombre": "luminaria_led"}, {}, casos=[])
v_vacio = A.verificar(_DOM, oraculo_vacio)
check("4c. oráculo sin casos => fallo (no hay tautología)", v_vacio["veredicto"] == "fallo")

print("\n" + "=" * 74)
print("[3] separación de funciones (I2): evidencia autor=modelo no ratifica")
print("=" * 74)
cand = {"nombre": "x", "autor": "modelo"}
v3 = A.verificar(cand, oraculo, runner=lambda c, o: (True, {"autor_raiz": "modelo"}))
check("3a. evidencia autor_raíz=modelo => bloqueada", v3["veredicto"] == "bloqueado_sin_oraculo",
      v3["veredicto"])

# [3b] FALSACIÓN del bypass I2: la evidencia que miente con `prueba_verificador_no_llm=True`
#      Y `autor_raiz=modelo` al mismo tiempo debe bloquearse. Antes este caso pasaba el gate
#      (el flag auto-declarado salvaba la escalada); ahora se bloquea SIEMPRE, sin excepción.
v3b = A.verificar(cand, oraculo,
                  runner=lambda c, o: (True, {"autor_raiz": "modelo",
                                             "prueba_verificador_no_llm": True}))
check("3b. evidencia autor_raíz=modelo + flag no_llm => IGUAL bloqueada (sin bypass)",
      v3b["veredicto"] == "bloqueado_sin_oraculo", v3b["veredicto"])

print("\n" + "=" * 74)
print("[5] ciclo: dato contingente SIN fuente => BLOQUEADA_SIN_ORACULO")
print("=" * 74)
m5 = mem()
r = A.ciclo_reintentos(m5, _DOM, {}, n_max=3, casos=_CASOS)
check("5a. estado bloqueada_sin_oraculo", r["estado"] == A.BLOQUEADA_SIN_ORACULO, r["estado"])
check("5b. lista qué falta", "led_eficacia_lm_w" in (r["faltan_fuente"] or []), r["faltan_fuente"])

print("\n" + "=" * 74)
print("[6] ciclo: oráculo pre-registrado + fn correcta => VERIFICADA_MEC")
print("=" * 74)
m6 = mem()
# registramos la premisa que hace deducible a P=Φ/η, y el dato contingente (η)
m6.write("premisa_flujo", 100.0, unit="lm", fuente="usuario", verified=True)
m6.write("fuente:led_eficacia_lm_w", {"valor": 100.0}, unit="lm/W", fuente="tabla", verified=True)
artefactos = {"led_eficacia_lm_w": A.resolver_oraculo(m6, {"fuente_id": "led_eficacia_lm_w"})}
r6 = A.ciclo_reintentos(m6, _DOM, artefactos, n_max=3, casos=_CASOS)
check("6a. estado verificada_mec", r6["estado"] == A.VERIFICADA_MEC, r6["estado"])
check("6b. etiqueta verificado", r6["etiqueta_epistemica"] == "verificado")

print("\n" + "=" * 74)
print("[7] entregar: etiqueta epistémica obligatoria + borrador oculto")
print("=" * 74)
e = A.entregar(r6)
check("7a. sin campos internos", all(not k.startswith("_") for k in e["entregable"]))
check("7b. etiqueta presente", bool(e["etiqueta_epistemica"]))

print("\n" + "=" * 74)
print("[8] ratificación sin veredicto => rechazada (compuerta anti-P0)")
print("=" * 74)
m8 = mem()
r_rechazo = A.ratificar_mecanicamente(m8, {"nombre": "x"}, {"veredicto": "fallo"}, "oid")
check("8a. sin veredicto verificada_mec => rechaza (None)", r_rechazo is None)
# con veredicto válido sí escribe
r_ok = A.ratificar_mecanicamente(m8, {"nombre": "luminaria_led"},
                                 {"veredicto": "verificada_mec", "evidencia": {"n_casos": 1}},
                                 "oid")
check("8b. con veredicto válido escribe registro", r_ok is not None)

print("\n" + "=" * 74)
print("[9] reintentos agotados sin verificar => AGOTADA")
print("=" * 74)
m9 = mem()
# premisa ratificada + dato contingente resuelto, para que el ciclo llegue al verificador
m9.write("premisa_flujo", 100.0, unit="lm", fuente="usuario", verified=True)
m9.write("fuente:led_eficacia_lm_w", {"valor": 100.0}, unit="lm/W", fuente="tabla", verified=True)
dom_falla = dict(_DOM, fn=_fn_mentirosa)
artefactos9 = {"led_eficacia_lm_w": {"fuente_id": "led_eficacia_lm_w", "valor": 100.0, "hash": "h"}}
r9 = A.ciclo_reintentos(m9, dom_falla, artefactos9, n_max=2,
                        consentimiento=lambda motivo: True, casos=_CASOS)
check("9a. fn mentirosa agota reintentos => AGOTADA", r9["estado"] == A.AGOTADA, r9["estado"])

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
