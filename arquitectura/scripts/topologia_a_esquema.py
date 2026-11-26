#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""topologia_a_esquema.py — converts the block graph (LLM) into the netlist that
`esquematico_svg.render_svg` expects, to be able to draw the PDF's SCHEMATIC.

The block graph (topologia_bloques) has structure:
    {
      "bloques": [{"id": str, "tipo": str, "puertos": [{"id": str, "dominio": str}]}],
      "nets": [{"id": str, "conexiones": [{"bloque": str, "puerto": str}]}],
    }

The schematic (esquematico_svg) expects:
    {
      "componentes": [{"ref": str, "nombre": str, "pines": [{"num": int, "nombre": str}]}],
      "conexiones": [{"de": {"ref": str, "pin": int}, "a": {"ref": str, "pin": int}}],
    }

Conversion:
  - each BLOCK -> a COMPONENT; its id is the ref, its type is the name.
  - each PORT -> a PIN (numbered by position 1..N); the pin name is the port id.
  - each NET -> as many CONNECTIONS as consecutive pairs of ports in that net
    (to draw the cable that joins all the net's ports in a chain).

This conversion is DETERMINISTIC and honest: it draws what the LLM proposed, without inventing
pins or connections. If the net has <2 connections, it produces no cable (loose net, already
rejected by the topologia_bloques validator).
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)


def grafo_a_netlist(grafo):
    """Converts block graph to schematic netlist. Returns netlist or None."""
    if not isinstance(grafo, dict):
        return None
    bloques = grafo.get("bloques") or []
    nets = grafo.get("nets") or []
    if not bloques:
        return None

    componentes = []
    pin_map = {}  # (bloque_id, puerto_id) -> num_pin
    for b in bloques:
        if not isinstance(b, dict) or not isinstance(b.get("id"), str):
            continue
        ref = b["id"]
        tipo = b.get("tipo", "")
        puertos = b.get("puertos") or []
        pines = []
        for i, p in enumerate(puertos):
            pid = p.get("id") if isinstance(p, dict) else str(p)
            dom = p.get("dominio", "") if isinstance(p, dict) else ""
            num = i + 1
            pines.append({"num": num, "nombre": f"{pid}" + (f" ({dom})" if dom else "")})
            pin_map[(ref, pid)] = num
        componentes.append({"ref": ref, "nombre": tipo, "pines": pines})

    # connections: for each net, chain its ports
    conexiones = []
    for n in nets:
        if not isinstance(n, dict):
            continue
        cs = n.get("conexiones") or []
        # collect the pairs (bloque, puerto) that exist
        pares = []
        for c in cs:
            if isinstance(c, dict):
                b, p = c.get("bloque"), c.get("puerto")
                if (b, p) in pin_map:
                    pares.append((b, p))
        # chain: each consecutive pair -> one pin-to-pin connection
        for k in range(len(pares) - 1):
            b1, p1 = pares[k]
            b2, p2 = pares[k + 1]
            conexiones.append({
                "de": {"ref": b1, "pin": pin_map[(b1, p1)]},
                "a": {"ref": b2, "pin": pin_map[(b2, p2)]},
            })

    return {"componentes": componentes, "conexiones": conexiones}


if __name__ == "__main__":
    grafo = {
        "bloques": [
            {"id": "fuente_12V", "tipo": "fuente_tension",
             "puertos": [{"id": "V+", "dominio": "potencia"}, {"id": "GND", "dominio": "tierra"}]},
            {"id": "R1", "tipo": "resistor",
             "puertos": [{"id": "t1", "dominio": "potencia"}, {"id": "t2", "dominio": "potencia"}]},
            {"id": "R2", "tipo": "resistor",
             "puertos": [{"id": "t1", "dominio": "potencia"}, {"id": "t2", "dominio": "potencia"}]},
        ],
        "nets": [
            {"id": "net_12V", "conexiones": [{"bloque": "fuente_12V", "puerto": "V+"}, {"bloque": "R1", "puerto": "t1"}]},
            {"id": "net_5V", "conexiones": [{"bloque": "R2", "puerto": "t1"}, {"bloque": "R1", "puerto": "t2"}]},
        ],
    }
    import json
    print(json.dumps(grafo_a_netlist(grafo), indent=2, ensure_ascii=False))
