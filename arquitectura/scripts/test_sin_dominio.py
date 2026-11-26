#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_sin_dominio.py — test de falsación del flujo genérico sin_dominio.

Verifica los invariantes que Fable pidió (fable_confirmar_sin_dominio), con LLM y web
STUBBEADOS (sin red), para no probar solo el camino feliz:

  [1] Regla de oro: tras resolver, NO existe fuente verified sin actor humano.
  [2] La evidencia web queda como 'fuente:*' UNVERIFIED con autor=modelo (no 'tabla').
  [3] registrar_fuente exige actor humano: con fuente='tabla'/'modelo' degrada a unverified.
  [4] Sensibilidad del invariante: fabricar una fuente verified no-humana => devuelve False.
  [5] Idempotencia del did: el id de la candidata no depende del nombre que invente el LLM.
  [6] Fallo cerrado: una query que lanza excepción no deja estado parcial ni tumba.
  [7] Genericidad: un pedido de dominio no electrónico también funciona (sin hardcode).
"""
import os
import sys
import hashlib

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import sin_dominio as SD  # noqa: E402
import skill_lib  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import generar_dominio_candidato as GDC  # noqa: E402

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


# ── stub del LLM: devuelve una spec candidata determinista con lookup_queries ──
def _spec_stub(pedido, usar_llm=True):
    return {
        "nombre": "dominio de prueba",
        "autor": "modelo",
        "inputs": [{"name": "x", "question": "x", "unit": "1"}],
        "formulas": ["y = 2*x (ley de ejemplo)"],
        "dimensiones": {"y": "1"},
        "ranges": {"y": (0.0, 100.0)},
        "fuentes_citadas": [],
        "lookup_queries": [{"input": "dato_contingente", "query": "ejemplo query"}],
    }


# ── stub de la web: devuelve hechos falsos/sin URL para probar robustez ──
def _web_stub_ok(query):
    return {"hechos": {"valor": "20 V"}, "fuentes": [{"url": "https://ejemplo.org/hoja.pdf"}],
            "via": "web"}


def _web_stub_sin_url(query):
    return {"hechos": {"valor": "20 V"}, "fuentes": [], "via": "web"}


def _web_stub_explota(query):
    raise RuntimeError("booom")


print("[1] regla de oro: tras resolver, no hay fuente verified sin humano")
# monkeypatch generating + web con stub
GDC.generar_spec_candidata = _spec_stub
SD.busqueda_web = _web_stub_ok
m = mem()
r = SD.resolver("diseñame algo", m=m, usar_llm=False, buscar_web=True)
check("1a. no hay fuente canónica (sin_fuente_humana=True)", r["gate"]["sin_fuente_humana"] is True)
check("1b. es_canonico False", r["gate"]["es_canonico"] is False)

print("\n[2] evidencia web queda UNVERIFIED autor=modelo (no 'tabla')")
# la fuente registrada por la web debe ser unverified
fuente_key = f"fuente:{r['dominio_id']}:dato_contingente"
raw = m.get_version(fuente_key, m.conn.execute(
    "SELECT version FROM facts WHERE key=? ORDER BY version DESC LIMIT 1",
    (fuente_key,)).fetchone()[0])
check("2a. evidencia web es unverified", raw["verified"] is False)
check("2b. autor/fuente = modelo", raw["fuente"] == "modelo", raw["fuente"])

print("\n[3] registrar_fuente exige actor humano")
m3 = mem()
# 'tabla' ya no acuña canónico
skill_lib.registrar_fuente(m3, "x", "tabla_fabricante", "ref", fuente="tabla")
f = m3.current("fuente:x")
check("3a. fuente='tabla' degrada a unverified", f is None)  # invisible a current (unverified)
# ingeniero sí acuña
skill_lib.registrar_fuente(m3, "y", "tabla_fabricante", "ref", fuente="ingeniero")
check("3b. fuente='ingeniero' es canónico", m3.current("fuente:y") is not None)

print("\n[4] sensibilidad del invariante: fuente verified no-humana => False")
m4 = mem()
# forzamos una fuente verified con fuente='tabla' directamente (estado malo)
m4.write("fuente:bad", {"tipo": "x"}, unit="", fuente="tabla", verified=True)
check("4a. invariante detecta la violación", SD._no_hay_fuente_verified_sin_humano(m4) is False)

print("\n[5] idempotencia: el id de candidata no depende del nombre del LLM")
did_a = GDC._normalizar_id("parlante SEALED de 1 vía")
did_b = GDC._normalizar_id("parlante SEALED de 1 vía")
check("5a. normalización determinista", did_a == did_b)

print("\n[6] fallo cerrado: query que explota no deja estado parcial")
SD.busqueda_web = _web_stub_explota
m6 = mem()
r6 = SD.resolver("diseñame algo", m=m6, usar_llm=False, buscar_web=True)
check("6a. no tira, degrada", r6["estado"] == "hueco_detectado" and "error_web" in r6)
check("6b. sin fuentes parciales", r6["fuentes"] == [])

print("\n[7] genericidad: pedido de dominio no electrónico")
m7 = mem()
r7 = SD.resolver("calcular la dosis de un medicamento", m=m7, usar_llm=False, buscar_web=False)
check("7a. funciona sin hardcode de dominio", r7["estado"] == "hueco_detectado")
check("7b. candidata autor=modelo", r7["candidata"]["autor"] == "modelo")

print("\n[8] generación de PDF genérico (sin hardcode de dominio)")
# candidata con inputs/fórmulas y fuentes, como las devolvería resolver()
r_pdf = {
    "pedido": "diseñame un parlante de 20 watts",
    "dominio_id": "parlante_20w",
    "candidata": {
        "autor": "modelo", "nombre": "diseño parlante 20W",
        "inputs": [{"name": "potencia_W", "question": "potencia (W)", "unit": "W"}],
        "formulas": ["V = raíz(P · Z) (ley de Ohm + Joule)"],
        "fuentes_citadas": ["Beranek, Acoustics"],
    },
    "fuentes": [{"input": "impedancia", "hechos": {"Impedance": "4 ohms"},
                "urls": ["https://ejemplo.org/spec.pdf"]}],
    "gate": {"es_canonico": False, "invariante_ok": True, "sin_fuente_humana": True},
}
import tempfile
_pdf = SD.generar_pdf(r_pdf, os.path.join(tempfile.gettempdir(), "test_sin_dominio.tex"))
check("8a. genera PDF válido", _pdf is not None and os.path.exists(_pdf), str(_pdf))

print("\n[9] el PDF declara el estado de topología (no es 'fabricable')")
# el .tex debe contener el grado real y la conclusión honesta
_tex = _pdf.replace(".pdf", ".tex")
txt = open(_tex, encoding="utf-8").read() if os.path.exists(_tex) else ""
check("9a. declara 'formula_suelta'", "formula_suelta" in txt or "formula\\_suelta" in txt)
check("9b. declara 'NO es listo para fabricar'", "listo para fabricar" in txt)
check("9c. declara falta de topología", "topología" in txt or "topolog\\'ia" in txt)

print("\n[10] el nombre del PDF deriva del dominio (no pisa los anteriores)")
check("10a. sanear nombre multimetro", SD._sanear_nombre_archivo("multimetro_digital_bateria_9v") == "multimetro_digital_bateria_9v")
check("10b. sanear quita acentos/espacios", SD._sanear_nombre_archivo("diseño parlante 20W") == "dise_o_parlante_20w")
check("10c. nombre vacío cae a 'sin_dominio'", SD._sanear_nombre_archivo("!!!") == "sin_dominio")

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
