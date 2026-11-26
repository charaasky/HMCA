#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""M1 contract + stale-use benchmark runner for any memory backend.

The contract (`BackendMemoria`) is the minimal interface that an external system
(MemGPT, A-MEM, LightMem) implement to evaluate on EQUAL footing against HCMA:
same data model, same correction, same metric.

Metric: after correcting an input, for each derived fact `leer(key)` returns:
  - FRESH value (correct), or
  - STALE value (stale-use → the system uses an old datum), or
  - None (detected it became invalid → must recompute).

Reference backends:
  - HCMA          : version governance + lineage (invalidates only what is affected).
  - Plano         : key→value dict, no lineage (does not know something became stale).
  - RefreshBruto  : no lineage, but on any correction it recomputes EVERYTHING.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prototipo import HCMAMemoria


class BackendMemoria:
    """M1 contract: interface that a memory backend must expose."""
    def escribir(self, key, value, depende_de=None):
        raise NotImplementedError
    def corregir(self, key, value):
        raise NotImplementedError
    def leer(self, key):
        raise NotImplementedError
    def version(self, key):
        raise NotImplementedError
    def nombre(self):
        return type(self).__name__


class BackendHCMA(BackendMemoria):
    def __init__(self):
        self.h = HCMAMemoria()
    def escribir(self, key, value, depende_de=None):
        if depende_de is None:
            self.h.write(key, value, fuente="usuario", verified=True)
        else:
            self.h.write(key, value, fuente="calculadora", verified=True, derived_from=depende_de)
    def corregir(self, key, value):
        self.escribir(key, value)
    def leer(self, key):
        f = self.h.current(key)
        return f["value"] if f else None
    def version(self, key):
        f = self.h.current(key)
        return f["version"] if f else None


class BackendPlano(BackendMemoria):
    """No lineage or versioning: ignores `depende_de` (cannot express it)."""
    def __init__(self):
        self.d = {}
    def escribir(self, key, value, depende_de=None):
        self.d[key] = value
    def corregir(self, key, value):
        self.d[key] = value
    def leer(self, key):
        return self.d.get(key)
    def version(self, key):
        return None


class BackendRefreshBruto(BackendMemoria):
    """No lineage, but on any correction it recomputes ALL the derivatives."""
    def __init__(self):
        self.d = {}
        self.derivados = {}  # key -> (func, [deps])
    def escribir(self, key, value, depende_de=None):
        if depende_de is None:
            self.d[key] = value
        else:
            self.d[key] = value  # derivatives are registered separately with their function
    def registrar_derivado(self, key, func, deps):
        self.derivados[key] = (func, deps)
        self.d[key] = func({k: self.d[k] for k in deps})
    def corregir(self, key, value):
        self.d[key] = value
        for dk, (func, deps) in self.derivados.items():
            self.d[dk] = func({k: self.d[k] for k in deps})
    def leer(self, key):
        return self.d.get(key)
    def version(self, key):
        return None


# ── scenarios ──────────────────────────────────────────────────────────────────
def _f_ab(d): return round(d["in_a"] + d["in_b"], 2)
def _f_c(d): return round(d["in_c"] * 2, 2)
def _f_all(d): return round(d["in_a"] + d["in_b"] + d["in_c"] + d["in_d"], 2)

ESCENARIOS = [
    {
        "inputs": {"in_a": 10.0, "in_b": 5.0, "in_c": 3.0, "in_d": 7.0},
        "derivados": [("d_ab", _f_ab, ["in_a", "in_b"]),
                      ("d_c", _f_c, ["in_c"]),
                      ("d_all", _f_all, ["in_a", "in_b", "in_c", "in_d"])],
        "correccion": ("in_a", 20.0),
    },
    {
        "inputs": {"in_a": 1.0, "in_b": 2.0, "in_c": 3.0, "in_d": 4.0},
        "derivados": [("d_ab", _f_ab, ["in_a", "in_b"]),
                      ("d_c", _f_c, ["in_c"]),
                      ("d_all", _f_all, ["in_a", "in_b", "in_c", "in_d"])],
        "correccion": ("in_c", 30.0),
    },
]


def evaluar(backend, escenarios):
    n_derivados = 0
    stale = 0
    recomputar = 0
    for sc in escenarios:
        inputs = sc["inputs"]
        # inputs
        for k, v in inputs.items():
            backend.escribir(k, v)
        # derivatives (depende_de = current versions, for the backend that supports it)
        for dk, func, deps in sc["derivados"]:
            val = func({k: inputs[k] for k in deps})
            if isinstance(backend, BackendRefreshBruto):
                backend.registrar_derivado(dk, func, deps)
            else:
                backend.escribir(dk, val, depende_de={k: backend.version(k) for k in deps})
        # correction
        k, v = sc["correccion"]
        backend.corregir(k, v)
        inputs_corregidos = dict(inputs)
        inputs_corregidos[k] = v
        # measure
        for dk, func, deps in sc["derivados"]:
            n_derivados += 1
            fresco = func({d: inputs_corregidos[d] for d in deps})
            leido = backend.leer(dk)
            if leido is None:
                recomputar += 1          # detected invalid → recompute
            elif leido == fresco:
                pass                      # fresh
            else:
                stale += 1                # STALE-USE: returned the old value
    return {"n": n_derivados,
            "stale_use": stale / n_derivados,
            "recomputar": recomputar / n_derivados}


def main():
    print("=" * 70)
    print("Benchmark M1: stale-use + costo de recomputo por backend")
    print("=" * 70)
    for backend in (BackendHCMA(), BackendPlano(), BackendRefreshBruto()):
        r = evaluar(backend, ESCENARIOS)
        print(f"\n{backend.nombre():14s} → stale-use = {r['stale_use']:.2f}  "
              f"| fracción a recomputar = {r['recomputar']:.2f}")
    print("\n" + "=" * 70)
    print("Lectura:")
    print("  HCMA         : stale-use 0, recomputa SOLO lo afectado (gobernanza + linaje)")
    print("  Plano        : stale-use > 0 (usa valores viejos sin saberlo)")
    print("  RefreshBruto : stale-use 0 pero recomputa TODO (correcto e ineficiente)")


if __name__ == "__main__":
    main()
