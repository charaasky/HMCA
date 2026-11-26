#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""heladera_gafa_hcma.py — real case WITH writes to HCMA memory (governed lineage).

Unlike `heladera_gafa.py` (which only printed a derived JSON), this script
MATERIALIZES all facts into `memoria_sqlite.HCMAMemoriaSQLite` with their lineage, so that
"reading the results" is reading `current()` of each real governed fact:

  fuente:gafa_hgw311p          → real refrigerator specs (manufacturer_table, with URL).
  cota:heladera:S_diseno        → power choice (SELECTION, not canonical).
  sim:heladera:thd              → THD by ngspice simulation (derived, not root).
  heladera:<magnitud>           → each computed value, derived from source/bound/sim.

Golden rule met: what has no source/bound/lineage authority stays unverified and
the degree is COMPUTED from the lineage (linaje.grado), not declared.

At the end, it reads and shows `current()` of each fact (pure read of the governed memory).

Usage:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/heladera_gafa_hcma.py
"""
import os
import sys
import json
import tempfile

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import linaje  # noqa: E402
from cvt_dimensionamiento import dimensionar  # noqa: E402
from selector_componentes import bom_capacitor  # noqa: E402


# ── REAL SPECS (OpenRouter web search, 2026-11-10) ────────────────────────
FUENTE_HELADERA = {
    "fuente_id": "gafa_hgw311p",
    "tipo": "tabla_fabricante",
    "ref": "Heladera Gafa HGW311P 319L Top Freezer (consumo 370 kWh/año, 220V)",
    "urls": [
        "https://elgarageelectronica.com/productos/heladera-gafa-319-litros-top-freezer-con-dispenser-hgw311p-silver-19rg9/",
        "https://tosellihogar.com.ar/producto/heladera-gafa-hgw311p-plata-wd/",
    ],
    "datos": {
        "consumo_anual_kWh": 370.0,
        "tension_V": 220.0,
        "capacidad_L": 319,
    },
}
S_DISENO_VA = 1500.0   # choice (SELECTION) to cover the 800-1200W start peak


def run():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_heladera_")
    os.close(fd)
    m = HCMAMemoriaSQLite(path)

    print("=" * 74)
    print("HELADERA GAFA HGW311P — flujo HCMA (hechos gobernados con linaje)")
    print("=" * 74)

    # 1. canonical source (manufacturer_table, public spec)
    v_fuente = m.write(
        f"fuente:{FUENTE_HELADERA['fuente_id']}",
        {"tipo": FUENTE_HELADERA["tipo"], "ref": FUENTE_HELADERA["ref"],
         "datos": FUENTE_HELADERA["datos"], "urls": FUENTE_HELADERA["urls"]},
        unit="", fuente="tabla", verified=True,
    )
    print(f"[1] fuente:{FUENTE_HELADERA['fuente_id']} -> v{v_fuente} (verified)")

    # 2. bound: design power (SELECTION, not canonical; the engineer marks it)
    #    we write it as cota:* (oracle by namespace) but with fuente='ingeniero'
    #    and metadata documenting WHY 1500 VA (start peak 800-1200W).
    v_cota = m.write(
        "cota:heladera:S_diseno",
        {"VA": S_DISENO_VA, "motivo": "cubrir pico de arranque del compresor (800-1200W)",
         "pico_arranque_W": "800-1200"},
        unit="VA", fuente="ingeniero", verified=True,
    )
    print(f"[2] cota:heladera:S_diseno -> v{v_cota} (verified, fuente ingeniero)")

    # 3. deterministic sizing (derived from the bound)
    d = dimensionar(Vin=220.0, Vout=220.0, S=S_DISENO_VA, f=50.0)

    magnitudes = [
        ("corriente_A", "A", d["corriente_A"]),
        ("area_nucleo_cm2", "cm2", d["area_nucleo_cm2"]),
        ("vueltas_primario", "1", d["vueltas_primario"]),
        ("vueltas_secundario_resonante", "1", d["vueltas_secundario_resonante"]),
        ("C_uF", "uF", d["C_uF"]),
        ("L_mH", "mH", d["L_mH"]),
        ("eficiencia_pct", "%", d["eficiencia_pct"]),
    ]
    print("\n[3] Hechos derivados (calculadora, linaje -> cota):")
    versiones = {}
    for key, unidad, val in magnitudes:
        v = m.write(f"heladera:{key}", {"valor": val}, unit=unidad,
                    fuente="calculadora", verified=True,
                    derived_from={"cota:heladera:S_diseno": v_cota})
        versiones[key] = v
        f = m.current(f"heladera:{key}")
        grado = linaje.grado(m, f"heladera:{key}", v)
        print(f"    heladera:{key:<28} = {val} {unidad}  [verified={f['verified']}, "
              f"grado={grado}]")

    # 4. capacitor BOM (derived from the C magnitude and the bound)
    I_rms = d["corriente_A"]
    bom = bom_capacitor(d["C_uF"], 220.0, I_rms)
    v_bom = m.write(
        "heladera:bom_capacitor",
        {"C_comercial_uF": bom["valor_comercial_uF"], "arreglo": bom["arreglo"],
         "n_piezas": bom["n_piezas"], "error_relativo": bom["error_relativo"],
         "tension_nominal_VAC": bom["tension_nominal_VAC"],
         "tension_ok": bom["tension_ok"]},
        unit="", fuente="calculadora", verified=True,
        derived_from={"heladera:C_uF": versiones["C_uF"]},
    )
    f_bom = m.current("heladera:bom_capacitor")
    print(f"\n[4] heladera:bom_capacitor -> {bom['valor_comercial_uF']} uF "
          f"(arreglo {bom['arreglo']})")
    print(f"    tension_ok = {bom['tension_ok']}  (ADVERTENCIA: 440VAC vs pico ~622V)")

    # 5. THD by simulation (derived from the simulator, not root)
    print("\n[5] THD por simulación ngspice (derivado):")
    try:
        from simulador import thd_tanque_resonante
        thd_in, thd_out = thd_tanque_resonante(d["L_mH"], d["C_uF"])
        v_thd = m.write(
            "sim:heladera:thd",
            {"thd_entrada_pct": thd_in, "thd_salida_pct": thd_out,
             "reduccion_x": round(thd_in / thd_out, 1)},
            unit="%", fuente="calculadora", verified=True,
            derived_from={"heladera:L_mH": versiones["L_mH"],
                          "heladera:C_uF": versiones["C_uF"]},
        )
        f_thd = m.current("sim:heladera:thd")
        print(f"    sim:heladera:thd -> in {thd_in}% -> out {thd_out}% "
              f"(reducción {thd_in/thd_out:.1f}x)  [verified={f_thd['verified']}]")
    except Exception as e:
        print(f"    (simulador no disponible: {e})")

    # 6. READING RESULTS FROM THE GOVERNED MEMORY (current of each fact)
    print("\n" + "=" * 74)
    print("LECTURA DE RESULTADOS — hechos vigentes (current) en la memoria HCMA:")
    print("=" * 74)
    for k in sorted(m.keys("")):
        f = m.current(k)
        if f is None:
            continue
        gr = linaje.grado(m, k, f["version"])
        print(f"  {k:<32} = {json.dumps(f['value'], ensure_ascii=False)}  "
              f"[{f['fuente']}][verified={f['verified']}][grado={gr}]")

    print("\n" + "=" * 74)
    print("Un hecho 'seleccion' (area_nucleo, etc.) NO es canónico: su grado depende de")
    print("que la cota/ingeniero sea autoridad. La REGULACIÓN ±1% exacta no es calculable")
    print("(depende del shunt magnético = secreto industrial).")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(run())
