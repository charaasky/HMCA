#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Formula extractor from PDF without having the source LaTeX.

Pipeline:
  1. detect  → ranks pages by density of math symbols in the text layer (cheap).
  2. regions  → locates the BOXES where there is math (deterministic, by glyph
                 geometry) and crops only those zones instead of sending the whole page.
                 This lowers the image tokens (cost) without losing quality.
  3. render    → PyMuPDF renders each region (or full page as fallback) to PNG.
  4. OCR       → a multimodal LLM (vision) transcribes the equations to LaTeX (the oracle).
  5. validate   → deduplicates and filters empty/invalid equations; returns page + context.

The crop (step 2) is optional and does NOT use any model: only the PDF text layer.
Usage:
  python3 extractor_formulas_pdf.py documento.pdf [--max-paginas N] [--dpi DPI] [--out formulas.json]
  python3 extractor_formulas_pdf.py documento.pdf --pagina-completa   # disables the crop
"""
import argparse, base64, json, os, re, sys

import fitz  # pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prototipo import call_vision, parse_json, report_cost

SYS_VISION = (
    "Sos un extractor de ECUACIONES de una página de un documento de ingeniería. Devolvé SOLO "
    "JSON (json_object) con:\n"
    '- "ecuaciones": lista de {"latex": "...", "contexto": "..."} (LaTeX, fiel a la imagen)\n'
    '- "nota": string breve\n'
    "Reglas:\n"
    "1. NO inventes ecuaciones que no estén en la imagen.\n"
    "2. SOLO devolvé ecuaciones matemáticas reales: con variables, fracciones, raíces, "
    "desigualdades, sumatorias, funciones o símbolos.\n"
    "3. DESCARTÁ filas de tablas, ítems de listas, números de página, encabezados y asignaciones "
    "triviales tipo 'ℓ = 7', 'x = 5' o '0 + 7 = 13'. Esos NO son ecuaciones.\n"
    "4. Si la página no tiene ecuaciones, devolvé ecuaciones=[]."
)

# Typical math symbols that appear (even as glyphs with degraded Unicode)
# in the text layer. They serve to rank pages AND to locate regions.
RE_MATEMATICA = re.compile(
    r"[=≠≈<≤>≥×÷±∓·⋅√∛∜∑∏∫∂∇∞∈∉⊂⊆∪∩∅∧∨⊕⊗⊥∥∠→⇒↔−⁄°"
    r"α-ωΑ-Ωℓ]"
    r"|\b(?:MPa|GPa|kPa|kN|MN|mm|cm|m²|mm²|cm²|m³|kg|kgf|Pa|Hz|°C)\b"
)

# Cropping parameters (in PDF points, 1pt = 1/72 inch).
PAD_REGION = 18.0        # margin around each math symbol
MERGE_MARGIN = 20.0      # maximum distance to merge nearby symbols into the same region
MAX_REGIONES_POR_PAGINA = 10  # if there are more regions, it is better to send the full page


def densidad_matematica(texto):
    """Cheap heuristic: count typical equation symbols in the text layer."""
    return len(RE_MATEMATICA.findall(texto))


def _es_fuente_math(nombre):
    """Detects math fonts by name (without 'symbol', which Word uses for bullets)."""
    nombre = (nombre or "").lower()
    return any(s in nombre for s in
               ("math", "cmsy", "cmmi", "mtextra", "stix", "xits", "latinmodern"))


def _tiene_fuente_math(page):
    """Detects math fonts even when their ToUnicode is broken (Cambria Math-Identity-H
    returns garbage/PUA in the text layer; the font signal is there)."""
    return any(_es_fuente_math(f[3]) for f in page.get_fonts())


def _ratio_pua(texto):
    if not texto:
        return 0.0
    return sum(1 for ch in texto if 0xE000 <= ord(ch) <= 0xF8FF or ch == "\ufffd") / len(texto)


def detectar_paginas(pdf, max_paginas=8):
    """Returns the indices (0-based) of the pages with the greatest math signal.

    Signal = symbol density + math font + proportion of PUA chars
    (Word/Cambria Math-Identity-H PDFs do not give Unicode but do give the font and the PUA).
    Only pages with signal > 0 are included (garbage is not sent to the OCR)."""
    doc = fitz.open(pdf)
    scored = []
    for i, p in enumerate(doc):
        texto = p.get_text()
        score = densidad_matematica(texto)
        if _tiene_fuente_math(p):
            score += 10
        if _ratio_pua(texto) > 0.02:
            score += 10
        if score > 0:
            scored.append((score, i))
    scored.sort(reverse=True)
    return [i for _, i in scored[:max_paginas]]


def detectar_regiones_matematicas(doc, page_index, pad=PAD_REGION, margin=MERGE_MARGIN):
    """Returns a list of fitz.Rect: boxes that enclose the math on the page.

    Deterministic and without an LLM. It uses the whole LINE containing a math symbol
    (not the word), so as not to cut the equation in half; then vertically close lines
    are grouped (multi-line fractions/radicals)."""
    page = doc[page_index]
    lineas = []
    for b in page.get_text("dict").get("blocks", []):
        if b.get("type") != 0:
            continue
        for l in b.get("lines", []):
            spans = l.get("spans", [])
            texto = "".join(s.get("text", "") for s in spans)
            # math if the line has symbols OR if it is PUA garbage (broken Identity-H)
            if RE_MATEMATICA.search(texto) or _ratio_pua(texto) > 0.2:
                lineas.append(fitz.Rect(l["bbox"]))
    if not lineas:
        return []

    rects = [fitz.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad) for r in lineas]
    # transitive union of nearby rectangles (union-find)
    n = len(rects)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            r, s = rects[i], rects[j]
            if (r.x0 <= s.x1 + margin and s.x0 <= r.x1 + margin and
                    r.y0 <= s.y1 + margin and s.y0 <= r.y1 + margin):
                union(i, j)

    grupos = {}
    for i in range(n):
        grupos.setdefault(find(i), []).append(rects[i])

    page_rect = page.rect
    out = []
    for g in grupos.values():
        r = fitz.Rect(min(x.x0 for x in g), min(x.y0 for x in g),
                      max(x.x1 for x in g), max(x.y1 for x in g)) & page_rect
        if not r.is_empty:
            out.append(r)
    # remove regions already contained in another (avoids sending duplicate pixels)
    out = [r for r in out if not any(s.contains(r) and s != r for s in out)]
    out.sort(key=lambda r: (r.y0, r.x0))
    return out


def _normalizar_latex(s):
    return re.sub(r"\s+", " ", str(s).strip())


def _es_ecuacion_valida(latex):
    """Post-hoc filter: discards pure arithmetic ('0+7=13') and trivial assignments
    ('ℓ=7' or '\\ell = 7')."""
    l = _normalizar_latex(latex)
    if not l:
        return False
    if not re.search(r"[a-zA-Zα-ωΑ-Ωℓ]", l):
        return False
    if re.fullmatch(r"(?:\\[a-zA-Z]+|[a-zA-Zα-ωΑ-Ωℓ])\s*=\s*-?[0-9]+(?:\.[0-9]+)?", l):
        return False
    return True


def _procesar_salida(out, pagina, region, ecuaciones, vistos):
    """Dumps the equations of an OCR result into the global list, with filter + dedup."""
    filtradas = 0
    ecs = out.get("ecuaciones", [])
    if not isinstance(ecs, list):
        ecs = []
    for e in ecs:
        if not isinstance(e, dict):
            filtradas += 1
            continue
        latex = _normalizar_latex(e.get("latex", ""))
        if not _es_ecuacion_valida(latex):
            filtradas += 1
            continue
        if latex in vistos:
            filtradas += 1
            continue
        vistos.add(latex)
        reg = {"latex": latex,
               "contexto": e.get("contexto", ""),
               "pagina": pagina,  # 1-based
               "region": region}
        if region is None:
            reg.pop("region")
        ecuaciones.append(reg)
    return filtradas


def extraer_ecuaciones(pdf, paginas=None, max_paginas=8, dpi=150, recortar=True):
    """Extracts equations (LaTeX) from the PDF. Returns a dict with statistics + list."""
    if paginas is None:
        paginas = detectar_paginas(pdf, max_paginas)
    doc = fitz.open(pdf)
    ecuaciones = []
    vistos = set()
    filtradas = 0
    areas = {"pagina_completa": 0.0, "recortada": 0.0, "paginas_recortadas": 0}

    for i in paginas:
        if not (0 <= i < doc.page_count):
            continue
        page = doc[i]
        pagina_1b = i + 1

        regiones = detectar_regiones_matematicas(doc, i) if recortar else []
        if regiones and len(regiones) <= MAX_REGIONES_POR_PAGINA:
            areas["paginas_recortadas"] += 1
            for ri, rect in enumerate(regiones):
                pix = page.get_pixmap(clip=rect, dpi=dpi)
                img = base64.b64encode(pix.tobytes("png")).decode()
                out = parse_json(call_vision(SYS_VISION, [img]))
                filtradas += _procesar_salida(out, pagina_1b, ri, ecuaciones, vistos)
                areas["recortada"] += rect.width * rect.height
        else:
            pix = page.get_pixmap(dpi=dpi)
            img = base64.b64encode(pix.tobytes("png")).decode()
            out = parse_json(call_vision(SYS_VISION, [img]))
            filtradas += _procesar_salida(out, pagina_1b, None, ecuaciones, vistos)
            areas["pagina_completa"] += page.rect.width * page.rect.height

    return {"documento": os.path.basename(pdf),
            "paginas_analizadas": paginas,
            "total_ecuaciones": len(ecuaciones),
            "falsos_positivos_filtrados": filtradas,
            "recorte": {"paginas_recortadas": areas["paginas_recortadas"],
                        "area_total_recortada": round(areas["recortada"], 1),
                        "area_paginas_completas": round(areas["pagina_completa"], 1)},
            "ecuaciones": ecuaciones}


def main():
    ap = argparse.ArgumentParser(description="Extractor de fórmulas desde PDF (sin LaTeX fuente)")
    ap.add_argument("pdf")
    ap.add_argument("--max-paginas", type=int, default=8)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--out", default=None)
    ap.add_argument("--pagina-completa", action="store_true",
                    help="desactiva el recorte de regiones (manda páginas completas)")
    args = ap.parse_args()

    print(f"Detectando páginas con ecuaciones en {args.pdf} ...")
    paginas = detectar_paginas(args.pdf, args.max_paginas)
    print(f"Páginas candidatas (1-based): {[p + 1 for p in paginas]}")

    r = extraer_ecuaciones(args.pdf, paginas=paginas, dpi=args.dpi,
                           recortar=not args.pagina_completa)
    print(f"\n=== {r['total_ecuaciones']} ecuaciones extraídas ===\n")
    for e in r["ecuaciones"]:
        loc = f"[pág {e['pagina']}" + (f", región {e['region']}]" if "region" in e else "]")
        print(f"  {loc} ${e['latex']}$")

    rec = r["recorte"]
    if rec["paginas_recortadas"]:
        print(f"\n[recorte activo: {rec['paginas_recortadas']} página(s) recortada(s)]")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        print(f"\n[guardado en {args.out}]")
    report_cost()


if __name__ == "__main__":
    main()
