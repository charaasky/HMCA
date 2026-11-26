#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""pedir_cualquier_cosa.py — 'ask for anything' flow (post-authorize, not pre-declare).

Implements Fable's cycle (pedir_cualquier_cosa):
  1. GAP DETECTION: the request does not match any registered domain => it SAYS so.
  2. SOURCE SEARCH: the missing datum (lm/W efficacy) is looked up in a web catalog as
     `fuente:` (oracle by citation; the LLM NEVER signs it).
  3. PROPOSAL of a candidate: candidate domain `autor=modelo` with deduced formulas.
  4. HONESTY gate: the candidate stays `asumida`/unverified; to become a canonical
     domain a HUMAN must ratify it (registrar_spec + registrar_fuente with author).

This demonstrates 'if I don't know, I say so, I look up the datum, and I propose the domain as a draft':
the 100-lumen LED luminaire is solved with P=Φ/η (a formula that does NOT change) using η from the
catalog. Nothing is invented.

Usage:
    PYTHONPATH=/home/charasky/Documentos/HMC/.deps python3 arquitectura/scripts/pedir_cualquier_cosa.py
"""
import os
import sys
import json

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from prototipo import DOMINIOS, busqueda_web  # noqa: E402
from dominio_luminaria_led import DOMINIO_LUMINARIA_LED, calcular, FUENTE_LED  # noqa: E402
import topologia as T  # noqa: E402


def resolver(pedido):
    """Full flow. Returns a dict with the system's honest decision."""
    out = {"pedido": pedido}

    # 1. GAP DETECTION: does it match any registered domain?
    pedido_norm = pedido.lower()
    matched = [k for k, v in DOMINIOS.items()
               if any(tok in pedido_norm for tok in k.replace("_", " ").split())]
    if matched:
        out["estado"] = "dominio_existente"
        out["dominios"] = matched
        return out

    # no match => we say so (we don't lie)
    out["estado"] = "hueco_detectado"
    out["mensaje"] = "No tengo un dominio para este pedido; lo declaro y propongo una candidata."

    # 2. SOURCE SEARCH (the missing datum, e.g. lm/W efficacy)
    # In a real flow, the router would decide which datum is missing; here we make it explicit for
    # the LED luminaire: the datum is the efficacy (lm/W).
    if "luminaria" in pedido_norm or "led" in pedido_norm or "lumen" in pedido_norm:
        fuente = FUENTE_LED
        out["fuente_propuesta"] = fuente
    else:
        fuente = None
        out["fuente_propuesta"] = None

    # 3. PROPOSAL of a candidate (autor=modelo)
    if fuente is not None:
        r = calcular(DOMINIO_LUMINARIA_LED["demo_inputs"],
                     eficacia_lm_por_W=fuente["eficacia_lm_por_W"])
        out["candidata"] = {
            "autor": DOMINIO_LUMINARIA_LED["autor"],  # 'modelo' -> not canonical
            "resultado": r,
            "formulas": ["P = Φ/η", "I = P/V", "S_cable = I/J"],
        }

    # 4. HONESTY gate: the candidate is NOT canonical until human ratification
    out["gate"] = {
        "es_canonico": False,
        "topologia": T.ASUMIDA,
        "requiere": "registrar_spec + registrar_fuente por humano (ingeniero/usuario)",
    }
    return out


def main():
    pedido = "diseñame una luminaria LED de 100 lúmenes"
    r = resolver(pedido)

    print("=" * 74)
    print(f"PEDIDO: {pedido}")
    print("=" * 74)
    print(f"\n[1] Detección de hueco: {r['estado']}")
    if r["estado"] == "hueco_detectado":
        print(f"    → {r['mensaje']}")

    if "fuente_propuesta" in r and r["fuente_propuesta"]:
        f = r["fuente_propuesta"]
        print(f"\n[2] Dato que falta, buscado en catálogo (no inventado):")
        print(f"    eficacia = {f['eficacia_lm_por_W']} lm/W  ({f['ref']})")

    if "candidata" in r:
        c = r["candidata"]
        print(f"\n[3] Candidata (autor={c['autor']}, NO canónica):")
        print(f"    fórmulas: {', '.join(c['formulas'])}")
        for k, v in c["resultado"].items():
            print(f"    {k}: {v}")

    print(f"\n[4] Gate de honestidad:")
    print(f"    es_canónico = {r['gate']['es_canonico']}  (topología {r['gate']['topologia']})")
    print(f"    para ser dominio real falta: {r['gate']['requiere']}")

    print("\n" + "=" * 74)
    print("Conclusión: el sistema DECLARA el hueco, BUSCA el dato, PROPONE candidata")
    print("autor=modelo, y NO la auto-promueve. Ese es el flujo 'pedir cualquier cosa'.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
