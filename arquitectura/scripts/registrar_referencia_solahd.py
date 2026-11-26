#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Registers the reference of the real product (SolaHD CVS 2000 VA) as a governed `fuente:<fid>`
in HCMA, with its URL as a verifiable citation.

Demonstrates integration (b): web search (via OpenRouter web plugin) brings real
manufacturer specs, and those specs are materialized as a canonical source of type
`tabla_fabricante` with `ref` = URL + data, entering the lineage as appropriate.

Careful with the golden rule: the LLM/web is INHERENTLY untrustworthy. These data are
registered as `fuente:` of type `tabla_fabricante` because they ARE public specs of a
manufacturer (SolaHD), but their expected value must be verifiable against the cited URL.
The authority comes from it being a public manufacturer datasheet, NOT the LLM.

Usage:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 \
      arquitectura/scripts/registrar_referencia_solahd.py
"""

import os
import sys
import tempfile
import json

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import skill_lib  # noqa: E402
import cvt_dimensionamiento  # noqa: E402


# Real product data obtained by web search (OpenRouter web plugin), with URLs.
REFERENCIA_SOLAHD = {
    "fuente_id": "solahd_cvs_2000va",
    "tipo": "tabla_fabricante",
    "ref": "SolaHD CVS Hardwired, 2000 VA, 220 V (hoja de datos + catálogo)",
    "urls": [
        "https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/7990/PdfFile_126538.pdf",
        "https://regulatronic.mx/producto/regulador-de-voltaje-ferroresonante-23-22-220-2-kva/",
    ],
    "datos": {
        "potencia_VA": 2000,
        "tension_V": 220,
        "frecuencia_Hz": 60,
        "regulacion_pct": 1.0,
        "thd_pct": 3.0,
        "eficiencia_max_pct": 92.0,
        "caja_cm": {"ancho": 29.0, "alto": 42.5, "profundidad": 27.3},
        "peso_kg": 50.9,
    },
}


def run():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_referencia_")
    os.close(fd)
    m = HCMAMemoriaSQLite(path)

    ref = REFERENCIA_SOLAHD
    fid = ref["fuente_id"]

    print("=" * 72)
    print("REGISTRO DE REFERENCIA REAL (SolaHD CVS 2000 VA) como fuente gobernada")
    print("=" * 72)

    # 1. fuente:<fid> canonical (type tabla_fabricante, with URL as citation)
    v_fuente = m.write(
        f"fuente:{fid}",
        {"tipo": ref["tipo"], "ref": ref["ref"], "datos": ref["datos"],
         "urls": ref["urls"]},
        unit="", fuente="tabla", verified=True,
    )
    print(f"[1] fuente:{fid}  -> version {v_fuente} (verified, fuente HCMA='tabla')")
    print(f"    (la autoridad la da que es una hoja de datos pública de fabricante)")
    print()

    # 2. compare with our deterministic sizing (at 50 Hz)
    d = cvt_dimensionamiento.dimensionar()
    print("[2] comparación determinista (nuestro diseño 50 Hz vs real 60 Hz):")
    comparaciones = [
        ("corriente_A", d["corriente_A"], "corriente nominal",
         ref["datos"]["potencia_VA"] / ref["datos"]["tension_V"]),
        ("eficiencia_pct", d["eficiencia_pct"], "eficiencia %",
         ref["datos"]["eficiencia_max_pct"]),
    ]
    for key, nuestro, nombre, real in comparaciones:
        print(f"    {nombre:<18} nuestro={nuestro:<8} real~={real}")
    print()

    # 3. the box: does our core fit?
    lado = d["seccion_nucleo_cm"]
    nucleo_ancho = lado * 3.0
    nucleo_alto = lado * 2.0
    caja = ref["datos"]["caja_cm"]
    entra = nucleo_ancho <= caja["ancho"] and nucleo_alto <= caja["profundidad"]
    print(f"[3] ¿núcleo (E-I ~{nucleo_ancho:.1f}x{nucleo_alto:.1f} cm) entra en")
    print(f"    caja {caja['ancho']}x{caja['profundidad']} cm (planta)? → {'SÍ' if entra else 'NO'}")
    print()

    # 4. the non-computable part: regulation and THD depend on the shunt (secret)
    print("[4] frontera de conocimiento:")
    print("    regulación ±1% y THD 3% del CVT: NO calculables sin curva B-H del shunt")
    print("    (secreto industrial). Nuestro diseño NO puede afirmar estos valores.")
    print()

    # 5. summary of grades
    print("[5] valores deterministas y su clase:")
    for k in ("corriente_A", "vueltas_primario", "vueltas_secundario_resonante",
              "vout_saturacion_V", "eficiencia_pct", "C_uF", "L_mH",
              "area_nucleo_cm2", "regulacion_exactitud_pct", "THD_pct"):
        clase = d.get(k + "_clase", "?")
        val = d.get(k, "?")
        val_s = "—" if val is None else str(val)
        print(f"    {k:<28} {val_s:<8} [{clase}]")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(run())
