#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Cambria Math glyph atlas (Identity-H subset) for single labeling by oracle.

Word preserves the original GIDs when subdividing (verified). This module rasterizes
each GID used in the thesis using SVGPathPen (resolves compounds, holes with
nonzero fill-rule and curves correctly) and PyMuPDF to rasterize the SVG.

Then the vision oracle labels each GID only ONCE (single cost) and a local
deterministic GID→Unicode/LaTeX dictionary is obtained, free forever.
"""
import os, sys

import fitz  # pymupdf
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FONT_PATH = "/tmp/AXCKZK_Cambria_Math_Identity_H.ttf"


def _render_gid(font, gid, cell=64):
    """Renders a glyph (by GID) to an L image of `cell`x`cell`. Handles holes,
    curves and components via SVGPathPen + PyMuPDF (nonzero fill-rule)."""
    glyphset = font.getGlyphSet()
    name = font.getGlyphOrder()[gid]
    glyph = glyphset[name]

    bp = BoundsPen(glyphset)
    glyph.draw(bp)
    if bp.bounds is None:
        return Image.new("L", (cell, cell), 255)
    xmin, ymin, xmax, ymax = bp.bounds
    w = max(xmax - xmin, 1e-9)
    h = max(ymax - ymin, 1e-9)
    scale = min((cell - 8) / w, (cell - 8) / h)
    cx0 = (xmin + xmax) / 2.0
    cy0 = (ymin + ymax) / 2.0
    dx = cell / 2.0 - scale * cx0
    dy = cell / 2.0 + scale * cy0  # y-flip: font y-up → image y-down

    inner = SVGPathPen(glyphset)
    # TransformPen tuple = (xx, xy, yx, yy, dx, dy); we want (x,y) → (s*x+dx, -s*y+dy)
    tpen = TransformPen(inner, (scale, 0, 0, -scale, dx, dy))
    glyph.draw(tpen)
    d = inner.getCommands()

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{cell}" height="{cell}" '
           f'viewBox="0 0 {cell} {cell}">'
           f'<path d="{d}" fill="black" fill-rule="nonzero"/></svg>')
    doc = fitz.open(stream=svg.encode(), filetype="svg")
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(1, 1), colorspace=fitz.csGRAY, alpha=False)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    if img.size != (cell, cell):
        img = img.resize((cell, cell))
    return img


DIGITS = {
    "0": ["111", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"],
    "3": ["111", "001", "011", "001", "111"],
    "4": ["101", "101", "111", "001", "001"],
    "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"],
    "7": ["111", "001", "001", "010", "010"],
    "8": ["111", "101", "111", "101", "111"],
    "9": ["111", "101", "111", "001", "111"],
}


def _draw_text(draw, text, x, y, scale=1):
    cx = x
    for ch in text:
        rows = DIGITS.get(ch)
        if not rows:
            cx += 4 * scale
            continue
        for r, row in enumerate(rows):
            for c, bit in enumerate(row):
                if bit == "1":
                    draw.rectangle([cx + c * scale, y + r * scale,
                                    cx + (c + 1) * scale - 1, y + (r + 1) * scale - 1], fill=0)
        cx += 4 * scale


def build_atlas(gids, cell=64, cols=10, font_path=FONT_PATH):
    font = TTFont(font_path)
    rows = (len(gids) + cols - 1) // cols
    sheet = Image.new("L", (cols * cell, rows * (cell + 18)), 255)
    d = ImageDraw.Draw(sheet)
    for k, gid in enumerate(gids):
        r, c = divmod(k, cols)
        img = _render_gid(font, gid, cell=cell)
        sheet.paste(img, (c * cell, r * (cell + 18)))
        _draw_text(d, str(gid), c * cell + 2, r * (cell + 18) + cell + 1, scale=2)
    return sheet


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", type=int, default=0, help="renderizar N glifos de prueba")
    ap.add_argument("--gids", default=None, help="archivo JSON con lista de GIDs")
    ap.add_argument("--out", default="glyph_atlas", help="prefijo de salida")
    args = ap.parse_args()

    if args.preview:
        gids = [7, 131, 889, 932, 3404, 3415, 3493, 3495, 4666, 4667]
        sheet = build_atlas(gids, cols=5)
        out = f"{args.out}_preview.png"
        sheet.save(out)
        print("guardado", out)
    elif args.gids:
        gids = json.load(open(args.gids))
        per_sheet = 50
        for i in range(0, len(gids), per_sheet):
            chunk = gids[i:i + per_sheet]
            sheet = build_atlas(chunk, cols=10)
            out = f"{args.out}_{i // per_sheet}.png"
            sheet.save(out)
            print("guardado", out, f"({len(chunk)} glifos)")
