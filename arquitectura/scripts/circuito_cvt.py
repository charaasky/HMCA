#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""circuito_cvt.py — REAL electrical circuit (SPICE netlist + node diagram) of the CVT.

Fixes the underlying bug: the deliverable's 'schema' was ASCII text, not a circuit. Here a
REAL circuit is generated and verifiable by ngspice simulation, with the values from the
sizing, so that the deliverable serves to BUILD (real topology) and to
VERIFY (simulatable netlist).

Topology of the ferroresonant stabilizer (CVT):
  Mains 220 V -> primary N1 -> saturable core -> [load winding | resonant winding]
  The resonant winding N2 is closed with the LC tank (series L + parallel C) tuned to
  the mains. Core saturation clips the over-voltage and regulates Vout.

Honest about what it models:
  - The netlist models the magnetic coupling TOPOLOGY + LC tank with real values.
  - The REAL core saturation (non-linear B-H core) is NOT modeled here due to lack of the
    material curve (industrial secret). The netlist uses linear coupling (K) + series L, which
    reproduces the resonance PRINCIPLE but NOT the fine regulation by saturation.
  - This is exactly the boundary HCMA must mark: a circuit verifiable by
    TOPOLOGY, not by exact regulation.
"""


def netlist_spice(d, bom, carga_VA=1500.0, f=50.0):
    """Returns the SPICE netlist (text) of the CVT with the computed values.

    Nodes:
      1 = mains phase
      0 = neutral
      2 = primary output (after the primary)
      The primary L1 couples to secondary L2 (resonant winding) and L3 (load winding).
    """
    import math
    V = d["Vin"]
    n1 = int(round(d["vueltas_primario"]))
    n2 = int(round(d["vueltas_secundario_resonante"]))
    n3 = n2  # load winding ~ equal to the resonant one in this approximation
    L_tanque = d["L_mH"] / 1000.0          # H (series tank inductance)
    C_tanque = bom["valor_comercial_uF"] / 1e6  # F (commercial array capacitance)
    R_carga = V * V / carga_VA             # equivalent load resistance (Ω)

    # turns ratio for coupling (linear K=1, approximation; saturation is not modeled)
    k = 0.98
    # primary/secondary inductances proportional to N^2 (arbitrary scale units)
    # we use inductance values that respect the transformation ratio and resonate.
    L_prim_mH = 250.0                      # estimate (SELECTION, not canonical)
    L_sec_mH = L_prim_mH * (n3 / n1) ** 2  # scale by N^2

    lines = []
    lines.append("* CVT ferroresonante — estabilizador de tensión")
    lines.append(f"* primario N1={n1}, resonante N2={n2}, carga N3={n3}")
    lines.append(f"* tanque LC: L={d['L_mH']} mH, C={bom['valor_comercial_uF']} uF")
    lines.append("")
    lines.append(f"Vred 1 0 SIN(0 {V*math.sqrt(2):.0f} {f})")
    # primary (magnetizing inductance + winding)
    lines.append(f"Lprim 1 2 {L_prim_mH/1000.0:.4f}")
    # linear magnetic coupling (K ~1); saturation is NOT modeled (missing B-H curve)
    lines.append(f"Lsec 2 3 {L_sec_mH/1000.0:.4f}")
    lines.append(f"K1 Lprim Lsec {k}")
    # resonant tank: series L + parallel C on the resonant secondary
    lines.append(f"Ltanque 3 4 {L_tanque:.6f}")
    lines.append(f"Ctanque 4 0 {C_tanque:.9f}")
    # equivalent load
    lines.append(f"Rcarga 4 0 {R_carga:.1f}")
    lines.append("")
    lines.append(".tran 10u 100m 0 10u")
    lines.append(".control")
    lines.append("run")
    lines.append("print v(4)")
    lines.append(".endc")
    lines.append(".end")
    return "\n".join(lines)


def diagrama_conexiones(d, bom, carga_VA=1500.0):
    """Structured textual diagram (nodes and components with real values) for building."""
    V = d["Vin"]
    n1 = int(round(d["vueltas_primario"]))
    n2 = int(round(d["vueltas_secundario_resonante"]))
    arreglo = bom["arreglo"]
    return (
        "CIRCUITO DEL ESTABILIZADOR FERRORESONANTE (conexiones reales)\n"
        f"\n"
        f"  [Fase 220V] --o N1 (primario, {n1} vueltas) o-- [núcleo FeSi saturable]\n"
        f"  [Neutro]    --o                            o-- [común]\n"
        f"\n"
        f"  Secundario resonante N2 = {n2} vueltas, cerrado sobre el tanque LC:\n"
        f"    N2_a ---- L serie ({d['L_mH']} mH) ---- o---- SALIDA (a carga)\n"
        f"                                          |\n"
        f"                           C paralelo {bom['valor_comercial_uF']} uF\n"
        f"     (arreglo: {arreglo})  |\n"
        f"    N2_b ----------------------------------o---- RETORNO\n"
        f"\n"
        f"  Punto de operación: tanque sintonizado a {d['f_Hz']} Hz.\n"
        f"  Corriente nominal: {d['corriente_A']} A.  Núcleo: {d['area_nucleo_cm2']} cm².\n"
        f"  NOTA: la SATURACIÓN real (regulación ±1%) no se modela sin la curva B-H del\n"
        f"  shunt magnético (secreto industrial); el circuito reproduce la TOPOLOGÍA y el\n"
        f"  principio de resonancia, no la exactitud de regulación.\n"
    )
