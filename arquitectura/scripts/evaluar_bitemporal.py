#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Bitemporal baseline (Zep/Graphiti temporal model) vs HCMA.

Graphiti (Zep) models facts with `valid_at`/`valid_until` (bitemporal) but NO derivation
lineage: invalidating a fact does not invalidate its derivatives. It requires Neo4j + LLM to run
the full system; here we simulate its temporal MODEL (valid_from/valid_until) in a
deterministic and fair way, to measure exactly the temporal claim.

Expected result: bitemporal = HCMA in ATOMIC drift (both stale-use=0), but
bitemporal < HCMA in drift with DERIVATIONS (it cannot invalidate transitively).
"""
import os, sys, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evaluar_backend_stale import BackendHCMA, evaluar as evaluar_derivado, ESCENARIOS

HERE = os.path.dirname(os.path.abspath(__file__))


class BackendBitemporal:
    """Graphiti temporal model: facts with valid_from/valid_until, NO lineage."""
    def __init__(self):
        self.facts = {}
        self._t = 0

    def escribir(self, key, value, depende_de=None):
        self._t += 1
        cur = self.facts.get(key)
        if cur is not None and cur["valid_until"] is None:
            cur["valid_until"] = self._t
        self.facts[key] = {"value": value, "valid_from": self._t, "valid_until": None}

    def corregir(self, key, value):
        self.escribir(key, value)

    def leer(self, key):
        f = self.facts.get(key)
        if f is None or f["valid_until"] is not None:
            return None
        return f["value"]

    def version(self, key):
        f = self.facts.get(key)
        return f["valid_from"] if f else None

    def nombre(self):
        return "Bitemporal (Graphiti-style)"


def evaluar_atomico(backend, hechos):
    """Atomic drift: a fact changes value; does read return the current one?."""
    n = stale = 0
    for f in hechos:
        key = f"{f['propiedad']} de {f['entidad']}"
        backend.escribir(key, f["viejo"])
        backend.escribir(key, f["nuevo"])          # new version supersedes the old one
        leido = backend.leer(key)
        n += 1
        if leido != f["nuevo"]:
            stale += 1
    return stale / n


def main():
    hechos = json.load(open(os.path.join(HERE, "..", "resultados", "drift_real.json"),
                            encoding="utf-8"))

    print("=" * 70)
    print("Comparación temporal: bitemporal (Graphiti) vs HCMA")
    print("=" * 70)

    # 1) ATOMIC drift (facts that changed, no derivations)
    bit = BackendBitemporal()
    hcma = BackendHCMA()
    s_bit = evaluar_atomico(bit, hechos)
    s_hcma = evaluar_atomico(hcma, hechos)
    print(f"\n[Drift ATÓMICO — {len(hechos)} hechos reales de Wikidata]")
    print(f"  Bitemporal (Graphiti-style): stale-use = {s_bit:.2f}")
    print(f"  HCMA (version+authority):    stale-use = {s_hcma:.2f}")

    # 2) drift with DERIVATIONS (deterministic: inputs -> derivatives -> correction)
    r_bit = evaluar_derivado(BackendBitemporal(), ESCENARIOS)
    r_hcma = evaluar_derivado(BackendHCMA(), ESCENARIOS)
    print(f"\n[Drift con DERIVACIONES — {ESCENARIOS[0]['correccion'][0]} corregido]")
    print(f"  Bitemporal (sin linaje): stale-use = {r_bit['stale_use']:.2f}")
    print(f"  HCMA (con linaje):       stale-use = {r_hcma['stale_use']:.2f}")

    print("\n" + "=" * 70)
    print("Conclusión: el bitemporal (Graphiti) resuelve drift ATÓMICO pero NO el de")
    print("derivaciones (no tiene linaje). HCMA agrega invalidación transitiva por linaje")
    print("exacto y resuelve ambos. La diferencia es el linaje, no la temporalidad.")
    print("=" * 70)


if __name__ == "__main__":
    main()
