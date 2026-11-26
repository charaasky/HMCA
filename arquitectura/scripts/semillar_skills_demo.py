#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""semillar_skills_demo.py — registers the domains/skills we were using, with their CONNECTIONS.

Populates HCMA memory with a graph of interconnected skills (`fuente:*` / `cota:*` oracles
+ skills with `derived_from` via the golden tests), so the dashboard shows the lineage TREE
(nodes + edges) and how different domains interconnect.

Uses HCMA primitives (`skill_lib`), does NOT write SQL by hand: each skill follows the
spec → impl → test (oracles) → ok → active cycle. The CONNECTIONS come from:
  - `fuente:<fid>` / `cota:*` : trust roots (independent oracles).
  - `skill:<otro>:spec`     : dependency between skills (one domain reuses another).

Re-runnable: if a skill already has a current spec, it skips it (idempotent by id).

The skills are DETERMINISTIC (pure functions, no network/fs/import): each impl is compiled in isolation
and evaluated against golden tests whose expected values are independent of the code.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import skill_lib as SK  # noqa: E402

DB = os.environ.get(
    "HCMA_SKILLS_DB",
    os.path.join(SCRIPTS, "..", "data", "memoria.db"))


# ─────────────────────────────────────────────────────────────────────────────
# Oracles (trust roots) — NOT minted by the agent, signed by the human
# ─────────────────────────────────────────────────────────────────────────────
FUENTES = [
    ("fisica_electromagnetismo", "texto_referencia",
     "Ley de Ohm, potencia eléctrica, resonancia LC (física clásica)"),
    ("permeabilidad_vacio", "texto_referencia",
     "Permeabilidad magnética del vacío μ0 = 4π×10⁻⁷ H/m"),
    ("eficacia_led", "tabla_fabricante",
     "Eficacia luminosa típica de LED blancos 80–120 lm/W"),
    ("norma_audio", "norma",
     "SPL de referencia a 1 W / 1 m (sensibilidad de parlantes)"),
    ("teoria_complejidad", "texto_referencia",
     "2-SAT decidible en tiempo polinomial (implicación + SCC)"),
]

