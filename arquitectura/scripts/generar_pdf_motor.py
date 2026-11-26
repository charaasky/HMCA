#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Generates the motor engineering PDF: specifications, dimensions, bill of materials and calculations.

Produces a complete document (not loose data), as an engineer would:
specifications, main dimensions, bill of materials (parts list), and the calculations
of power/torque/current, winding (copper wire) and bushing (PV).
"""
import math, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
G = 9.80665
RHO_CU = 0.0171     # Ω·mm²/m
DENS_CU = 0.00896   # kg per (mm² × m)
RHO_STEEL = 7850.0  # kg/m³

# ── Design point ────────────────────────────────────────────────────────────
P_KW = 1.5
RPM = 1500.0
V = 220.0
ETA = 0.9
J = 4.0          # A/mm² (current density)
L_ALAMBRE_M = 50.0
DIAM_EJE_MM = 24.0
CARGA_RADIAL_N = 500.0
LONG_BUJE_MM = 25.0


def torque(P_w, rpm):
    return P_w * 60.0 / (2.0 * math.pi * rpm)


# ── Calculations (deterministic, no LLM) ──────────────────────────────────────────
P_W = P_KW * 1000.0
TAU = torque(P_W, RPM)
I = P_W / (ETA * V)

A_CABLE = I / J
D_CABLE = math.sqrt(4.0 * A_CABLE / math.pi)
R_BOBINA = RHO_CU * L_ALAMBRE_M / A_CABLE
M_CU = A_CABLE * L_ALAMBRE_M * DENS_CU

P_BUJE = CARGA_RADIAL_N / (DIAM_EJE_MM * LONG_BUJE_MM)
V_BUJE = math.pi * (DIAM_EJE_MM / 1000.0) * RPM / 60.0
PV = P_BUJE * V_BUJE

# Shaft: weight (Ø24 × 200 mm)
L_EJE_M = 0.2
VOL_EJE = math.pi / 4.0 * (DIAM_EJE_MM / 1000.0) ** 2 * L_EJE_M
M_EJE = VOL_EJE * RHO_STEEL

FRAME = "90L"
H_MM = 90
L_TOTAL_MM = 330
RODAMIENTO = "6204-2Z (Ø20/47 × 14 mm)"

# ── Bill of materials (parts list) ─────────────────────────────────────────────
DESPIECE = [
    ("Carcasa", "fundición de hierro", 1, f"H={H_MM} mm, L≈{L_TOTAL_MM} mm", "~6.0"),
    ("Núcleo del estator", "chapa acero al silicio M400-50A", 1, "paquete laminado", "~5.0"),
    ("Bobinado del estator", "alambre de cobre esmaltado", 1,
     f"A={A_CABLE:.3f} mm², L={L_ALAMBRE_M:.0f} m", f"{M_CU:.2f}"),
    ("Núcleo del rotor + jaula", "chapa + aluminio inyectado", 1, "paquete laminado", "~3.0"),
    ("Eje", "acero C45 (AISI 1045)", 1, f"Ø{DIAM_EJE_MM} × {L_EJE_M*1000:.0f} mm", f"{M_EJE:.2f}"),
    ("Rodamientos", "acero, sello 2Z", 2, RODAMIENTO, "0.10 c/u"),
    ("Tapas (escudos)", "fundición", 2, f"Ø aprox {H_MM+60} mm", "~1.5 c/u"),
    ("Ventilador", "polipropileno", 1, "—", "~0.10"),
    ("Tapa de ventilador", "chapa de acero", 1, "—", "~0.30"),
    ("Caja de bornes", "plástico / baquelita", 1, "—", "~0.20"),
    ("Placa de características", "aluminio", 1, "—", "—"),
    ("Tornillería", "acero", "kit", "M5–M8", "~0.10"),
]


def esc(s):
    s = str(s)
    s = (s.replace("≈", " aprox. ")
          .replace("—", "-").replace("–", "-")
          .replace("&", r"\&").replace("%", r"\%").replace("#", r"\#")
          .replace("_", r"\_").replace("$", r"\$"))
    return s


def f(x, n=2):
    return f"{x:.{n}f}"


TEX = r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{geometry}
\geometry{margin=2.2cm}
\setlength{\parindent}{0pt}
\setlength{\parskip}{6pt}
\usepackage{booktabs}
\usepackage{hyperref}
\hypersetup{colorlinks=true,urlcolor=blue}

\title{\vspace{-1.2cm}Diseño de motor eléctrico — memoria de cálculo y despiece\\[2mm]
\large Motor asíncrono jaula de ardilla, 1.5 kW · 1500 rpm}
\author{Mauro Javier Silva}
\date{2026-11-03}

\begin{document}
\maketitle

\section*{1. Especificaciones}

\begin{table}[h]
\centering
\begin{tabular}{ll}
\toprule
Parámetro & Valor \\
\midrule
Potencia nominal & <<P_KW>> kW \\
Velocidad nominal (4 polos) & <<RPM>> rpm \\
Par nominal & <<TAU>> N·m \\
Tensión de alimentación & <<V>> V \\
Eficiencia & <<ETA>> \\
Corriente nominal & <<I>> A \\
Grado de protección & IP55 \\
Clase de aislación & F \\
\bottomrule
\end{tabular}
\end{table}

\section*{2. Dimensiones principales}

\begin{table}[h]
\centering
\begin{tabular}{ll}
\toprule
Dimensión & Valor \\
\midrule
Tamaño de carcasa (IEC) & <<FRAME>> \\
Altura de eje (H) & <<H_MM>> mm \\
Longitud total aprox. & <<L_TOTAL_MM>> mm \\
Diámetro del eje & <<DIAM_EJE_MM>> mm \\
Rodamiento & <<RODAMIENTO>> \\
\bottomrule
\end{tabular}
\end{table}

\section*{3. Despiece (lista de materiales)}

\begin{table}[h]
\centering
\small
\begin{tabular}{c p{2.7cm} p{3.1cm} c p{3.6cm} r}
\toprule
\# & Componente & Material & Cant. & Dimensiones & Peso (kg) \\
\midrule
<<DESPIECE_ROWS>>
\bottomrule
\end{tabular}
\end{table}

Peso total estimado: $\approx$ 17--18 kg (coherente con motores comerciales de 1.5 kW).

\section*{4. Cálculos}

\subsection*{4.1 Potencia, par y corriente}

\begin{itemize}
  \item Potencia: $P = \eta\,V\,I = <<ETA>> \times <<V>> \times <<I>> = <<P_W>>$ W.
  \item Par: $\tau = P\,60 / (2\pi n) = <<TAU>>$ N·m.
  \item Corriente: $I = P / (\eta\,V) = <<I>>$ A.
\end{itemize}

\subsection*{4.2 Bobinado (alambre de cobre)}

\begin{itemize}
  \item Sección del alambre: $A = I/J = <<I>> / <<J>> = <<A_CABLE>>$ mm² (diámetro $\approx$ <<D_CABLE>> mm).
  \item Resistencia: $R = \rho L/A = <<R_BOBINA>>\,\Omega$.
  \item Peso del cobre: $m = A\,L\,\rho_{Cu} = <<M_CU>>$ kg.
\end{itemize}

\subsection*{4.3 Buje (verificación PV)}

\begin{itemize}
  \item Presión: $p = W/(d\,L) = <<P_BUJE>>$ MPa.
  \item Velocidad superficial: $v = \pi\,d\,n/60 = <<V_BUJE>>$ m/s.
  \item $PV = <<PV>>$ MPa·m/s (límite bronce autolubricado $\approx 1.8$ MPa·m/s): \textbf{<<PV_OK>>}.
\end{itemize}

\section*{5. Supuestos y notas}

\begin{itemize}
  \item Corriente calculada para alimentación monofásica ($I = P/(\eta V)$); para trifásica usar
        $I = P/(\sqrt{3}\,V\,\cos\varphi\,\eta)$.
  \item Los pesos del despiece marcados con ``$\sim$'' son estimaciones de orden de magnitud; los
        del bobinado y el eje son calculados.
  \item \textbf{No se verifica aquí:} térmica, aislamiento, par de arranque, ventilación,
        resistencia mecánica del eje a fatiga, ni fijación/anclaje. Requieren revisión adicional.
  \item Dimensiones de carcasa según serie IEC (tamaño 90); verificar contra catálogo del fabricante.
\end{itemize}

\end{document}
"""


