#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""baseline_asociativo.py — minimal associative memory (baseline of experiment D).

Unlike HCMA, this memory has NO lineage, NOR verification, NOR invalidation by
dependency. It models what the HeLa-Mem / Mem0 / A-MEM family does: an association graph
where the strength of each edge is REINFORCED by co-activation (simple Hebbian), and
retrieval is done by association strength, with no notion of 'source' or 'truth'.

It is the CONTRAST to show that HCMA's difference is GOVERNANCE, not retrieval.
It is NOT part of the system; it is the baseline the experiment must beat.
"""


class MemoriaAsociativa:
    """Fact graph with Hebbian reinforcement by co-activation. NO lineage or verification."""

    def __init__(self):
        self.hechos = {}      # key -> value
        self.aristas = {}     # (a, b) -> strength
        self.uso = {}         # key -> usage count

    def escribir(self, key, valor):
        self.hechos[key] = valor

    def co_activar(self, a, b):
        """Reinforces the edge (a,b) by co-activation (Hebbian: 'fire together, wire together')."""
        if a in self.hechos and b in self.hechos:
            self.aristas[(a, b)] = self.aristas.get((a, b), 0.0) + 1.0

    def recuperar(self, clave, top_k=5):
        """Retrieves neighbors by association strength (without verification)."""
        vecinos = []
        for (a, b), fuerza in self.aristas.items():
            if a == clave:
                vecinos.append((b, fuerza))
            elif b == clave:
                vecinos.append((a, fuerza))
        vecinos.sort(key=lambda x: -x[1])
        return [v[0] for v in vecinos[:top_k]]

    def usar(self, key):
        self.uso[key] = self.uso.get(key, 0) + 1
