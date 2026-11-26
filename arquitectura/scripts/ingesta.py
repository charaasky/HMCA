#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""First knowledge ingestion slice: discover → fetch → parse → extract.

Demonstrates the mechanism with a real web document:
  1. discover  → real web search (OpenRouter) to find an engineering PDF.
  2. fetch     → download the PDF.
  3. parse     → extract text + tables (pymupdf) and detect equations (heuristic).
  4. extract   → the LLM converts a fragment into structured facts (formulas/tables/facts).
"""
import json, os, re, sys, socket, ipaddress
from urllib.request import Request, urlopen
from urllib.parse import urlparse

import fitz  # pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prototipo import busqueda_web, call_llm, parse_json, MODEL

HERE = os.path.dirname(os.path.abspath(__file__))

SYS_EXTRACTOR = (
    "Eres un extractor de conocimiento de ingeniería. Dado un fragmento de un documento "
    "técnico, extraé SOLO JSON (json_object) con:\n"
    '- "formulas": lista de {"expresion", "variables", "contexto"} (ecuaciones del texto)\n'
    '- "tablas": lista de {"nombre", "columnas", "nota"}\n'
    '- "hechos": objeto {clave: valor} (propiedades, constantes, coeficientes, normas citadas)\n'
    '- "limitacion": string (qué NO se pudo extraer y por qué)\n'
    "Reglas: sé fiel al texto; NO inventes números ni fórmulas que no estén explícitas. "
    "Si el fragmento no tiene fórmulas, devolvé formulas=[] y explicalo en limitacion."
)


def descubrir(query):
    r = busqueda_web(query)
    urls = [f["url"] for f in r.get("fuentes", [])]
    return urls, r.get("hechos", {})


MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB cap


def _host_privado(url):
    """True if the host resolves to private/loopback/link-local/reserved IP (E6: SSRF)."""
    host = urlparse(url).hostname
    if not host:
        return True
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(host))
    except Exception:
        return True
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def fetch_pdf(url, destino):
    """Downloads a PDF with security restrictions: only http/https, non-private host, size cap
    and %PDF- magic bytes verification before writing (E1/E6)."""
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError(f"esquema no permitido: {urlparse(url).scheme}")
    if _host_privado(url):
        raise ValueError("host privado/reservado no permitido (SSRF)")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
    with urlopen(req, timeout=120) as resp:
        cl = resp.headers.get("Content-Length")
        if cl and int(cl) > MAX_PDF_BYTES:
            raise ValueError("PDF demasiado grande (Content-Length)")
        data = resp.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("PDF demasiado grande")
    if not data.startswith(b"%PDF-"):
        raise ValueError("no es un PDF (magic bytes)")
    with open(destino, "wb") as f:
        f.write(data)
    return destino


def parse_pdf(path):
    doc = fitz.open(path)
    texto, tablas = [], []
    for page in doc:
        texto.append(page.get_text())
        for t in page.find_tables():
            tablas.append(t.extract())
    return {"paginas": doc.page_count, "texto": "\n".join(texto),
            "tablas": tablas, "n_tablas": len(tablas)}


def detectar_ecuaciones(texto):
    """Cheap heuristic: count lines with typical formula signs."""
    lineas = [l for l in texto.splitlines() if re.search(r"[=×÷√∑∫±·]|[α-ωΑ-Ω]|/(?![/])", l)]
    return len(lineas)


def extraer(fragmento):
    return parse_json(call_llm(f"Fragmento:\n\n{fragmento}", MODEL, system=SYS_EXTRACTOR))


def main():
    query = os.environ.get("INGESTA_QUERY",
                           "tesis de ingeniería civil cálculo de vigas de acero filetype:pdf")
    print(f"1) Descubriendo: {query}")
    urls, hechos = descubrir(query)
    print(f"   URLs encontradas: {len(urls)}")
    for u in urls[:5]:
        print(f"     - {u}")

    destino = os.path.join(HERE, "documento_ingenieria.pdf")
    ok = False
    for u in urls:
        try:
            fetch_pdf(u, destino)
            with open(destino, "rb") as f:
                head = f.read(5)
            if head == b"%PDF-":
                print(f"2) PDF descargado: {u}")
                ok = True
                break
            print(f"   (no es PDF): {u}")
        except Exception as e:
            print(f"   (falló {u}): {e}")
    if not ok:
        print("   ⚠️ No se pudo descargar un PDF directo desde los resultados.")
        return

    print("3) Parseando...")
    p = parse_pdf(destino)
    n_eq = detectar_ecuaciones(p["texto"])
    print(f"   Páginas: {p['paginas']} | tablas detectadas: {p['n_tablas']} | "
          f"chars de texto: {len(p['texto'])} | líneas con signos de ecuación: {n_eq}")

    if p["tablas"]:
        print(f"   Primera tabla detectada: {p['tablas'][0][:2]}")

    print("4) Extracción estructurada (fragmento 6000 chars)...")
    frag = p["texto"][:6000]
    out = extraer(frag)
    print(json.dumps(out, ensure_ascii=False, indent=2)[:3500])


if __name__ == "__main__":
    main()
