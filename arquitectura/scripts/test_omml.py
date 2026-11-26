#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Tests deterministas para extractor_omml.py (sin API, sin modelo).

1. Construye un .docx mínimo con OMML a mano y verifica la conversión a LaTeX.
2. Si hay pandoc, verificación end-to-end: LaTeX -> .docx -> OMML -> LaTeX.
"""
import os, subprocess, sys, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extractor_omml import extraer_math_docx

HERE = os.path.dirname(os.path.abspath(__file__))

# ── OMML mínimo para: (1) fracción  (2) raíz  (3) sumatoria con límites  (4) sub/sup
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

def _r(text):
    return f'<m:r><m:t xml:space="preserve">{text}</m:t></m:r>'


OMML_BODY = f"""
<m:oMathPara>
  <m:oMath>
    <m:f><m:num>{_r("a")}</m:num><m:den>{_r("b")}</m:den></m:f>
  </m:oMath>
  <m:oMath>
    <m:rad><m:e>{_r("x")}</m:e></m:rad>
  </m:oMath>
  <m:oMath>
    <m:rad><m:deg>{_r("3")}</m:deg><m:e>{_r("y")}</m:e></m:rad>
  </m:oMath>
  <m:oMath>
    <m:nary>
      <m:naryPr><m:chr m:val="∑"/></m:naryPr>
      <m:sub>{_r("i=1")}</m:sub><m:sup>{_r("n")}</m:sup><m:e>{_r("x")}</m:e>
    </m:nary>
  </m:oMath>
  <m:oMath>
    <m:sSubSup><m:e>{_r("x")}</m:e><m:sub>{_r("i")}</m:sub><m:sup>{_r("2")}</m:sup></m:sSubSup>
  </m:oMath>
  <m:oMath>
    <m:d><m:dPr><m:begChr m:val="("/><m:endChr m:val=")"/></m:dPr><m:e>{_r("a+b")}</m:e></m:d>
  </m:oMath>
  <m:oMath>
    <m:d><m:e>{_r("c+d")}</m:e></m:d>
  </m:oMath>
  <m:oMath>
    <m:nary>
      <m:sub>{_r("0")}</m:sub><m:sup>{_r("1")}</m:sup><m:e>{_r("f(x)")}</m:e>
    </m:nary>
  </m:oMath>
  <m:oMath>
    <m:limLow><m:e>{_r("lim")}</m:e><m:lim>{_r("x→0")}</m:lim></m:limLow>
  </m:oMath>
</m:oMathPara>
"""

DOCUMENT = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W}" xmlns:m="{M}">
  <w:body>
    <w:p><w:r><w:t>Contexto de prueba</w:t></w:r></w:p>
    {OMML_BODY}
  </w:body>
</w:document>"""


def _build_min_docx():
    path = os.path.join(HERE, "_test_omml.docx")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", DOCUMENT)
    return path


def test_minimal():
    path = _build_min_docx()
    r = extraer_math_docx(path)
    assert "error" not in r, r
    lats = [e["latex"] for e in r["ecuaciones"]]
    print("LaTeX extraídos:", lats)
    esperados = [
        r"\frac{a}{b}",
        r"\sqrt{x}",
        r"\sqrt[3]{y}",
        r"\sum_{i=1}^{n} x",
        r"{x}_{i}^{2}",
        r"\left(a+b\right)",
        r"\left(c+d\right)",
        r"\int_{0}^{1} f(x)",
        r"{lim}_{x{\rightarrow}0}",
    ]
    assert lats == esperados, f"esperado {esperados}, obtuve {lats}"
    assert r["total_ecuaciones"] == len(esperados)
    print("✅ test_minimal OK")


def test_pandoc_roundtrip():
    """End-to-end: LaTeX -> docx (pandoc) -> OMML -> LaTeX (nuestro)."""
    pandoc = subprocess.run(["which", "pandoc"], capture_output=True, text=True).stdout.strip()
    if not pandoc:
        print("⏭  pandoc no disponible: salteo test end-to-end")
        return
    tex = os.path.join(HERE, "_test_omml.tex")
    docx = os.path.join(HERE, "_test_pandoc.docx")
    with open(tex, "w", encoding="utf-8") as f:
        f.write(r"""
\documentclass{article}
\begin{document}
La fórmula es $\frac{a}{b}$ y también $\sqrt{x}$ y $\sum_{i=1}^{n} x_i$.
\end{document}
""")
    subprocess.run([pandoc, tex, "-o", docx, "-s"], check=True)
    r = extraer_math_docx(docx)
    assert "error" not in r, r
    lats = [e["latex"] for e in r["ecuaciones"]]
    print("pandoc roundtrip LaTeX:", lats)
    # debe recuperar la fracción, la raíz y la sumatoria
    joined = " ".join(lats)
    for frag in (r"\frac", r"\sqrt", r"\sum"):
        assert frag in joined, f"falta {frag} en {lats}"
    print("✅ test_pandoc_roundtrip OK")


if __name__ == "__main__":
    test_minimal()
    test_pandoc_roundtrip()
    print("\nTODOS LOS TESTS OMML PASARON")