COTAS = [
    ("electrica", "densidad_corriente", "cobre", 1.0, 6.0, "A/mm2"),
    ("magnetica", "flujo_densidad", "acero_silicio", 0.1, 1.8, "T"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Skills (id, spec, impl, demo usages)
# -----------------------------------------------------------------------------
# `fuente` of each golden test lists the DEPENDENCIES: `fuente:*`/`cota:*` (oracle)
# and `skill:<otro>:spec` (reuse of another domain). The graph comes from there.
# ─────────────────────────────────────────────────────────────────────────────
SKILLS = [
    {
        "id": "ley_ohm",
        "nombre": "Ley de Ohm y potencia",
        "codigo": (
            "def calcular(d):\n"
            "    V = d.get('V'); I = d.get('I'); R = d.get('R')\n"
            "    if V is not None and R is not None and R != 0:\n"
            "        V = float(V); R = float(R); I = V / R\n"
            "        return {'I': I, 'P': V * I}\n"
            "    if I is not None and R is not None:\n"
            "        I = float(I); R = float(R); V = I * R\n"
            "        return {'V': V, 'P': V * I}\n"
            "    if V is not None and I is not None and I != 0:\n"
            "        V = float(V); I = float(I)\n"
            "        return {'R': V / I, 'P': V * I}\n"
            "    raise ValueError('datos insuficientes')\n"),
        "tests": [
            {"id": "corriente_potencia", "inputs": {"V": 12, "R": 4},
             "esperado": {"I": 3.0, "P": 36.0},
             "fuente": ["fuente:fisica_electromagnetismo"]},
        ],
        "usos": 3,
    },
    {
        "id": "inductancia_bobina",
        "nombre": "Inductancia de una bobina (L = μ·N²·A/l)",
        "codigo": (
            "def calcular(d):\n"
            "    mu0 = 4 * math.pi * 1e-7\n"
            "    N = float(d['N']); A = float(d['A'])\n"
            "    l = float(d['l']); mur = float(d['mu_r'])\n"
            "    return {'L': mu0 * mur * N * N * A / l}\n"),
        "tests": [
            {"id": "bobina_nucleo", "inputs": {"N": 100, "A": 1e-4, "l": 0.05, "mu_r": 1000},
             "esperado": {"L": 0.025132741228718343},
             "fuente": ["fuente:fisica_electromagnetismo", "fuente:permeabilidad_vacio"]},
        ],
        "usos": 3,
    },
    {
        "id": "capacitancia_placas",
        "nombre": "Capacitancia de placas paralelas (C = ε·A/d)",
        "codigo": (
            "def calcular(d):\n"
            "    e0 = 8.8541878128e-12\n"
            "    A = float(d['A']); dsep = float(d['d']); er = float(d['er'])\n"
            "    return {'C': e0 * er * A / dsep}\n"),
        "tests": [
            {"id": "placa_aire", "inputs": {"A": 1e-4, "d": 1e-3, "er": 1},
             "esperado": {"C": 8.854187812800001e-13},
             "fuente": ["fuente:fisica_electromagnetismo"]},
        ],
        "usos": 1,
    },
    {
        "id": "resonancia_lc",
        "nombre": "Frecuencia de resonancia LC (f = 1/(2π√(LC)))",
        "codigo": (
            "def calcular(d):\n"
            "    L = float(d['L']); C = float(d['C'])\n"
            "    return {'f': 1 / (2 * math.pi * math.sqrt(L * C))}\n"),
        "tests": [
            {"id": "resonancia_tipica", "inputs": {"L": 1e-3, "C": 1e-6},
             "esperado": {"f": 5032.921210448703},
             "fuente": ["fuente:fisica_electromagnetismo",
                        "skill:inductancia_bobina:spec",
                        "skill:capacitancia_placas:spec"]},
        ],
        "usos": 2,
    },
    {
        "id": "transformador",
        "nombre": "Relación de transformación (Vs = Vp·Ns/Np)",
        "codigo": (
            "def calcular(d):\n"
            "    Vp = float(d['Vp']); Np = float(d['Np']); Ns = float(d['Ns'])\n"
            "    return {'Vs': Vp * Ns / Np, 'relacion': Ns / Np}\n"),
        "tests": [
            {"id": "reduccion_220_22", "inputs": {"Vp": 220, "Np": 1000, "Ns": 100},
             "esperado": {"Vs": 22.0, "relacion": 0.1},
             "fuente": ["fuente:fisica_electromagnetismo", "skill:inductancia_bobina:spec"]},
        ],
        "usos": 4,
    },
    {
        "id": "fuente_lineal",
        "nombre": "Rectificación + filtro (Vdc = Vrms·√2 − Vdrop)",
        "codigo": (
            "def calcular(d):\n"
            "    Vrms = float(d['Vrms']); Vdrop = float(d['Vdrop'])\n"
            "    return {'Vdc': Vrms * math.sqrt(2) - Vdrop}\n"),
        "tests": [
            {"id": "9v_ac_a_dc", "inputs": {"Vrms": 9, "Vdrop": 1.4},
             "esperado": {"Vdc": 11.327922061357857},
             "fuente": ["fuente:fisica_electromagnetismo",
                        "skill:transformador:spec", "skill:ley_ohm:spec"]},
        ],
        "usos": 2,
    },
    {
        "id": "estabilizador_ferroresonante",
        "nombre": "Estabilizador ferroresonante (CVT): flujo en el núcleo",
        "codigo": (
            "def calcular(d):\n"
            "    V = float(d['V']); f = float(d['f'])\n"
            "    N = float(d['N']); A = float(d['A'])\n"
            "    B = V / (4.44 * f * N * A)\n"
            "    return {'B': B, 'en_saturacion': B > 1.8}\n"),
        "tests": [
            {"id": "flujo_nominal", "inputs": {"V": 220, "f": 50, "N": 1000, "A": 1e-3},
             "esperado": {"B": 0.990990990990991, "en_saturacion": False},
             "fuente": ["skill:resonancia_lc:spec", "skill:inductancia_bobina:spec",
                        "skill:transformador:spec",
                        "cota:magnetica:flujo_densidad:acero_silicio"]},
        ],
        "usos": 1,
    },
    {
        "id": "parlante",
        "nombre": "SPL de un parlante (sensibilidad + 10·log10(P))",
        "codigo": (
            "def calcular(d):\n"
            "    P = float(d['P']); sens = float(d['sensibilidad'])\n"
            "    return {'SPL': sens + 10 * math.log10(P)}\n"),
        "tests": [
            {"id": "15w_88db", "inputs": {"P": 15, "sensibilidad": 88},
             "esperado": {"SPL": 99.76091259055681},
             "fuente": ["fuente:norma_audio", "skill:ley_ohm:spec"]},
        ],
        "usos": 2,
    },
    {
        "id": "luminaria_led",
        "nombre": "Flujo luminoso de un LED (lm = P · eficacia)",
        "codigo": (
            "def calcular(d):\n"
            "    P = float(d['P']); ef = float(d['eficacia'])\n"
            "    return {'lumenes': P * ef}\n"),
        "tests": [
            {"id": "100_lumenes", "inputs": {"P": 1.0, "eficacia": 100},
             "esperado": {"lumenes": 100.0},
             "fuente": ["fuente:eficacia_led", "skill:ley_ohm:spec"]},
        ],
        "usos": 1,
    },
    {
        "id": "cargador_bateria",
        "nombre": "Tiempo de carga (t = capacidad / corriente)",
        "codigo": (
            "def calcular(d):\n"
            "    cap = float(d['capacidad_mAh']); I = float(d['corriente_mA'])\n"
            "    return {'tiempo_h': cap / I}\n"),
        "tests": [
            {"id": "4000mah_1a", "inputs": {"capacidad_mAh": 4000, "corriente_mA": 1000},
             "esperado": {"tiempo_h": 4.0},
             "fuente": ["skill:fuente_lineal:spec", "skill:ley_ohm:spec"]},
        ],
        "usos": 1,
    },
    {
        "id": "multimetro",
        "nombre": "Medición de corriente por shunt (I = Vshunt/Rshunt)",
        "codigo": (
            "def calcular(d):\n"
            "    V = float(d['Vshunt']); R = float(d['Rshunt'])\n"
            "    return {'I': V / R}\n"),
        "tests": [
            {"id": "shunt_10a", "inputs": {"Vshunt": 0.1, "Rshunt": 0.01},
             "esperado": {"I": 10.0},
             "fuente": ["skill:ley_ohm:spec"]},
        ],
        "usos": 1,
    },
    {
        "id": "sat2",
        "nombre": "Decisor 2-SAT (búsqueda exhaustiva determinista)",
        "codigo": (
            "def calcular(d):\n"
            "    n = int(d['n']); clauses = d['clausulas']\n"
            "    for mask in range(1 << n):\n"
            "        ok = True\n"
            "        for a, b in clauses:\n"
            "            va = (mask >> (abs(a) - 1)) & 1\n"
            "            vb = (mask >> (abs(b) - 1)) & 1\n"
            "            la = (va == 1) if a > 0 else (va == 0)\n"
            "            lb = (vb == 1) if b > 0 else (vb == 0)\n"
            "            if not (la or lb):\n"
            "                ok = False\n"
            "                break\n"
            "        if ok:\n"
            "            return {'satisfacible': True}\n"
            "    return {'satisfacible': False}\n"),
        "tests": [
            {"id": "satisfacible", "inputs": {"n": 2, "clausulas": [[1, 2], [-1, 2]]},
             "esperado": {"satisfacible": True},
             "fuente": ["fuente:teoria_complejidad"]},
            {"id": "insatisfacible", "inputs": {"n": 1, "clausulas": [[1, 1], [-1, -1]]},
             "esperado": {"satisfacible": False},
             "fuente": ["fuente:teoria_complejidad"]},
        ],
        "usos": 1,
    },
]


def main():
    m = HCMAMemoriaSQLite(DB)

    # 1. oracles (trust roots)
    for fid, tipo, ref in FUENTES:
        if m.current(f"fuente:{fid}") is None:
            SK.registrar_fuente(m, fid, tipo, ref, fuente="ingeniero")
    for dominio, magnitud, regimen, mn, mx, unidad in COTAS:
        if m.current(f"cota:{dominio}:{magnitud}:{regimen}") is None:
            SK.registrar_cota(m, dominio, magnitud, regimen, mn, mx, unidad,
                              fuente="tabla", ref="física/ingeniería de referencia")

    # 2. skills in topological order (dependencies first)
    resumen = []
    for s in SKILLS:
        sid = s["id"]
        if m.current(f"skill:{sid}:spec") is not None:
            resumen.append({"id": sid, "estado": "ya_registrado"})
            continue
        spec = {"nombre": s["nombre"], "tests_dorados": s["tests"]}
        SK.registrar_spec(m, sid, spec, fuente="ingeniero")
        version_impl, impl_hash = SK.proponer_impl(m, sid, s["codigo"], fuente="modelo")
        ev = SK.evaluar(m, sid, version_impl)
        estado = "ok" if ev.get("todos_ok") else "FALLO:" + str(ev.get("motivo"))
        if ev.get("todos_ok"):
            SK.activar(m, sid, impl_hash)
        # demo usages (for the ranking / 'usos' column)
        for i in range(s.get("usos", 0)):
            ok_v = ev.get("ok_version")
            if ok_v is not None:
                SK.registrar_uso(m, sid, f"demo-{i}", impl_hash, ok_v)
        resumen.append({"id": sid, "estado": estado, "impl_hash": impl_hash[:10]})

    print("=== Skills registrados ===")
    for r in resumen:
        print(f"  {r['id']:28s} {r['estado']}")

    # 3. graph summary (nodes + edges via the dashboard tree)
    try:
        sys.path.insert(0, os.path.join(SCRIPTS, "..", "dashboard"))
        import skills as DSK
        arbol = DSK.arbol_skills(m)
        a = arbol["analisis"]
        print(f"\n=== Árbol de skills (linaje) ===")
        print(f"  skills: {a['n_skills']} · nodos: {a['n_nodos']} · oráculos: {a['n_oraculos']}")
        print(f"  raíces: {a['raices']}")
        print(f"  hojas: {a['hojas']}")
        print(f"  profundidad máxima: {a['profundidad_maxima']} · ciclos: {a['ciclos']}")
        print(f"  aristas (skill → dependencia):")
        for e in arbol["aristas"]:
            print(f"    {e['de']}  →  {e['a']}")
    except Exception as e:
        print(f"\n(árbol no calculado: {e})")

    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
