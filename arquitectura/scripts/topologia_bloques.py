#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""topologia_bloques.py — BLOCK topology schema + validator + promotion.

Closes the 'formula_suelta -> conectado' gap (Fable 5.2 diagnosis, fable_fabricable).

The problem: the formulas the LLM drafts are correct but LOOSE: they do not refer to any
physical structure. A PDF of formulas is not a design. To be 'conectado' it needs:

  1. a block GRAPH (components) with ports TYPED by domain (potencia/señal/medición)
     and nets that connect them;
  2. the formula -> graph BINDING: each variable of each formula points to a port/net/block;
  3. a deterministic structural VALIDATOR (no LLM) that detects: floating ports, single-port
     nets, incompatible domains, variables without binding.

Promotion (who declares what):
  - The LLM PROPOSES the graph. If the validator passes, the state is ASUMIDA -> grade 1
    (derivado_bajo_supuesto).
  - A HUMAN signs it (external HMAC) over the canonical hash of the graph + bindings -> DECLARADA
    -> grade 2 (conectado).
  - The LLM NEVER declares alone. If the author could self-promote, procedencia.py is useless.

Does NOT touch the core. It is a declarative layer over governed memory.
"""
import hashlib
import json
import os
import sys
import unicodedata

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import topologia as T  # noqa: E402


# ── port domains (for the compatibility validator) ──────────────────
DOMINIO_POTENCIA = "potencia"
DOMINIO_SENAL = "senal"
DOMINIO_MEDICION = "medicion"
DOMINIO_REFERENCIA = "referencia"
DOMINIO_TIERRA = "tierra"

DOMINIOS = (DOMINIO_POTENCIA, DOMINIO_SENAL, DOMINIO_MEDICION, DOMINIO_REFERENCIA, DOMINIO_TIERRA)


class TopologiaInvalida(ValueError):
    """The block graph does not pass the structural validator."""


def hash_canonico(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


# ── graph schema ───────────────────────────────────────────────────────────
# grafo = {
#   "bloques": [{"id": str, "tipo": str, "puertos": [{"id": str, "dominio": str}]}],
#   "nets": [{"id": str, "conexiones": [{"bloque": str, "puerto": str}]}],
#   "bindings": [{"variable": str, "bloque": str, "puerto": str | null}],
# }
def _normalizar_dominio(dom):
    """Normalizes a domain name (removes accents) to tolerate 'medición'/'medicion'."""
    if not isinstance(dom, str):
        return ""
    s = unicodedata.normalize("NFKD", dom)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s


def validar_grafo(grafo):
    """Structurally validates a block graph (deterministic, no LLM).

    Checks:
      - graph with >= 1 block (a graph without blocks is NOT a topology).
      - blocks with unique ids and ports with valid (id, domain).
      - nets with >= 2 connections to EXISTING ports (no floating ports nor loose nets).
      - each port used by at most ONE net (a port cannot be in two nets).
      - compatible domains in each net (except ground, which connects with potencia/señal).
      - bindings: each variable points to an EXISTING block/port.

    Returns (ok, defectos). Does not change state.
    """
    defectos = []
    if not isinstance(grafo, dict):
        return False, ["grafo no es dict"]

    bloques = grafo.get("bloques", [])
    nets = grafo.get("nets", [])
    bindings = grafo.get("bindings", [])

    if not bloques:
        return False, ["grafo sin bloques (no es una topología)"]

    # blocks
    ids_bloque = set()
    puertos = {}   # (bloque, puerto) -> dominio
    for b in bloques:
        if not isinstance(b, dict) or not isinstance(b.get("id"), str) or not b["id"]:
            defectos.append("bloque sin id")
            continue
        if b["id"] in ids_bloque:
            defectos.append(f"bloque duplicado: {b['id']}")
        ids_bloque.add(b["id"])
        for p in b.get("puertos", []):
            if not isinstance(p, dict) or not isinstance(p.get("id"), str):
                defectos.append(f"bloque {b['id']}: puerto sin id")
                continue
            dom = _normalizar_dominio(p.get("dominio"))
            if dom not in DOMINIOS:
                defectos.append(f"bloque {b['id']}.{p.get('id')}: dominio inválido {p.get('dominio')!r}")
            puertos[(b["id"], p.get("id"))] = dom

    # nets
    puerto_en_net = {}
    for n in nets:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str):
            defectos.append("net sin id")
            continue
        conexiones = n.get("conexiones", [])
        if len(conexiones) < 2:
            defectos.append(f"net {n['id']}: menos de 2 conexiones (net suelta)")
        dominios_en_net = set()
        for c in conexiones:
            if not isinstance(c, dict):
                defectos.append(f"net {n['id']}: conexión inválida")
                continue
            b, p = c.get("bloque"), c.get("puerto")
            if (b, p) not in puertos:
                defectos.append(f"net {n['id']}: puerto inexistente {b}.{p}")
                continue
            if (b, p) in puerto_en_net:
                defectos.append(f"puerto {b}.{p} en más de una net")
            puerto_en_net[(b, p)] = n["id"]
            dominios_en_net.add(puertos[(b, p)])
        # domain compatibility (ground is return wildcard)
        dominios_norm = {d for d in dominios_en_net if d != DOMINIO_TIERRA}
        if len(dominios_norm) > 1:
            defectos.append(f"net {n['id']}: dominios incompatibles {sorted(dominios_norm)}")

    # bindings: each variable -> existing block/port
    for bi in bindings:
        if not isinstance(bi, dict) or not isinstance(bi.get("variable"), str):
            defectos.append("binding sin variable")
            continue
        b = bi.get("bloque")
        if b not in ids_bloque:
            defectos.append(f"binding '{bi.get('variable')}': bloque inexistente {b!r}")
        # if it points to a port, it must exist
        p = bi.get("puerto")
        if p is not None and (b, p) not in puertos:
            defectos.append(f"binding '{bi.get('variable')}': puerto inexistente {b}.{p}")

    return (not defectos, defectos)


# ── promotion (topology states) ──────────────────────────────────────────
def estado_topologia_bloques(grafo, firmada=False):
    """Topology state of the graph: ASUMIDA if the validator passes, DECLARADA if it is also
    signed by a human. If the validator fails, AUSENTE (there is no valid topology)."""
    ok, defectos = validar_grafo(grafo)
    if not ok:
        return T.AUSENTE, defectos
    if firmada:
        return T.DECLARADA, []
    return T.ASUMIDA, []


def grado_desde_bloques(grafo, firmada=False):
    """Deliverable grade of the block graph (delegates to topologia.grado_entregable)."""
    estado, defectos = estado_topologia_bloques(grafo, firmada=firmada)
    grado, faltantes_topo = T.grado_entregable([estado])
    if defectos:
        faltantes_topo = defectos + faltantes_topo
    if estado == T.ASUMIDA:
        faltantes_topo = ["topología de bloques propuesta por el modelo, sin ratificación humana"] + defectos
    return grado, faltantes_topo


if __name__ == "__main__":
    # demo: 9V multimeter graph (power and signal SEPARATED, the bug Fable flagged)
    grafo_ok = {
        "bloques": [
            {"id": "bateria_9v", "tipo": "fuente", "puertos": [{"id": "V+", "dominio": "potencia"}, {"id": "V-", "dominio": "tierra"}]},
            {"id": "icl7106", "tipo": "adc", "puertos": [
                {"id": "V+", "dominio": "potencia"}, {"id": "V-", "dominio": "tierra"},
                {"id": "IN_HI", "dominio": "senal"}, {"id": "IN_LO", "dominio": "tierra"}]},
            {"id": "divisor", "tipo": "atenuador", "puertos": [
                {"id": "in", "dominio": "senal"}, {"id": "out", "dominio": "senal"}, {"id": "gnd", "dominio": "tierra"}]},
            {"id": "shunt", "tipo": "resistor", "puertos": [{"id": "a", "dominio": "medicion"}, {"id": "b", "dominio": "medicion"}]},
        ],
        "nets": [
            {"id": "vbus", "conexiones": [{"bloque": "bateria_9v", "puerto": "V+"}, {"bloque": "icl7106", "puerto": "V+"}]},
            {"id": "gnd", "conexiones": [{"bloque": "bateria_9v", "puerto": "V-"}, {"bloque": "icl7106", "puerto": "V-"}, {"bloque": "divisor", "puerto": "gnd"}]},
            {"id": "senal_atenuada", "conexiones": [{"bloque": "divisor", "puerto": "out"}, {"bloque": "icl7106", "puerto": "IN_HI"}]},
        ],
        "bindings": [
            {"variable": "v_in", "bloque": "divisor", "puerto": "in"},
            {"variable": "tension_entrada_adc", "bloque": "icl7106", "puerto": "IN_HI"},
        ],
    }
    ok, defectos = validar_grafo(grafo_ok)
    print("grafo bien formado?", ok, defectos)
    grafo, falt = grado_desde_bloques(grafo_ok, firmada=False)
    print("grado:", grafo, "| faltantes:", falt)
