#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Ingestion of books with free/open licenses → versioned knowledge.

Flow: download → parse (PDF or OCR text) → chunk → extract facts/formulas/
tables with the LLM → consolidate into a persistent store with provenance + hash.

HCMA principle: ingested knowledge enters as UNVERIFIED (candidate). It is not
self-certified: the engineer validates it or a trusted source promotes it to verified.
Each downloaded artifact remains linked to its license and its SHA-256, so that the
corpus is auditable, redistributable, and "deletable by provenance" (if a source is
withdrawn, all of its facts can be invalidated by origin lineage).

Usage:
  python3 ingesta_libros.py                 # ingests the first item in the catalog
  python3 ingesta_libros.py --list          # shows the catalog
  INGESTA_ID=... INGESTA_MAX_CHUNKS=5 python3 ingesta_libros.py
"""
import json, os, re, sys, hashlib, time, argparse
from urllib.request import Request, urlopen
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from prototipo import (load_key, call_llm, parse_json, get_model_max_tokens,
                       report_cost, HCMAMemoria, MODEL, _es_metadata)
import ingesta  # reuse: _host_privado (SSRF), SYS_EXTRACTOR, fetch_pdf

STORE_DIR = os.path.join(HERE, "..", "resultados", "conocimiento")

# ═══════════════════════════════════════════════════════════════════════════════
# Catalog of legitimate sources (clear license, redistributable).
# Adding a book = adding an item with its url and license. Nothing else changes.
# ═══════════════════════════════════════════════════════════════════════════════
CATALOGO = [
    {
        "id": "kottkamp-strength-materials-1919",
        "titulo": "Strength of materials: a text book for technical and industrial schools",
        "anio": 1919,
        "licencia": "Public Domain (EE.UU., obra anterior a 1928)",
        "fuente": "Internet Archive",
        "idioma": "en",
        "pdf_url": "https://archive.org/download/strengthofmateri00kottrich/strengthofmateri00kottrich.pdf",
        "text_url": "https://archive.org/download/strengthofmateri00kottrich/strengthofmateri00kottrich_djvu.txt",
    },
    {
        "id": "bovey-theory-structures-1893",
        "titulo": "Theory of structures and strength of materials",
        "autor": "Henry Taylor Bovey",
        "anio": 1893,
        "licencia": "Public Domain (EE.UU.)",
        "fuente": "Internet Archive",
        "idioma": "en",
        "pdf_url": "https://archive.org/download/theorystructure03bovegoog/theorystructure03bovegoog.pdf",
        "text_url": "https://archive.org/download/theorystructure03bovegoog/theorystructure03bovegoog_djvu.txt",
    },
    {
        "id": "hancock-applied-mechanics-1909",
        "titulo": "Applied Mechanics for Engineers: A Text-book for Engineering Students",
        "autor": "Edward Lee Hancock",
        "anio": 1909,
        "licencia": "Public Domain (EE.UU.)",
        "fuente": "Internet Archive",
        "idioma": "en",
        "pdf_url": "https://archive.org/download/appliedmechanic00hancgoog/appliedmechanic00hancgoog.pdf",
        "text_url": "https://archive.org/download/appliedmechanic00hancgoog/appliedmechanic00hancgoog_djvu.txt",
    },
    {
        "id": "ats-cyclopedia-applied-electricity-1916",
        "titulo": "Cyclopedia of applied electricity (vol. 2)",
        "autor": "American Technical Society",
        "anio": 1916,
        "licencia": "Public Domain (EE.UU., serie 1912–1916)",
        "fuente": "Internet Archive",
        "idioma": "en",
        "pdf_url": "https://archive.org/download/cyclopediaofappl02amer/cyclopediaofappl02amer.pdf",
        "text_url": "https://archive.org/download/cyclopediaofappl02amer/cyclopediaofappl02amer_djvu.txt",
    },
    {
        "id": "mechanics-map-moore",
        "titulo": "Mechanics Map (Moore et al.) — statics & dynamics",
        "anio": 2021,
        "licencia": "CC BY-NC-SA 4.0",
        "fuente": "LibreTexts Engineering",
        "idioma": "en",
        "pdf_url": "https://eng.libretexts.org/@api/deki/pages/50565/pdf/Mechanics%2bMap%2b(Moore%2bet%2bal.).pdf",
        "text_url": None,
        # LibreTexts' direct export returns only the cover page (6 pages); to
        # ingest the full book, the per-chapter URLs must be resolved (pending).
        "nota": "PDF directo = portada parcial; requiere URLs por capítulo.",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Core (does not change per book)
# ═══════════════════════════════════════════════════════════════════════════════

def _descargar(url, destino, max_bytes=80 * 1024 * 1024):
    """Secure download (E1/E6): http/https only, non-private host, size cap."""
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError(f"esquema no permitido: {urlparse(url).scheme}")
    if ingesta._host_privado(url):
        raise ValueError("host privado/reservado no permitido (SSRF)")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
    with urlopen(req, timeout=180) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("artefacto demasiado grande")
    with open(destino, "wb") as f:
        f.write(data)
    return destino


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _texto_desde_pdf(path):
    import fitz  # pymupdf
    doc = fitz.open(path)
    partes = []
    for page in doc:
        partes.append(page.get_text())
    return {"paginas": doc.page_count, "texto": "\n".join(partes)}


def _texto_desde_txt(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        t = f.read()
    return {"paginas": None, "texto": t}


def trocear(texto, chunk=6000, solape=400):
    """Splits into fragments of ~`chunk` chars with `solape` overlap.

    Robust against regions with many line breaks (stamp pages, tables):
    the advance between fragments is always >= `chunk - solape`, so it never generates
    tiny fragments nor loops (the previous version did, in bursts of '\n')."""
    n = len(texto)
    if n <= chunk:
        return [texto]
    paso = chunk - solape
    chunks = []
    inicio = 0
    while inicio < n:
        fin = min(inicio + chunk, n)
        if fin < n:
            # cut at the last '\n' in the window, but never before
            # `inicio + paso` (guarantees minimum advance and non-tiny chunks).
            corte = texto.rfind("\n", inicio + paso, fin)
            if corte != -1:
                fin = corte + 1
        chunks.append(texto[inicio:fin])
        if fin >= n:
            break
        inicio = fin - solape
    return chunks


def extraer_fragmento(frag, procedencia):
    """Extracts facts/formulas/tables from a fragment. Returns a JSON dict."""
    prompt = (f"Procedencia: {procedencia}\n\n"
              f"Fragmento:\n\n{frag}")
    max_t = get_model_max_tokens(MODEL, default=8000)
    return parse_json(call_llm(prompt, MODEL, system=ingesta.SYS_EXTRACTOR,
                               max_tokens=max_t))


def _procedencia(item, sha, texto, parsed, n_done, total, skip=0):
    return {
        "id": item["id"], "titulo": item["titulo"],
        "autor": item.get("autor", "Desconocido"), "anio": item["anio"],
        "licencia": item["licencia"], "fuente": item["fuente"],
        "idioma": item["idioma"], "url": item.get("pdf_url") or item.get("text_url"),
        "sha256_artefacto": sha, "chars": len(texto),
        "paginas": parsed.get("paginas"),
        "chunks_procesados": n_done, "chunks_total": total, "skip": skip,
        "fecha_ingesta": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _merge_uno(store, e, idx):
    """Merges one extraction into the store, annotating provenance (chunk) per fact."""
    if not isinstance(e, dict):
        return
    for k, v in (e.get("hechos") or {}).items():
        destino = "metadata" if _es_metadata(k) else "hechos"
        store[destino].append({"clave": k, "valor": v, "chunk": idx})
    for f in (e.get("formulas") or []):
        if isinstance(f, dict):
            f = dict(f); f["chunk"] = idx
            store["formulas"].append(f)
    for t in (e.get("tablas") or []):
        if isinstance(t, dict):
            t = dict(t); t["chunk"] = idx
            store["tablas"].append(t)
    if e.get("limitacion"):
        store["limitaciones"].append({"chunk": idx, "nota": e["limitacion"]})


def cargar_en_hcma(store):
    """Loads the extracted knowledge into HCMA as UNVERIFIED (source 'libro').

    'libro' is NOT in FUENTE_CONFIABLE → all enter as candidates. The engineer
    can promote them with `write(..., fuente='tabla', verified=True)` after validating them.
    """
    h = HCMAMemoria()
    for hh in store["hechos"]:
        meta = {"origen": store["procedencia"]["id"], "chunk": hh.get("chunk")}
        h.write(hh["clave"], hh["valor"], unit="", fuente="libro", verified=False,
                meta=meta)
    return h


# ═══════════════════════════════════════════════════════════════════════════════

def ingerir(item, max_chunks=None):
    os.makedirs(STORE_DIR, exist_ok=True)
    tmp_dir = os.path.join(STORE_DIR, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    out_json = os.path.join(STORE_DIR, item["id"] + ".json")

    # 1) download (with cache): prefer OCR text, else PDF.
    if item.get("text_url"):
        artefacto = os.path.join(tmp_dir, item["id"] + ".txt")
        if not os.path.exists(artefacto) or os.path.getsize(artefacto) == 0:
            _descargar(item["text_url"], artefacto)
        parsed = _texto_desde_txt(artefacto)
    else:
        artefacto = os.path.join(tmp_dir, item["id"] + ".pdf")
        if not os.path.exists(artefacto) or os.path.getsize(artefacto) == 0:
            ingesta.fetch_pdf(item["pdf_url"], artefacto)
        parsed = _texto_desde_pdf(artefacto)

    sha = _sha256(artefacto)
    texto = parsed["texto"].strip()
    print(f"    artefacto: {os.path.basename(artefacto)} "
          f"({len(texto)} chars, SHA-256 {sha[:16]}…)")

    chunks = trocear(texto)
    skip = int(os.environ.get("INGESTA_SKIP_CHUNKS", "0") or 0)
    if skip:
        chunks = chunks[skip:]
    if max_chunks:
        chunks = chunks[:max_chunks]
    total = len(chunks)
    print(f"    fragmentos a procesar: {total} (skip={skip})")

    # 2) extract incrementally: save after each fragment so progress is not lost
    #    if the run is interrupted (call_llm's cache makes the re-run cheap).
    store = {"hechos": [], "metadata": [], "formulas": [], "tablas": [], "limitaciones": []}
    for i, c in enumerate(chunks):
        print(f"    → fragmento {i + 1}/{total} ({len(c)} chars)…", flush=True)
        try:
            e = extraer_fragmento(
                c, f"{item['titulo']} · {item['fuente']} · {item['licencia']}")
        except Exception as ex:
            print(f"       (falló: {ex})")
            e = {"limitacion": f"fallo: {ex}"}
        _merge_uno(store, e, i + skip)
        store["procedencia"] = _procedencia(item, sha, texto, parsed, i + 1, total, skip)
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)

    store["procedencia"] = _procedencia(item, sha, texto, parsed, total, total, skip)

    # 3) per-book manifest (integrity + license)
    manifest = {
        "id": item["id"], "titulo": item["titulo"], "licencia": item["licencia"],
        "fuente": item["fuente"], "sha256_artefacto": sha,
        "archivo_store": os.path.basename(out_json),
        "n_hechos": len(store["hechos"]), "n_formulas": len(store["formulas"]),
        "n_tablas": len(store["tablas"]),
    }
    with open(os.path.join(STORE_DIR, item["id"] + ".manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return store, out_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="listar catálogo")
    args = ap.parse_args()

    if args.list:
        for it in CATALOGO:
            print(f"  {it['id']:40s} {it['titulo'][:50]:50s} {it['licencia']}")
        return

    item = next((x for x in CATALOGO if x["id"] == os.environ.get("INGESTA_ID")),
                CATALOGO[0])
    max_chunks = int(os.environ.get("INGESTA_MAX_CHUNKS", "0")) or None

    print(f"Ingiriendo: {item['titulo']}\n"
          f"  licencia: {item['licencia']}\n  fuente:   {item['fuente']}\n"
          f"  idioma:   {item['idioma']}")

    store, out_json = ingerir(item, max_chunks)

    print(f"\nResultado: {os.path.relpath(out_json, HERE)}")
    print(f"  hechos: {len(store['hechos'])} | metadata: {len(store['metadata'])} | "
          f"fórmulas: {len(store['formulas'])} | tablas: {len(store['tablas'])} | "
          f"limitaciones: {len(store['limitaciones'])}")

    # show a sample of what was extracted
    for h in store["hechos"][:8]:
        print(f"    · {h['clave']} = {h['valor']}")
    for f in store["formulas"][:5]:
        print(f"    ≈ fórmula: {f.get('expresion', '')[:70]}")

    # 4) load into HCMA as candidates (not verified)
    h = cargar_en_hcma(store)
    pend = h.pending_unverified()
    print(f"\nHCMA: {len(h.facts)} claves cargadas como UNVERIFIED "
          f"({sum(len(v) for v in pend.values())} versiones candidatas). "
          f"Ninguna auto-certificada.")

    report_cost()


if __name__ == "__main__":
    main()
