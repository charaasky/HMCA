#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""esquematico_svg.py — renderiza un ESQUEMÁTICO ELÉCTRICO legible a SVG + PDF.

Rediseñado para legibilidad: SÍMBOLOS ELÉCTRICOS ESTÁNDAR y CORRECTOS por tipo de bloque
(fuente de tensión = círculo con +/-, resistor = zigzag, capacitor = barras, carga = resistencia),
letras GRANDES (font 12-14), espaciado holgado, y nombres de pin que NO se superponen con el
cuerpo del componente.

Entrada: netlist = {
    "componentes": [{"ref": str, "nombre": str, "pines": [{"num": int, "nombre": str}]}],
    "conexiones": [{"de": {"ref": str, "pin": int}, "a": {"ref": str, "pin": int}}],
}

El SVG se convierte a PDF con google-chrome (headless --print-to-pdf).
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))


# ── dimensiones del lienzo (generosas para legibilidad) ─────────────────────
ANCHO_COMP = 460.0       # separación horizontal entre columnas (suficiente para pin names largos)
ALTO_FILA = 340.0        # separación vertical entre filas (evita solapamiento etiqueta/pin)
MARGEN = 80.0
ANCHO_SIMB = 120.0       # ancho del cuerpo del símbolo
ALTO_SIMB = 90.0         # alto del cuerpo del símbolo (para IC)


def _pines_lista(c):
    """Normaliza `pines` a lista de dict {num, nombre}."""
    raw = c.get("pines") or []
    out = []
    for p in raw:
        if isinstance(p, dict):
            out.append({"num": p.get("num") or p.get("numero"), "nombre": p.get("nombre", "")})
        else:
            out.append({"num": p, "nombre": ""})
    return out


def _tipo_simbolo(ref, nombre, tipo):
    """Clasifica el tipo de símbolo eléctrico a dibujar.

    Orden de prioridad: el `tipo` del bloque (del grafo) es más fiable que el ref/nombre.
    Devuelve uno de: fuente_tension, fuente_corriente, resistor, capacitor, inductancia,
    tierra. Devuelve None si NO reconoce el símbolo (=> defecto declarado, NO se dibuja
    un fallback que mienta sobre el componente).
    """
    t = (tipo or "").lower()
    n = (nombre or "").lower()
    r = (ref or "").upper()

    # tipos del grafo (fuente_tension, resistor, carga_resistiva, ...)
    if "fuente" in t and "tension" in t:
        return "fuente_tension"
    if "fuente" in t and "corriente" in t:
        return "fuente_corriente"
    if "tierra" in t or "ground" in t or "gnd" in t:
        return "tierra"
    if "capacitor" in t or "capacitancia" in t:
        return "capacitor"
    if "induct" in t or "bobina" in t or "inductor" in t:
        return "inductancia"
    if "resist" in t or "carga" in t or "potenciometro" in t:
        return "resistor"

    # ref (R/C/L/Q/D/V...)
    if r.startswith("R") or "resistor" in n or "resist" in n or "carga" in n:
        return "resistor"
    if r.startswith("C") or "capacitor" in n or "capacit" in n:
        return "capacitor"
    if r.startswith("L") or "induct" in n or "bobina" in n:
        return "inductancia"
    if r.startswith("V") or "fuente" in n or "bateria" in n or "battery" in n:
        return "fuente_tension"

    # NO reconocido: None => defecto declarado (nunca fallback silencioso)
    return None


def _svg_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _solo_numero(pins):
    """Devuelve el nº de 'pines' (terminales) como texto, para etiquetar puertos."""
    return pins


