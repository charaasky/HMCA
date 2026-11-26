#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_manifiesto.py — test del manifiesto firmado (firma cubre TODO el contenido).

P0 de Fable (fable_revision_integral): la firma debe cubrir spec+grafo+mecanismos+bom, no solo
el grafo. Invariantes:
  [1] firma válida sobre el manifiesto completo -> recorre escalones (hasta nivel_max).
  [2] editar el BOM (que el humano firmó) invalida la firma -> degrada.
  [3] nivel_max limita el grado (si solo autoriza 'conectado', no sube a funcional/realizable).
  [4] firma inválida/ausente -> derivado_bajo_supuesto (con grafo) o formula_suelta.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import manifiesto as MT  # noqa: E402
import grado_general as GG  # noqa: E402

os.environ["HCMA_HUMANO_HMAC_KEY"] = "clave-manifiesto-test"

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


def _grafo():
    return {
        "bloques": [
            {"id": "bat", "tipo": "fuente", "puertos": [{"id": "p", "dominio": "potencia"}, {"id": "n", "dominio": "tierra"}]},
            {"id": "reg", "tipo": "reg", "puertos": [{"id": "in", "dominio": "potencia"}, {"id": "gnd", "dominio": "tierra"}]},
        ],
        "nets": [
            {"id": "v", "conexiones": [{"bloque": "bat", "puerto": "p"}, {"bloque": "reg", "puerto": "in"}]},
            {"id": "g", "conexiones": [{"bloque": "bat", "puerto": "n"}, {"bloque": "reg", "puerto": "gnd"}]},
        ],
        "bindings": [],
    }


_MODELOS = {"reg": {"funcion": "regula", "clase_modelo": "lineal"}}
_PUNTOS = {"reg": {"tension": 9.0}}
_BOM = {"reg": {"part_no": "x", "ratings": {"tension": 20.0}}}


print("[1] manifiesto firmado completo -> recorre hasta realizable")
man = MT.construir_manifiesto("dom", "pedido", {"formulas": ["x"]}, grafo=_grafo(),
                              mecanismos=_MODELOS, bom=_BOM,
                              puntos_operacion=_PUNTOS, nivel_max="realizable")
firma = MT.firmar_manifiesto(man)
r = GG.grado_general_manifiesto(man, firma)
check("1a. realizable", r["grado"] == "realizable", r["grado"])

print("\n[2] editar el BOM tras firmar invalida -> degrada")
man_edit = MT.construir_manifiesto("dom", "pedido", {"formulas": ["x"]}, grafo=_grafo(),
                                   mecanismos=_MODELOS, bom={"reg": {"part_no": "y", "ratings": {"tension": 5.0}}},
                                   puntos_operacion=_PUNTOS, nivel_max="realizable")
r2 = GG.grado_general_manifiesto(man_edit, firma)
check("2a. firma inválida tras editar BOM", not MT.verificar_manifiesto(man_edit, firma))
check("2b. degrada a derivado_bajo_supuesto o formula_suelta",
      r2["grado"] in ("derivado_bajo_supuesto", "formula_suelta"), r2["grado"])

print("\n[3] nivel_max limita el grado")
man_conectado = MT.construir_manifiesto("dom", "pedido", {"formulas": ["x"]}, grafo=_grafo(),
                                        mecanismos=_MODELOS, bom=_BOM,
                                        puntos_operacion=_PUNTOS, nivel_max="conectado")
firma_c = MT.firmar_manifiesto(man_conectado)
r3 = GG.grado_general_manifiesto(man_conectado, firma_c)
check("3a. nivel_max 'conectado' -> conectado (no sube)", r3["grado"] == "conectado", r3["grado"])

print("\n[4] firma inválida -> degrada")
man_sin = MT.construir_manifiesto("dom", "pedido", {"formulas": ["x"]}, grafo=_grafo(), nivel_max="conectado")
r4 = GG.grado_general_manifiesto(man_sin, "0" * 64)
check("4a. derivado_bajo_supuesto (grafo válido)", r4["grado"] == "derivado_bajo_supuesto", r4["grado"])

print("\n[5] ANTI-ROLLBACK: un manifiesto VIEJO (versión menor) no desplaza al vigente")
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
m = HCMAMemoriaSQLite(":memory:")
# instalar versión 2
man_v2 = MT.construir_manifiesto("dom2", "p", {"f": ["x"]}, grafo=_grafo(),
                                  nivel_max="conectado", version=2)
fr_v2 = MT.firmar_manifiesto(man_v2)
ok2, motivo2, _ = MT.instalar_manifiesto(m, man_v2, fr_v2)
check("5a. instalar version 2 => ok", ok2 is True, motivo2)
# intentar re-instalar un manifiesto VIEJO (version 1), firmado y más permisivo
man_viejo = MT.construir_manifiesto("dom2", "p", {"f": ["x"]}, grafo=_grafo(),
                                    mecanismos=_MODELOS, bom=_BOM,
                                    puntos_operacion=_PUNTOS, nivel_max="realizable", version=1)
fr_viejo = MT.firmar_manifiesto(man_viejo)
ok_viejo, motivo_viejo, _ = MT.instalar_manifiesto(m, man_viejo, fr_viejo)
check("5b. re-instalar version 1 (más permisivo) => RECHazado (rollback)",
      ok_viejo is False and "rollback" in motivo_viejo.lower(), motivo_viejo)
# instalar una versión MAYOR sí pasa
man_v3 = MT.construir_manifiesto("dom2", "p", {"f": ["x"]}, grafo=_grafo(),
                                  nivel_max="conectado", version=3)
fr_v3 = MT.firmar_manifiesto(man_v3)
ok3, motivo3, _ = MT.instalar_manifiesto(m, man_v3, fr_v3)
check("5c. instalar version 3 (> vigente) => ok", ok3 is True, motivo3)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
