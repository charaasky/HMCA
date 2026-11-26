#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_ablacion.py — matriz de ablación (Fable: 'apagá cada mecanismo y mostrás que su ataque pasa').

Invariante de la ablación: apagar UN mecanismo de defensa debe hacer que EXACTAMENTE su ataque
pase (exitoso=True) y que los demás se sigan bloqueando. Es la prueba de que la defensa es
CAUSAL, no correlacional: si un ataque pasa igual con el mecanismo prendido y apagado, o si se
apaga un mecanismo y NINGÚN ataque cambia, la defensa no es la que frena.

Mapeo mecanismo -> ataque (mismos nombres que suite_adversarial):
  nonce+CAS                     -> replay
  separacion_dominio            -> dominio_cruzado
  exp_absoluta_firmada          -> expirado
  hash_accion+artifacto_en_payload -> artefacto_cambiado
  validacion_tipos             -> riesgo_nan
  consumir_autorizacion_atomica -> consumo_concurrente
  sandbox_allowlist            -> evasion_netlist
  evidencia_obligatoria        -> cerrar_sin_evidencia
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# PRUEBA autorizada: habilita el apagado de defensas para la matriz de ablación.
os.environ["HCMA_PRUEBA_OVERRIDE"] = "1"

import ablacion as AB  # noqa: E402
import suite_adversarial as SA  # noqa: E402

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


# Mapeo exacto: mecanismo defensivo -> nombre del ataque que debe desbloquearse
# NOTA: 'replay' y 'consumo_concurrente' comparten el mecanismo consumir_autorizacion_atomica
# (el CAS del flag un-solo-uso). Por eso el mapeo es 8 mecanismos -> 8 ataques, pero dos de
# esos ataques (replay, consumo_concurrente) son dos caras del MISMO mecanismo. La ablación
# apaga el CAS y esperamos que AMBOS pasen; eso es correcto y honesto (un solo mecanismo).
MECANISMO_A_ATAQUE = {
    AB.SEPARACION_DOMINIO: "dominio_cruzado",
    AB.EXPIRACION_ABSOLUTA: "expirado",
    AB.HASH_ARTEFACTO: "artefacto_cambiado",
    AB.VALIDACION_TIPOS: "riesgo_nan",
    AB.CAS_ATOMICO: "consumo_concurrente",
    AB.SANDBOX_ALLOWLIST: "evasion_netlist",
    AB.EVIDENCIA_OBLIGATORIA: "cerrar_sin_evidencia",
}


print("=" * 74)
print("[1] baseline: con TODA defensa activa, 0 ataques exitosos")
print("=" * 74)
AB.reset()
base = SA.correr_suite()
check("1a. baseline 0 exitosos", all(not r["exitoso"] for r in base.values()),
      {n: r["exitoso"] for n, r in base.items() if r["exitoso"]})

print("\n[2] matriz de ablación: apagar UN mecanismo => SU ataque pasa (y solo los compartidos)")
# Ataques que comparten el MISMO mecanismo (CAS del consumo): replay y consumo_concurrente.
COMPARTEN = {
    AB.CAS_ATOMICO: {"replay", "consumo_concurrente"},
}

for mecanismo, ataque in sorted(MECANISMO_A_ATAQUE.items()):
    AB.reset()
    AB.apagar(mecanismo)
    suite = SA.correr_suite()
    # el ataque correspondiente DEBE pasar
    ataque_objetivo = suite[ataque]["exitoso"]
    # los que pueden pasar además del objetivo: los que comparten mecanismo
    permitidos = COMPARTEN.get(mecanismo, {ataque})
    otros_pasan = [n for n, r in suite.items() if r["exitoso"] and n not in permitidos]
    ok = ataque_objetivo is True and len(otros_pasan) == 0
    check(f"2. {mecanismo:32s} -> {ataque:22s} pasa (y solo compartidos)",
          ok,
          f"ataque_objetivo={ataque_objetivo}, otros_pasan={otros_pasan}")

print("\n[3] re-encender: tras re-encender TODAS las defensas, vuelve a 0 exitosos")
AB.reset()
final = SA.correr_suite()
check("3a. tras reset vuelve a 0 exitosos", all(not r["exitoso"] for r in final.values()),
      {n: r["exitoso"] for n, r in final.items() if r["exitoso"]})

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
