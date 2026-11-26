#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Formula extractor from a .docx WITHOUT any model (deterministic).

Word math lives as OMML (Office Math Markup Language) inside
`word/document.xml`: `m:oMath` elements. This module walks them and converts them
to LaTeX in a 100% deterministic way (only zipfile + xml.etree, stdlib).

It requires no source LaTeX, no OCR, no GPU nor local LLM.

Usage:
  python3 extractor_omml.py documento.docx [--out formulas.json]
"""
import argparse, json, os, re, sys, zipfile
import xml.etree.ElementTree as ET

NS_MATH = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def _local(tag):
    """Returns the local name of a tag/attribute in Clark notation (`{ns}local`)."""
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _attr(elem, name):
    for k, v in elem.attrib.items():
        if _local(k) == name:
            return v
    return None


def _child(elem, name):
    for c in elem:
        if _local(c.tag) == name:
            return c
    return None


def _children(elem, name):
    return [c for c in elem if _local(c.tag) == name]


# Unicode that appears typed literally in m:t → LaTeX command (the output must
# compile in a standard LaTeX without depending on unicode-math).
_UNICODE_MATH = {
    "×": r"\times", "÷": r"\div", "·": r"\cdot", "⋅": r"\cdot",
    "≤": r"\leq", "≥": r"\geq", "≠": r"\neq", "≈": r"\approx", "≡": r"\equiv",
    "≪": r"\ll", "≫": r"\gg", "∝": r"\propto",
    "∴": r"\therefore", "∵": r"\because", "⟹": r"\implies",
    "−": "-", "–": "-", "—": "-",
    "√": r"\surd", "∑": r"\sum", "∏": r"\prod", "∫": r"\int", "∐": r"\coprod",
    "∞": r"\infty", "∂": r"\partial", "∇": r"\nabla",
    "±": r"\pm", "∓": r"\mp",
    "→": r"\rightarrow", "←": r"\leftarrow", "↔": r"\leftrightarrow",
    "⇒": r"\Rightarrow", "⇔": r"\Leftrightarrow",
    "∈": r"\in", "∉": r"\notin", "⊂": r"\subset", "⊆": r"\subseteq",
    "∪": r"\cup", "∩": r"\cap", "∅": r"\emptyset",
    "∧": r"\land", "∨": r"\lor", "¬": r"\neg",
    "⊕": r"\oplus", "⊗": r"\otimes", "⊥": r"\perp", "∥": r"\parallel",
    "∠": r"\angle", "∀": r"\forall", "∃": r"\exists",
    "ℏ": r"\hbar", "ℓ": r"\ell",
    "°": r"^{\circ}", "′": r"^{\prime}", "″": r"^{\prime\prime}",
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\varepsilon", "ϵ": r"\epsilon", "ζ": r"\zeta", "η": r"\eta",
    "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi",
    "π": r"\pi", "ϖ": r"\varpi", "ρ": r"\rho", "ϱ": r"\varrho",
    "σ": r"\sigma", "ς": r"\varsigma", "τ": r"\tau", "υ": r"\upsilon",
    "φ": r"\varphi", "ϕ": r"\phi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Υ": r"\Upsilon",
    "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
    "½": r"\frac{1}{2}", "⅓": r"\frac{1}{3}", "¼": r"\frac{1}{4}", "¾": r"\frac{3}{4}",
}

_SUP = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
         "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
         "⁻": "-", "⁺": "+", "⁼": "=", "⁽": "(", "⁾": ")",
         "ⁿ": "n", "ⁱ": "i"}
_SUB = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
         "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
         "₋": "-", "₊": "+", "₌": "="}

_ESCAPE = {
    "{": r"\{", "}": r"\}", "%": r"\%", "&": r"\&", "$": r"\$",
    "#": r"\#", "_": r"\_", "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}", "\\": r"\backslash{}",
}


def _escape_text(s):
    r"""Escapes literal OMML text to LaTeX: maps math Unicode and escapes specials.

    Groups consecutive super/subscripts (10¹² → 10^{12}) and separates with a space a command
    that ends in a letter if an alphanumeric follows (2πr → 2\pi r), so that it compiles."""
    out = []
    for ch in s:
        if ch in _UNICODE_MATH:
            cmd = _UNICODE_MATH[ch]
            if cmd and cmd[-1].isalpha():
                cmd = "{" + cmd + "}"  # self-contained: survives Word splitting runs
            out.append(cmd)
        elif ch in _ESCAPE:
            out.append(_ESCAPE[ch])
        else:
            out.append(ch)  # includes ⁰¹²... and ₀₁₂... that are grouped below
    r = "".join(out)
    # group consecutive super/subscripts AFTER escaping (do not re-escape ^ {} _)
    r = re.sub("[" + "".join(_SUP) + "]+",
               lambda m: "^{" + "".join(_SUP[c] for c in m.group(0)) + "}", r)
    r = re.sub("[" + "".join(_SUB) + "]+",
               lambda m: "_{" + "".join(_SUB[c] for c in m.group(0)) + "}", r)
    return r


def _run_text(elem):
    """Text of an `m:r` run (concatenates escaped `m:t`) respecting m:nor / m:sty / m:scr."""
    texto = "".join(c.text or "" for c in elem.iter() if _local(c.tag) == "t")
    latex = _escape_text(texto)
    rpr = _child(elem, "rPr")
    nor = _child(rpr, "nor") if rpr is not None else None
    sty = _child(rpr, "sty") if rpr is not None else None
    scr = _child(rpr, "scr") if rpr is not None else None
    if nor is not None:
        return (r"\text{" + latex + "}") if " " in latex else (r"\mathrm{" + latex + "}")
    if sty is not None:
        val = _attr(sty, "val") or ""
        if val in ("b", "bi"):
            return r"\mathbf{" + latex + "}"
        if val == "p":
            return r"\mathrm{" + latex + "}"
    if scr is not None:
        val = _attr(scr, "val") or ""
        if val == "double-struck":
            return r"\mathbb{" + latex + "}"
        if val == "script":
            return r"\mathcal{" + latex + "}"
    return latex


def _convert(elem):
    """Recursively converts an OMML subtree to LaTeX."""
    if elem is None:
        return ""
    tag = _local(elem.tag)

    if tag in ("r",):
        return _run_text(elem)
    if tag == "t":
        return _escape_text(elem.text or "")

    def kids():
        return "".join(_convert(c) for c in elem)

    if tag == "f":  # fraction
        num = _child(elem, "num")
        den = _child(elem, "den")
        return r"\frac{" + kids_of(num) + "}{" + kids_of(den) + "}"

    if tag == "sSup":
        return "{" + kids_of(_child(elem, "e")) + "}^{" + kids_of(_child(elem, "sup")) + "}"

    if tag == "sSub":
        return "{" + kids_of(_child(elem, "e")) + "}_{" + kids_of(_child(elem, "sub")) + "}"

    if tag == "sSubSup":
        return ("{" + kids_of(_child(elem, "e")) + "}"
                + "_{" + kids_of(_child(elem, "sub")) + "}"
                + "^{" + kids_of(_child(elem, "sup")) + "}")

    if tag == "rad":  # root
        deg = _child(elem, "deg")
        radicand = _child(elem, "e")
        if deg is not None and kids_of(deg).strip():
            return r"\sqrt[" + kids_of(deg) + "]{" + kids_of(radicand) + "}"
        return r"\sqrt{" + kids_of(radicand) + "}"

    if tag == "nary":  # sums, integrals, products, unions...
        pr = _child(elem, "naryPr")
        chr_el = _child(pr, "chr") if pr is not None else None
        # ECMA-376: absent m:chr = integral (Word omits the chr for ∫).
        op_chr = _attr(chr_el, "val") if chr_el is not None else "∫"
        op = _NARY.get(op_chr, r"\operatorname{" + (op_chr or "?") + "}")
        sub = _child(elem, "sub")
        sup = _child(elem, "sup")
        base = _child(elem, "e")
        s = ""
        if sub is not None and kids_of(sub).strip():
            s += "_{" + kids_of(sub) + "}"
        if sup is not None and kids_of(sup).strip():
            s += "^{" + kids_of(sup) + "}"
        return op + s + " " + kids_of(base)

    if tag == "d":  # delimiters (parentheses, brackets, absolute value...)
        pr = _child(elem, "dPr")
        beg_el = _child(pr, "begChr") if pr is not None else None
        end_el = _child(pr, "endChr") if pr is not None else None
        sep_el = _child(pr, "sepChr") if pr is not None else None
        # ECMA-376: absent begChr/endChr = '(' and ')'; absent sepChr = '|'.
        beg = _attr(beg_el, "val") if beg_el is not None else "("
        end = _attr(end_el, "val") if end_el is not None else ")"
        sep = _attr(sep_el, "val") if sep_el is not None else "|"
        inner = sep.join(kids_of(e) for e in _children(elem, "e"))
        if beg == "" and end == "":
            return inner
        return r"\left" + _delim(beg) + inner + r"\right" + _delim(end)

    if tag == "func":
        fname_el = _child(elem, "fName")
        body = kids_of(_child(elem, "e"))
        # Word encodes lim_{x→0} f(x) as func > fName > limLow(e=lim, lim=x→0)
        inner = _child(fname_el, "limLow") or _child(fname_el, "limUpp")
        if inner is not None:
            base = kids_of(_child(inner, "e")).strip()
            fn = _FUNC.get(base, r"\operatorname{" + (base or "?") + "}")
            lim_part = kids_of(_child(inner, "lim"))
            if _local(inner.tag) == "limLow":
                return fn + "_{" + lim_part + "} " + body
            return fn + "^{" + lim_part + "} " + body
        name = kids_of(fname_el).strip()
        fn = _FUNC.get(name, r"\operatorname{" + name + "}" if name else r"\operatorname{}")
        return fn + " " + body

    if tag == "limLow":
        return "{" + kids_of(_child(elem, "e")) + "}_{" + kids_of(_child(elem, "lim")) + "}"
    if tag == "limUpp":
        return "{" + kids_of(_child(elem, "e")) + "}^{" + kids_of(_child(elem, "lim")) + "}"

    if tag == "acc":  # accent (hat, tilde, bar, vector...)
        pr = _child(elem, "accPr")
        chr_el = _child(pr, "chr") if pr is not None else None
        ch = _attr(chr_el, "val") if chr_el is not None else "^"
        return _ACC.get(ch, r"\hat") + "{" + kids_of(_child(elem, "e")) + "}"

    if tag == "bar":  # overline / underline
        pr = _child(elem, "barPr")
        pos_el = _child(pr, "pos") if pr is not None else None
        # ECMA-376: absent m:pos = "bot" (bar underneath). Verify with a real docx.
        pos = _attr(pos_el, "val") if pos_el is not None else "bot"
        return (r"\underline{" if pos == "bot" else r"\overline{") + \
               kids_of(_child(elem, "e")) + "}"

    if tag == "groupChr":
        pr = _child(elem, "groupChrPr")
        chr_el = _child(pr, "chr") if pr is not None else None
        ch = _attr(chr_el, "val") if chr_el is not None else ""
        if ch == "⏞":
            return r"\overbrace{" + kids_of(_child(elem, "e")) + "}"
        if ch == "⏟":
            return r"\underbrace{" + kids_of(_child(elem, "e")) + "}"
        return kids_of(_child(elem, "e"))

    if tag == "eqArr":  # system of stacked equations (the alignment & is NOT escaped)
        rows = [kids_of(e).replace(r"\&", "&") for e in _children(elem, "e")]
        return r"\begin{aligned}" + r" \\ ".join(rows) + r"\end{aligned}"

    if tag == "m":  # matrix
        rows = []
        for mr in _children(elem, "mr"):
            cells = [kids_of(e) for e in _children(mr, "e")]
            rows.append(" & ".join(cells))
        return r"\begin{matrix}" + r" \\ ".join(rows) + r"\end{matrix}"

    if tag == "borderBox":
        return r"\boxed{" + kids_of(_child(elem, "e")) + "}"
    if tag == "box":
        return kids_of(_child(elem, "e"))

    if tag in ("preSubSup", "sPre"):  # sub/sup before the base
        base = kids_of(_child(elem, "e"))
        sub = _child(elem, "sub")
        sup = _child(elem, "sup")
        pre = ""
        if sup is not None and kids_of(sup).strip():
            pre += "^{" + kids_of(sup) + "}"
        if sub is not None and kids_of(sub).strip():
            pre += "_{" + kids_of(sub) + "}"
        return pre + base

    # generic containers (e, num, den, sup, sub, deg, lim, fName, mr, ...)
    return kids()


def kids_of(elem):
    """LaTeX text of a subtree (converts all its children in order)."""
    if elem is None:
        return ""
    return "".join(_convert(c) for c in elem)


def _delim(ch):
    if ch in ("", None):
        return "."  # invisible delimiter → \left. / \right.
    return {
        "(": "(", ")": ")",
        "[": "[", "]": "]",
        "{": r"\{", "}": r"\}",
        "|": "|", "‖": r"\|",
        "⌊": r"\lfloor", "⌋": r"\rfloor",
        "⌈": r"\lceil", "⌉": r"\rceil",
        "⟨": r"\langle", "⟩": r"\rangle",
        "⟦": r"\llbracket", "⟧": r"\rrbracket",
    }.get(ch, ch)


_NARY = {
    "∑": r"\sum", "∫": r"\int", "∏": r"\prod", "∐": r"\coprod",
    "⋃": r"\bigcup", "⋂": r"\bigcap", "⋀": r"\bigwedge", "⋁": r"\bigvee",
    "⊕": r"\bigoplus", "⊗": r"\bigotimes", "⊙": r"\bigodot",
    "⨄": r"\biguplus", "⨁": r"\bigoplus", "⨂": r"\bigotimes",
}

_FUNC = {
    "sin": r"\sin", "cos": r"\cos", "tan": r"\tan",
    "csc": r"\csc", "sec": r"\sec", "cot": r"\cot",
    "arcsin": r"\arcsin", "arccos": r"\arccos", "arctan": r"\arctan",
    "sinh": r"\sinh", "cosh": r"\cosh", "tanh": r"\tanh",
    "log": r"\log", "ln": r"\ln", "lg": r"\lg",
    "lim": r"\lim", "max": r"\max", "min": r"\min",
    "sup": r"\sup", "inf": r"\inf", "det": r"\det",
    "exp": r"\exp", "gcd": r"\gcd", "Pr": r"\Pr",
    "dim": r"\dim", "ker": r"\ker", "hom": r"\hom",
    "limsup": r"\limsup", "liminf": r"\liminf",
    "argmax": r"\arg\max", "argmin": r"\arg\min",
}

_ACC = {
    "^": r"\hat", "̂": r"\hat",
    "~": r"\tilde", "̃": r"\tilde",
    "¯": r"\bar", "̅": r"\bar",
    "→": r"\vec", "⃗": r"\vec",
    "·": r"\dot", "̇": r"\dot",
    "¨": r"\ddot",
}


def _paragraph_context(elem, parent):
    """Returns the plain text of the containing paragraph, excluding the math."""
    p = parent.get(elem)
    while p is not None and _local(p.tag) != "p":
        p = parent.get(p)
    if p is None:
        return ""
    parts = []
    for c in p.iter():
        if _local(c.tag) != "t":
            continue
        # discard text that is inside an oMath
        inside = False
        a = parent.get(c)
        while a is not None:
            if _local(a.tag) == "oMath":
                inside = True
                break
            a = parent.get(a)
        if not inside:
            parts.append(c.text or "")
    return " ".join(" ".join(parts).split())


def extraer_math_docx(path):
    """Extracts all OMML formulas from a .docx and returns them as LaTeX."""
    z = zipfile.ZipFile(path)
    doc = next((n for n in z.namelist()
                if n.lower().endswith("word/document.xml")), None)
    if doc is None:
        # tolerates unusual casing/paths
        doc = next((n for n in z.namelist() if n.lower().endswith("document.xml")), None)
    if doc is None:
        return {"error": "no se encontró word/document.xml en el .docx"}
    root = ET.fromstring(z.read(doc))
    parent = {c: p for p in root.iter() for c in p}

    out = []
    vistos = set()
    for om in [el for el in root.iter() if _local(el.tag) == "oMath"]:
        latex = " ".join(kids_of(om).split())
        if not latex or latex in vistos:
            continue
        vistos.add(latex)
        out.append({"latex": latex, "contexto": _paragraph_context(om, parent)})
    return {"documento": os.path.basename(path),
            "total_ecuaciones": len(out),
            "ecuaciones": out}


def main():
    ap = argparse.ArgumentParser(description="Extractor OMML→LaTeX desde .docx (sin modelo)")
    ap.add_argument("docx")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    r = extraer_math_docx(args.docx)
    if "error" in r:
        print("ERROR:", r["error"])
        sys.exit(1)

    print(f"=== {r['total_ecuaciones']} ecuaciones extraídas de {r['documento']} ===\n")
    for e in r["ecuaciones"]:
        print(f"  ${e['latex']}$")
        if e["contexto"]:
            print(f"     contexto: {e['contexto'][:100]}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        print(f"\n[guardado en {args.out}]")


if __name__ == "__main__":
    main()