def main():
    rows = []
    for i, (comp, mat, cant, dims, peso) in enumerate(DESPIECE, 1):
        rows.append(f"{i} & {esc(comp)} & {esc(mat)} & {cant} & {esc(dims)} & {esc(peso)} \\\\")
    despiece_rows = "\n".join(rows)

    tex = (TEX
           .replace("<<P_KW>>", f(P_KW, 1))
           .replace("<<RPM>>", f(RPM, 0))
           .replace("<<TAU>>", f(TAU, 2))
           .replace("<<V>>", f(V, 0))
           .replace("<<ETA>>", f(ETA, 2))
           .replace("<<I>>", f(I, 2))
           .replace("<<FRAME>>", FRAME)
           .replace("<<H_MM>>", str(H_MM))
           .replace("<<L_TOTAL_MM>>", str(L_TOTAL_MM))
           .replace("<<DIAM_EJE_MM>>", str(DIAM_EJE_MM))
           .replace("<<RODAMIENTO>>", esc(RODAMIENTO))
           .replace("<<DESPIECE_ROWS>>", despiece_rows)
           .replace("<<P_W>>", f(P_W, 0))
           .replace("<<J>>", f(J, 1))
           .replace("<<A_CABLE>>", f(A_CABLE, 3))
           .replace("<<D_CABLE>>", f(D_CABLE, 2))
           .replace("<<R_BOBINA>>", f(R_BOBINA, 3))
           .replace("<<M_CU>>", f(M_CU, 3))
           .replace("<<P_BUJE>>", f(P_BUJE, 3))
           .replace("<<V_BUJE>>", f(V_BUJE, 3))
           .replace("<<PV>>", f(PV, 3))
           .replace("<<PV_OK>>", "OK" if PV <= 1.8 else "EXCEDE EL LÍMITE"))

    texfile = os.path.join(HERE, "motor_diseno.tex")
    open(texfile, "w", encoding="utf-8").write(tex)

    for _ in range(2):
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
                        texfile], cwd=HERE, check=False)
    pdf = texfile.replace(".tex", ".pdf")
    print("PDF generado:", pdf, "->", os.path.exists(pdf))


if __name__ == "__main__":
    main()
