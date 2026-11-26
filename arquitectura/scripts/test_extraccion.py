#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Demuestra los 3 caminos de extracción."""
import json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extraccion import (extraer_ecuaciones_latex, extraer_ejemplo_resuelto,
                        verificar_ejemplo, extraer_ecuaciones_pdf)
from prototipo import calc_barra_dominadas

HERE = os.path.dirname(os.path.abspath(__file__))


def fn_beam(inputs):
    qs = calc_barra_dominadas(inputs)
    return {q["name"]: q["value"] for q in qs}


print("=" * 60)
print("CAMINO 1 — fuente LaTeX (ecuaciones exactas)")
print("=" * 60)

# a) .tex real generado por mí (motor_diseno.tex, con ecuaciones inline)
tex_real = os.path.join(HERE, "motor_diseno.tex")
eqs = extraer_ecuaciones_latex(tex_real)
print(f"\n[{tex_real}]  → {len(eqs)} ecuaciones extraídas")
for e in eqs[:6]:
    print(f"   [{e['tipo']}]  ${e['latex']}$")

# b) entornos display (equation) — sintético para probar ese caso
snippet = r"""
\section{Cálculo}
\begin{equation}
  M = \frac{P \cdot L}{4}
\end{equation}
\begin{align}
  \tau &= \frac{P}{2} \\
  W &= \frac{M}{f_b}
\end{align}
"""
with tempfile.NamedTemporaryFile("w", suffix=".tex", delete=False) as f:
    f.write(snippet)
    tmp_tex = f.name
eqs2 = extraer_ecuaciones_latex(tmp_tex)
print(f"\n[snippet display]  → {len(eqs2)} ecuaciones")
for e in eqs2:
    print(f"   [{e['tipo']}]  ${e['latex']}$")

print("\n" + "=" * 60)
print("CAMINO 2 — ejemplo resuelto → test dorado")
print("=" * 60)

ejemplo_texto = (
    "Ejemplo resuelto: viga simplemente apoyada con masa_kg=180, luz_m=2, "
    "factor_dinamico=2.0, fy_mpa=235. Resultado: momento_maximo_kNm=1.765, "
    "seccion_requerida_cm3=11.38."
)
ej = extraer_ejemplo_resuelto(ejemplo_texto)
print("\nExtraído del texto:")
print(json.dumps(ej, ensure_ascii=False, indent=2))

ok, detalle = verificar_ejemplo(fn_beam, ej)
print(f"\nVerificación (fórmula determinista reproduce el ejemplo): {ok}")
print(f"   {detalle}")

# ejemplo con un número MAL a propósito (para ver que detecta el desacuerdo)
ej_mal = {"inputs": {"masa_kg": 180, "luz_m": 2, "factor_dinamico": 2.0, "fy_mpa": 235},
          "resultado": {"momento_maximo_kNm": 0.88}}
ok2, det2 = verificar_ejemplo(fn_beam, ej_mal)
print(f"\nControl negativo (resultado equivocado 0.88): {ok2}  →  {det2}")

print("\n" + "=" * 60)
print("CAMINO 3 — PDF con OCR matemático (+ fallback)")
print("=" * 60)

pdf = os.path.join(HERE, "documento_ingenieria.pdf")
r = extraer_ecuaciones_pdf(pdf, motor="auto")
print(f"\nMotor usado: {r.get('motor')} | calidad: {r.get('calidad')}")
if r.get("ecuaciones_candidatas"):
    print("Ecuaciones candidatas (capa de texto, baja calidad):")
    for l in r["ecuaciones_candidatas"][:6]:
        print(f"   {l.strip()[:100]}")
print(f"\nNota: {r.get('nota')}")
print("\nSi hubiera MATHPIX_APP_ID/KEY o `nougat` instalado, el motor 'auto' los usaría "
      "en lugar del fallback (interfaz ya implementada).")