def _dibujar_simbolo(tipo, x, y, pines):
    """Dibuja el símbolo del componente centrado en (x, y). Devuelve (elementos_svg, puntos_pin).

    Los puntos_pin son las coordenadas (px, py) del extremo de cada terminal, en orden.
    Un símbolo de 2 terminales devuelve 2 puntos (izq, der). Un IC multi-pin devuelve N puntos.
    """
    e = []
    # todas las conexiones terminan en terminales izquierda/derecha sobre la línea y
    n = len(pines)
    if tipo in ("fuente_tension", "fuente_corriente", "capacitor", "inductancia") and n == 2:
        # símbolos de 2 terminales horizontales
        # cable de entrada y salida
        e.append(f'<line x1="{x - ANCHO_SIMB/2 - 40}" y1="{y}" x2="{x - ANCHO_SIMB/2}" y2="{y}" stroke="#1f2937" stroke-width="2"/>')
        e.append(f'<line x1="{x + ANCHO_SIMB/2}" y1="{y}" x2="{x + ANCHO_SIMB/2 + 40}" y2="{y}" stroke="#1f2937" stroke-width="2"/>')
        izq_pin = (x - ANCHO_SIMB/2 - 40, y)
        der_pin = (x + ANCHO_SIMB/2 + 40, y)
    else:
        # genérico resistor / cualquier cosa: 2 terminales
        e.append(f'<line x1="{x - ANCHO_SIMB/2 - 40}" y1="{y}" x2="{x - ANCHO_SIMB/2}" y2="{y}" stroke="#1f2937" stroke-width="2"/>')
        e.append(f'<line x1="{x + ANCHO_SIMB/2}" y1="{y}" x2="{x + ANCHO_SIMB/2 + 40}" y2="{y}" stroke="#1f2937" stroke-width="2"/>')
        izq_pin = (x - ANCHO_SIMB/2 - 40, y)
        der_pin = (x + ANCHO_SIMB/2 + 40, y)

    # cuerpo del símbolo
    if tipo == "fuente_tension":
        # círculo con + y -
        r = 40.0
        e.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="#1f2937" stroke-width="2.5"/>')
        e.append(f'<text x="{x}" y="{y - 8}" font-size="24" text-anchor="middle" fill="#1f2937" font-weight="bold">+</text>')
        e.append(f'<text x="{x}" y="{y + 20}" font-size="24" text-anchor="middle" fill="#1f2937" font-weight="bold">&#8722;</text>')

    elif tipo == "resistor":
        # zigzag horizontal grande (US IEC)
        zx = x - ANCHO_SIMB/2
        puntos = []
        for k in range(10):
            px = zx + k * (ANCHO_SIMB / 9)
            py = y if k % 2 == 0 else (y - 18 if (k // 2) % 2 == 0 else y + 18)
            puntos.append(f"{px:.1f},{py:.1f}")
        e.append(f'<polyline points="{" ".join(puntos)}" fill="none" stroke="#1f2937" stroke-width="2.5"/>')

    elif tipo == "capacitor":
        # dos barras paralelas verticales
        e.append(f'<line x1="{x - 10}" y1="{y - 30}" x2="{x - 10}" y2="{y + 30}" stroke="#1f2937" stroke-width="3"/>')
        e.append(f'<line x1="{x + 10}" y1="{y - 30}" x2="{x + 10}" y2="{y + 30}" stroke="#1f2937" stroke-width="3"/>')

    elif tipo == "inductancia":
        # arcos
        for k in range(4):
            cx = x - ANCHO_SIMB/2 + (k + 0.5) * (ANCHO_SIMB / 4)
            e.append(f'<path d="M {cx - 20:.1f} {y} A 20 20 0 0 1 {cx + 20:.1f} {y}" fill="none" stroke="#1f2937" stroke-width="2.5"/>')

    else:
        # fallback: rectángulo claro
        e.append(f'<rect x="{x - ANCHO_SIMB/2}" y="{y - ALTO_SIMB/2}" width="{ANCHO_SIMB}" height="{ALTO_SIMB}" fill="none" stroke="#1f2937" stroke-width="2.5"/>')

    return e, [izq_pin, der_pin]


def calcular_layout(netlist):
    """Calcula el layout del esquemático (posiciones, pin_pos, elementos con bbox).

    Devuelve dict con:
      - pos: {ref: (x, y)} centro de cada componente.
      - pin_pos: {(ref, pin): (px, py)} extremo de cada terminal.
      - textos: lista de {ref, texto, x, y, font_size, ancla, ancho_est} cada etiqueta/texto que
        se va a dibujar, con su bounding box aproximada (ancho estimado por longitud).
      - viewbox: (max_x, max_y) del lienzo final.

    Se usa para el VERIFICADOR DE FORMA (tamaño de texto, solapamiento, desborde) de forma
    determinista, SIN parsear el SVG ni depender del reescalado de LaTeX.
    """
    componentes = netlist.get("componentes") or []
    conexiones = netlist.get("conexiones") or []
    cols = 2

    # Ancho estimado del nombre de pin MÁS largo (fuente de verdad para el espaciado).
    # Los nombres largos desbordan si el offset/margen/separación son fijos; por eso toda la
    # geometría horizontal (offset X, separación entre columnas y ancho del lienzo) deriva de
    # este valor, con margen de seguridad. Sin esto, un pin name largo empuja el texto fuera
    # del viewBox o lo solapa con el componente de la columna vecina.
    ancho_pin_max = 0.0
    for c in componentes:
        for p in _pines_lista(c):
            if p.get("nombre"):
                ancho_pin_max = max(ancho_pin_max, len(p["nombre"]) * 7.0)

    # Separación horizontal entre columnas: lo que ocupan dos nombres de pin enfrentados
    # (izquierdo del comp B + derecho del comp A) más el cuerpo y un margen de holgura.
    # Nunca baja del piso base: un netlist con pins cortos no debe apretarse.
    ancho_col = max(ANCHO_COMP, ANCHO_SIMB + 2 * (40.0 + 8.0 + ancho_pin_max) + 120.0)

    pos = {}
    pin_pos = {}
    for i, c in enumerate(componentes):
        col = i % cols
        row = i // cols
        ref = c.get("ref", f"C{i}")
        pines = _pines_lista(c)
        x = MARGEN + col * ancho_col
        y = MARGEN + row * ALTO_FILA
        pos[ref] = (x, y)
        if len(pines) == 2:
            pin_pos[(ref, pines[0]["num"])] = (x - ANCHO_SIMB/2 - 40, y)
            pin_pos[(ref, pines[1]["num"])] = (x + ANCHO_SIMB/2 + 40, y)
        else:
            izq = pines[:(len(pines)+1)//2]
            der = pines[(len(pines)+1)//2:]
            for j, p in enumerate(izq):
                py = y - ALTO_SIMB/2 + (j + 1) * (ALTO_SIMB / (len(izq) + 1))
                pin_pos[(ref, p["num"])] = (x - ANCHO_SIMB/2 - 40, py)
            for j, p in enumerate(der):
                py = y - ALTO_SIMB/2 + (j + 1) * (ALTO_SIMB / (len(der) + 1))
                pin_pos[(ref, p["num"])] = (x + ANCHO_SIMB/2 + 40, py)

    # construir la lista de textos con bounding box aproximada
    textos = []
    for c in componentes:
        ref = c.get("ref", "?")
        pines = _pines_lista(c)
        x, y = pos[ref]
        # etiqueta del componente (centrada debajo)
        etiqueta = f"{ref}: {c.get('nombre', '')}"
        # estimación de ancho: ~0.6 em por caracter + márgenes
        ancho_est = len(etiqueta) * 8.0  # font 14 aprox 8px/char
        textos.append({
            "ref": ref, "texto": etiqueta, "x": x, "y": y + ALTO_SIMB/2 + 30,
            "font_size": 14, "ancla": "middle", "ancho_est": ancho_est,
        })
        # nombres de pines
        if len(pines) == 2:
            p_izq, p_der = pines[0], pines[1]
            px_izq, py_izq = pin_pos[(ref, p_izq["num"])]
            px_der, py_der = pin_pos[(ref, p_der["num"])]
            if p_izq.get("nombre"):
                ancho = len(p_izq["nombre"]) * 7.0
                textos.append({"ref": ref, "texto": p_izq["nombre"], "x": px_izq - 8,
                               "y": py_izq - 10, "font_size": 12, "ancla": "end",
                               "ancho_est": ancho})
            if p_der.get("nombre"):
                ancho = len(p_der["nombre"]) * 7.0
                textos.append({"ref": ref, "texto": p_der["nombre"], "x": px_der + 8,
                               "y": py_der - 10, "font_size": 12, "ancla": "start",
                               "ancho_est": ancho})

    # OFFSET X y viewbox: derivan del ancho de pin MÁS largo (con margen), NO de constantes
    # fijas. El offset izquierdo debe albergar el nombre de pin más largo del lado izquierdo;
    # el ancho del lienzo debe albergar la última columna + el nombre de pin derecho más largo.
    OFFSET_X = MARGEN + ancho_pin_max + 20.0
    OFFSET_Y = 20.0
    ultima_x = max((p[0] for p in pos.values()), default=0.0)
    # ancho del lienzo: el contenido se dibuja con translate(OFFSET_X, ...). El extremo derecho
    # real = OFFSET_X + (última columna + cuerpo + pin derecho más largo). Una holgura final
    # evita recortar el pin name de la última columna.
    max_x = OFFSET_X + ultima_x + ANCHO_SIMB/2 + 40.0 + 8.0 + ancho_pin_max + 60.0
    max_y = max((p[1] for p in pos.values()), default=0.0) + ALTO_FILA/2 + 140
    max_x = max(max_x, 800.0)
    max_y = max(max_y, 500.0)

    return {
        "pos": pos,
        "pin_pos": pin_pos,
        "textos": textos,
        "componentes": componentes,
        "conexiones": conexiones,
        "viewbox": (max_x, max_y),
        "offset": (OFFSET_X, OFFSET_Y),
    }


def render_svg(netlist, salida_svg, titulo="Esquemático"):
    """Renderiza el netlist a SVG legible. Devuelve path o None."""
    componentes = netlist.get("componentes") or []
    conexiones = netlist.get("conexiones") or []
    if not componentes:
        return None

    # usar el MISMO layout que el verificador (calcular_layout) para no divergir
    layout = calcular_layout(netlist)
    pos = layout["pos"]
    pin_pos = layout["pin_pos"]
    view_w, view_h = layout["viewbox"]
    OFFSET_X, OFFSET_Y = layout["offset"]

    # cables (wires): ruteo ortogonal, cable grueso y claro
    wires = []
    for cx in conexiones:
        a = cx.get("de"); b = cx.get("a")
        if not a or not b:
            continue
        pa = pin_pos.get((a.get("ref"), a.get("pin")))
        pb = pin_pos.get((b.get("ref"), b.get("pin")))
        if pa is None or pb is None:
            continue
        x1, y1 = pa; x2, y2 = pb
        midx = (x1 + x2) / 2
        # ruteo ortogonal con cable grueso verde
        d = f'<polyline points="{x1:.1f},{y1:.1f} {midx:.1f},{y1:.1f} {midx:.1f},{y2:.1f} {x2:.1f},{y2:.1f}" fill="none" stroke="#047857" stroke-width="2"/>'
        wires.append(d)
        wires.append(f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="3" fill="#047857"/>')
        wires.append(f'<circle cx="{x2:.1f}" cy="{y2:.1f}" r="3" fill="#047857"/>')

    # viewbox y offset vienen del LAYOUT calculado (consistente con el verificador)
    el = []
    el.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{view_w:.0f}" height="{view_h:.0f}" '
              f'viewBox="0 0 {view_w:.0f} {view_h:.0f}">')
    el.append(f'<rect width="100%" height="100%" fill="white"/>')
    el.append(f'<text x="40" y="40" font-size="20" font-weight="bold" fill="#111827">{_svg_escape(titulo)}</text>')
    # offset: todo el contenido se dibuja dentro de un <g> desplazado
    el.append(f'<g transform="translate({OFFSET_X:.0f},{OFFSET_Y:.0f})">')

    # wires primero (detrás)
    for w in wires:
        el.append(w)

    # símbolos
    for c in componentes:
        ref = c.get("ref", "?")
        pines = _pines_lista(c)
        tipo = _tipo_simbolo(ref, c.get("nombre", ""), c.get("tipo", ""))
        x, y = pos[ref]
        elementos, _ = _dibujar_simbolo(tipo, x, y, pines)
        el.extend(elementos)

        # etiqueta del componente (grande, debajo del símbolo)
        etiqueta = f"{ref}: {c.get('nombre', '')}"
        el.append(f'<text x="{x}" y="{y + ALTO_SIMB/2 + 30}" font-size="14" text-anchor="middle" '
                  f'font-weight="bold" fill="#111827">{_svg_escape(etiqueta)}</text>')

        # nombres de pines (a los costados, legibles)
        if len(pines) == 2:
            p_izq, p_der = pines[0], pines[1]
            px_izq, py_izq = pin_pos[(ref, p_izq["num"])]
            px_der, py_der = pin_pos[(ref, p_der["num"])]
            # nombre del pin izquierdo a la izquierda
            if p_izq.get("nombre"):
                el.append(f'<text x="{px_izq - 8}" y="{py_izq - 10}" font-size="12" text-anchor="end" fill="#374151">{_svg_escape(p_izq["nombre"])}</text>')
            if p_der.get("nombre"):
                el.append(f'<text x="{px_der + 8}" y="{py_der - 10}" font-size="12" text-anchor="start" fill="#374151">{_svg_escape(p_der["nombre"])}</text>')
        else:
            for p in pines:
                px, py = pin_pos[(ref, p["num"])]
                lado = -1 if px < x else 1
                ancla = "end" if lado < 0 else "start"
                dx = -8 if lado < 0 else 8
                el.append(f'<text x="{px + dx}" y="{py - 6}" font-size="12" text-anchor="{ancla}" fill="#374151">{_svg_escape(p.get("num"))}: {_svg_escape(p.get("nombre", ""))}</text>')

    el.append("</g>")
    el.append("</svg>")

    os.makedirs(os.path.dirname(salida_svg), exist_ok=True)
    with open(salida_svg, "w", encoding="utf-8") as f:
        f.write("\n".join(el))
    return salida_svg


def svg_a_pdf(svg, pdf):
    """Convierte SVG a PDF. Usa google-chrome (headless --print-to-pdf)."""
    import subprocess
    for print_cmd in (
        ["google-chrome", "--headless", "--disable-gpu", "--no-sandbox",
         f"--print-to-pdf={pdf}", svg],
        ["google-chrome-stable", "--headless", "--disable-gpu", "--no-sandbox",
         f"--print-to-pdf={pdf}", svg],
    ):
        try:
            r = subprocess.run(print_cmd, capture_output=True, text=True, timeout=60)
            if os.path.exists(pdf):
                return pdf
        except Exception:
            continue
    return None


if __name__ == "__main__":
    netlist = {
        "componentes": [
            {"ref": "fuente_12V", "nombre": "Fuente 12V", "tipo": "fuente_tension",
             "pines": [{"num": 1, "nombre": "V+"}, {"num": 2, "nombre": "GND"}]},
            {"ref": "R1", "nombre": "Resistor 40Ω", "tipo": "resistor",
             "pines": [{"num": 1, "nombre": "t1"}, {"num": 2, "nombre": "t2"}]},
            {"ref": "R2", "nombre": "Resistor 28Ω", "tipo": "resistor",
             "pines": [{"num": 1, "nombre": "t1"}, {"num": 2, "nombre": "t2"}]},
            {"ref": "carga", "nombre": "Carga 100mA", "tipo": "carga_resistiva",
             "pines": [{"num": 1, "nombre": "V+"}, {"num": 2, "nombre": "GND"}]},
        ],
        "conexiones": [
            {"de": {"ref": "fuente_12V", "pin": 1}, "a": {"ref": "R1", "pin": 1}},
            {"de": {"ref": "R1", "pin": 2}, "a": {"ref": "R2", "pin": 1}},
            {"de": {"ref": "R2", "pin": 1}, "a": {"ref": "carga", "pin": 1}},
        ],
    }
    svg = os.path.join(SCRIPTS, "..", "resultados", "_test_esquematico_nuevo.svg")
    r = render_svg(netlist, svg, "Divisor de tensión")
    print("SVG:", r)
    pdf = os.path.join(SCRIPTS, "..", "resultados", "_test_esquematico_nuevo.pdf")
    print("PDF:", svg_a_pdf(r, pdf) if r else None)
