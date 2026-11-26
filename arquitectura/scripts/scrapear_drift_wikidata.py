#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Scrapes REAL temporal drift, 100% automatic, from Wikidata.

For each office (position) it searches its QID and queries SPARQL to obtain the holders
ordered by start date. The first is the current one; the second is the predecessor.
Both the old value and the new one and the timestamp come from Wikidata (nothing curated).
"""
import os, json, time, urllib.request, urllib.parse

UA = "HCMA-research/0.1 (contact: mauro@example.com)"
API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"


def _search(office):
    params = {"action": "wbsearchentities", "search": office, "language": "en",
              "type": "item", "format": "json"}
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    d = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    return d.get("search", [{}])[0].get("id")


def _officeholders(office_qid, limit=2):
    q = (
        "SELECT ?holderLabel ?start WHERE {\n"
        f"  ?holder p:P39 ?stmt.\n"
        f"  ?stmt ps:P39 wd:{office_qid}.\n"
        "  OPTIONAL { ?stmt pq:P580 ?start. }\n"
        '  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }\n'
        "}\nORDER BY DESC(?start)\nLIMIT " + str(limit)
    )
    url = SPARQL + "?" + urllib.parse.urlencode({"query": q, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    d = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    return [(b.get("holderLabel", {}).get("value"),
             b.get("start", {}).get("value")) for b in d["results"]["bindings"]]


# (entity for the query, office for SPARQL, property)
OFICIOS = [
    ("Argentina", "President of Argentina", "head of state"),
    ("United Kingdom", "Monarch of the United Kingdom", "monarch"),
    ("Denmark", "Monarch of Denmark", "monarch"),
    ("United States", "President of the United States", "president"),
    ("Brazil", "President of Brazil", "president"),
    ("France", "President of France", "president"),
    ("Germany", "Chancellor of Germany", "chancellor"),
    ("United Kingdom", "Prime Minister of the United Kingdom", "prime minister"),
    ("Mexico", "President of Mexico", "president"),
    ("Chile", "President of Chile", "president"),
    ("India", "President of India", "president"),
    ("Italy", "President of Italy", "president"),
]


def main():
    out = []
    for entidad, office, prop in OFICIOS:
        qid = _search(office)
        if not qid:
            print(f"  ✗ {office}: cargo no encontrado")
            continue
        holders = _officeholders(qid, limit=2)
        if len(holders) < 2:
            print(f"  ✗ {office}: sin predecesor")
            continue
        (nuevo, start_nuevo), (viejo, _) = holders[0], holders[1]
        cambio = start_nuevo[:4] if start_nuevo else "?"
        out.append({"entidad": entidad, "propiedad": prop, "viejo": viejo,
                    "nuevo": nuevo, "cambio": cambio, "timestamp": start_nuevo})
        print(f"  ✓ {prop} de {entidad}: «{viejo}» → «{nuevo}» ({cambio})")
        time.sleep(0.3)

    destino = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "resultados", "drift_real.json")
    json.dump(out, open(destino, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n{len(out)} hechos 100% automáticos (Wikidata, viejo+nuevo+timestamp) → {destino}")


if __name__ == "__main__":
    main()
