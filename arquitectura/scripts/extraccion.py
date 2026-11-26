#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Three paths for extracting engineering knowledge.

  1. LaTeX source        → exact equations (if the .tex exists).
  2. Solved examples  → golden tests (input → numeric result).
  3. PDF with mathematical OCR → Mathpix/Nougat/Grobid + heuristic fallback.

The honest point: path 1 and path 2 are deterministic and cheap; path 3 needs an
external service (Mathpix API key) or a heavy model (Nougat), so it exposes the
interface and, without that, falls back to a text-layer fallback marked as "low quality".
"""
import base64, json, os, re, shutil, sys
from urllib.request import Request, urlopen

import fitz  # pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prototipo import call_llm, parse_json, MODEL, call_vision


# ═══════════════════════════════════════════════════════════════════════════════
# PATH 1 — LaTeX source (exact equations)
# ═══════════════════════════════════════════════════════════════════════════════
def extraer_ecuaciones_latex(ruta_tex):
    """Extracts equations from a .tex: equation/align/gather/eqnarray environments,
    display $$...$$ and \\[...\\], and inline $...$."""
    src = open(ruta_tex, encoding="utf-8").read()
    src = re.sub(r"(?<!\\)%.*", "", src)  # remove comments
    eqs = []

    entornos = r"equation\*?|align\*?|gather\*?|eqnarray\*?"
    for m in re.finditer(rf"\\begin\{{({entornos})\}}(.*?)\\end\{{\1\}}", src, re.S):
        eqs.append({"tipo": m.group(1), "latex": m.group(2).strip()})
    for m in re.finditer(r"\$\$(.*?)\$\$|\\\[(.*?)\\\]", src, re.S):
        eqs.append({"tipo": "display", "latex": (m.group(1) or m.group(2)).strip()})
    for m in re.finditer(r"(?<!\$)\$([^$]+?)\$(?!\$)", src):
        eqs.append({"tipo": "inline", "latex": m.group(1).strip()})
    return eqs


# ═══════════════════════════════════════════════════════════════════════════════
# PATH 2 — solved examples → golden tests
# ═══════════════════════════════════════════════════════════════════════════════
SYS_EJEMPLO = (
    "Eres un extractor de ejemplos resueltos de ingeniería. Dado un fragmento técnico, "
    "identificá UN ejemplo numérico resuelto y devolvé SOLO JSON (json_object):\n"
    '- "inputs": objeto {nombre: valor_numerico} (los datos de entrada con sus números)\n'
    '- "resultado": objeto {nombre: valor_numerico} (el resultado que da el texto)\n'
    '- "formula_hint": string (la fórmula que usaron, si aparece)\n'
    '- "nota": string\n'
    "Reglas: extraé los NÚMEROS exactos que aparecen; si no hay un ejemplo resuelto, "
    "devolvé inputs={} y resultado={}."
)


def extraer_ejemplo_resuelto(texto):
    return parse_json(call_llm(f"Fragmento:\n\n{texto}", MODEL, system=SYS_EJEMPLO))


def verificar_ejemplo(fn, ejemplo, tolerancia=0.02):
    """Reproduces the solved example with a deterministic formula and compares.

    Returns (ok, detail). `fn` receives a dict of inputs and returns a dict name→value.
    """
    inputs = ejemplo.get("inputs", {})
    esperado = ejemplo.get("resultado", {})
    if not inputs or not esperado:
        return False, "ejemplo sin inputs o sin resultado"
    try:
        got = fn(inputs)
    except Exception as e:
        return False, f"la fórmula falló con estos inputs: {e}"
    if isinstance(got, list):
        got = {q["name"]: q["value"] for q in got}
    if not isinstance(got, dict):
        return False, f"la fórmula devolvió un tipo no soportado: {type(got).__name__}"
    detalle = []
    ok = True
    for k, v_esp in esperado.items():
        if k not in got:
            detalle.append(f"{k}: no producido por la fórmula")
            ok = False
            continue
        try:
            v_esp_f = float(str(v_esp).replace(",", "."))
            v_got_f = float(str(got[k]).replace(",", "."))
        except (TypeError, ValueError):
            detalle.append(f"{k}: no numérico (fórmula={got[k]!r} vs texto={v_esp!r})")
            ok = False
            continue
        diff = abs(v_got_f - v_esp_f)
        rel = diff / max(abs(v_esp_f), 1e-9)
        pasa = rel <= tolerancia
        ok = ok and pasa
        detalle.append(f"{k}: fórmula={v_got_f} vs texto={v_esp_f} (rel {rel:.3f}) {'OK' if pasa else 'MISMATCH'}")
    return ok, "; ".join(detalle)


# ═══════════════════════════════════════════════════════════════════════════════
# PATH 3 — PDF with mathematical OCR + fallback
# ═══════════════════════════════════════════════════════════════════════════════
def _mathpix(ruta_pdf):
    app_id = os.environ.get("MATHPIX_APP_ID")
    app_key = os.environ.get("MATHPIX_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError("Mathpix requiere MATHPIX_APP_ID y MATHPIX_APP_KEY")
    import base64
    data = base64.b64encode(open(ruta_pdf, "rb").read()).decode()
    body = json.dumps({
        "src": f"data:application/pdf;base64,{data}",
        "formats": ["latex_styled"],
        "data_options": {"include_latex": True},
    }).encode()
    req = Request("https://api.mathpix.com/v3/pdf", data=body,
                  headers={"app_id": app_id, "app_key": app_key,
                           "Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode())


def _nougat(ruta_pdf):
    if not shutil.which("nougat"):
        raise RuntimeError("Nougat no está instalado (pip install nougat-ocr; requiere torch)")
    import subprocess
    out = subprocess.run(["nougat", "pdf", ruta_pdf, "--out", "/tmp/nougat_out"],
                         capture_output=True, text=True, timeout=1800)
    return {"stdout": out.stdout[-2000:], "stderr": out.stderr[-1000:]}


SYS_VISION = (
    "Sos un transcriptor de ecuaciones matemáticas a partir de imágenes de páginas de un "
    "documento de ingeniería. Devolvé SOLO JSON (json_object) con:\n"
    '- "ecuaciones": lista de {"latex": "...", "contexto": "..."} (LaTeX)\n'
    '- "texto_relevante": string (el texto técnico que acompaña a las ecuaciones)\n'
    "Reglas: sé fiel a lo que ves en la imagen; NO inventes símbolos ni ecuaciones que no "
    "estén. Si una página no tiene ecuaciones, devolvé ecuaciones=[] y explicalo en texto_relevante."
)


def _vision(ruta_pdf, paginas=None, dpi=150):
    """Real mathematical OCR with a multimodal LLM: renders PDF pages to PNG and the model
    transcribes the equations to LaTeX. Requires neither Mathpix nor Nougat, only OpenRouter."""
    doc = fitz.open(ruta_pdf)
    if paginas is None:
        paginas = list(range(min(doc.page_count, 6)))
    imgs_b64 = []
    for i in paginas:
        if 0 <= i < doc.page_count:
            pix = doc[i].get_pixmap(dpi=dpi)
            imgs_b64.append(base64.b64encode(pix.tobytes("png")).decode())
    out = parse_json(call_vision(SYS_VISION, imgs_b64))
    out["motor"] = "vision"
    out["paginas"] = paginas
    return out


def _fallback_pdf(ruta_pdf):
    """PDF text layer: extracts equation-like lines (low quality)."""
    doc = fitz.open(ruta_pdf)
    lineas = []
    for page in doc:
        for l in page.get_text().splitlines():
            if re.search(r"[=×÷√∑∫±·]|[α-ωΑ-Ω]|\b(MPa|kN|mm|cm|m²|mm²|kPa)\b", l):
                lineas.append(l)
    return {"motor": "fallback_texto", "calidad": "baja",
            "ecuaciones_candidatas": lineas[:50], "nota":
            "Ecuaciones reconstruidas de la capa de texto del PDF (se pierde la estructura "
            "simbólica). Para exactitud usar LaTeX (camino 1) o un OCR matemático real."}


def extraer_ecuaciones_pdf(ruta_pdf, motor="auto", paginas=None):
    """Path 3: mathematical OCR. motor = auto|vision|mathpix|nougat|fallback.

    `auto` prefers real OCR: Mathpix (if there is a key) → vision (multimodal model) →
    Nougat (if installed) → text layer fallback.
    """
    if motor == "auto":
        if os.environ.get("MATHPIX_APP_ID") and os.environ.get("MATHPIX_APP_KEY"):
            motor = "mathpix"
        else:
            motor = "vision"   # the multimodal model is the default real OCR
    if motor == "mathpix":
        return _mathpix(ruta_pdf)
    if motor == "nougat":
        return _nougat(ruta_pdf)
    if motor == "vision":
        return _vision(ruta_pdf, paginas=paginas)
    return _fallback_pdf(ruta_pdf)
