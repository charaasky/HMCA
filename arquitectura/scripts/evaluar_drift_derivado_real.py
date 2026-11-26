#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Real DERIVED drift: population → density, with Wikidata data.

Closes the gap: the previous real drift was atomic. Here we derive density (= population /
area) and measure the transitive invalidation when the population (input) changes, comparing
HCMA (lineage) vs bitemporal (Graphiti-style, no lineage).
"""
import os, sys, json, urllib.request, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evaluar_bitemporal import BackendBitemporal
from evaluar_backend_stale import BackendHCMA

SPARQL = "https://query.wikidata.org/sparql"
UA = "HCMA-research/0.1 (contact: mauro@example.com)"


def _sparql(query):
    url = SPARQL + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())


def _datos_paises():
    q = (
        "SELECT ?countryLabel ?pop ?date ?area WHERE {\n"
        "  VALUES ?country { wd:Q414 wd:Q30 wd:Q145 wd:Q155 wd:Q142 wd:Q183 }.\n"
        "  ?country p:P1082 ?pstmt.\n"
        "  ?pstmt ps:P1082 ?pop.\n"
        "  ?pstmt pq:P585 ?date.\n"
        "  OPTIONAL { ?country wdt:P2046 ?area. }\n"
        '  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }\n'
        "}\nORDER BY ?countryLabel DESC(?date)"
    )
    d = _sparql(q)
    by = {}
    for b in d["results"]["bindings"]:
        name = b["countryLabel"]["value"]
        pop = float(b["pop"]["value"])
        area = b.get("area", {}).get("value")
        by.setdefault(name, {"pops": [], "area": area})
        by[name]["pops"].append(pop)
    out = []
    for name, v in by.items():
        if v["area"] and len(v["pops"]) >= 2:
            out.append({"entidad": name, "pop_old": v["pops"][1],
                        "pop_new": v["pops"][0], "area": float(v["area"])})
    return out


def evaluar(backend, paises):
    stale = 0
    recomputar = 0
    for p in paises:
        backend.escribir("poblacion", p["pop_old"])
        backend.escribir("densidad", p["pop_old"] / p["area"],
                         depende_de={"poblacion": backend.version("poblacion")})
        backend.corregir("poblacion", p["pop_new"])
        dens_new = p["pop_new"] / p["area"]
        leido = backend.leer("densidad")
        if leido is None:
            recomputar += 1          # detected invalidity (correct)
        elif abs(leido - dens_new) > 1e-9:
            stale += 1               # returned the old value
    n = len(paises)
    return stale / n, recomputar / n


def main():
    paises = _datos_paises()
    print(f"Drift derivado REAL: {len(paises)} países, densidad = población / área")
    for p in paises:
        print(f"  · {p['entidad']}: {p['pop_old']:.0f} → {p['pop_new']:.0f} hab "
              f"(área {p['area']:.0f} km²)")

    s_bit, r_bit = evaluar(BackendBitemporal(), paises)
    s_hcma, r_hcma = evaluar(BackendHCMA(), paises)
    print("\n" + "=" * 66)
    print(f"  Bitemporal (sin linaje): stale-use = {s_bit:.2f} | recomputa = {r_bit:.2f}")
    print(f"  HCMA (con linaje):       stale-use = {s_hcma:.2f} | recomputa = {r_hcma:.2f}")
    print("=" * 66)
    print("Conclusión: sobre datos reales con DERIVACIÓN, el bitemporal no invalida el")
    print("derivado (densidad) al corregir la población; HCMA sí, por linaje exacto.")


if __name__ == "__main__":
    main()
