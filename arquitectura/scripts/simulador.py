#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""simulador.py — electrotechnics simulator (ngspice) as DERIVER, not source.

Implements I-SIM (POSTMORTEM_skills.md §7decies, validated by Fable):
  - ngspice is a LOCAL DETERMINISTIC process (offline, no network).
  - A sim result is a DERIVED fact, never a root. authority = min(sid, params).
  - The raw rawfile is NOT hashed: deterministic scalar METRICS are extracted.

Engine: local ngspice binary (in `.deps/ngspice/`), ASCII rawfile parsed in a simple
and stable way (without depending on the PySpice API, which changes).

Functions:
  rc_frecuencia_corte()      -> -3dB cutoff frequency of an RC (calibration)
  thd_tanque_resonante()     -> THD input vs LC-filtered output (harmonics)
The authority/grade decision is made by the HCMA layer (not this module).
"""

import os
import re
import subprocess
import tempfile
import hashlib

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
NGSPICE_BIN = os.path.join(DEP, "ngspice", "usr", "bin", "ngspice")
NGSPICE_LIB = os.path.join(DEP, "ngspice", "usr", "lib", "x86_64-linux-gnu")
NGSPICE_SCRIPTS = os.path.join(DEP, "ngspice", "usr", "share", "ngspice", "scripts")


def _entorno():
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = NGSPICE_LIB + ":" + env.get("LD_LIBRARY_PATH", "")
    env["SPICE_SCRIPTS"] = NGSPICE_SCRIPTS
    return env


def _limites_recurso():
    """Resource limits (CPU/memory/file) for the ngspice subprocess.

    Prevents a pathological netlist from consuming the machine (DoS). Used as preexec_fn.
    """
    def _apply():
        try:
            import resource as _r
            _r.setrlimit(_r.RLIMIT_CPU, (30, 30))
            _r.setrlimit(_r.RLIMIT_AS, (1 << 30, 1 << 30))       # 1 GiB
            _r.setrlimit(_r.RLIMIT_FSIZE, (256 << 20, 256 << 20))  # 256 MiB
        except Exception:
            pass
    return _apply


def hash_de(s) -> str:
    return hashlib.sha256(str(s).encode("utf-8")).hexdigest()


def version_ngspice() -> str:
    out = subprocess.run([NGSPICE_BIN, "--version"], capture_output=True, text=True,
                         env=_entorno(), timeout=30)
    lines = out.stdout.splitlines()
    return lines[1].strip() if len(lines) > 1 else "?"


def _correr_raw(netlist, timeout=60):
    """Runs ngspice -b on `netlist` in a temp dir, with SANDBOX (resource limits and
    isolated environment). Returns the temp directory."""
    tmp = tempfile.mkdtemp(prefix="ngspice_")
    cir = os.path.join(tmp, "net.cir")
    with open(cir, "w") as f:
        f.write(netlist)
    env = _entorno()
    # isolation: HOME to a temp dir (not the real home, where the key is)
    env["HOME"] = tmp
    env["TMPDIR"] = tmp
    subprocess.run([NGSPICE_BIN, "-b", cir], capture_output=True, text=True,
                   env=env, cwd=tmp, timeout=timeout,
                   preexec_fn=_limites_recurso())
    return tmp


def _buscar_raw(tmp):
    raw = os.path.join(tmp, "out.raw")
    if not os.path.exists(raw):
        for fn in os.listdir(tmp):
            if fn.endswith(".raw"):
                raw = os.path.join(tmp, fn)
                break
    if not os.path.exists(raw):
        raise RuntimeError("ngspice no produjo out.raw")
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# ASCII rawfile parsing (common to AC and TRAN)
# ─────────────────────────────────────────────────────────────────────────────
def _parsear_raw(raw_path):
    """Parses an ngspice ASCII rawfile and returns (axis_array, series, value_names).

    Format: 'Variables:' block (index, name, type; `frequency`/`time` are the axis and are
    discarded); 'Values:' block alternates a POINT line (index + axis value) followed by
    one VALUE line per variable (starts with TAB). We distinguish WITHOUT strip() by the
    initial character: point line does NOT start with tab; value line DOES.
    """
    import numpy as np
    lines = open(raw_path, "r").read().splitlines()

    value_names = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("Variables:"):
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("Values:"):
                parts = lines[i].strip().split()
                if parts and parts[0].isdigit() and len(parts) >= 2 and parts[1] != "frequency" and parts[1] != "time":
                    value_names.append(parts[1])
                i += 1
            break
        i += 1

    n = len(value_names)
    eje = []
    series = [[] for _ in value_names]
    cur = 0
    i += 1  # skip 'Values:'
    while i < len(lines):
        raw = lines[i]
        if raw == "" or raw.isspace():
            cur = 0
            i += 1
            continue
        if not raw.startswith("\t"):
            parts = raw.split()
            if len(parts) >= 2:
                # the second token is 'value' or 'value,0'
                eje.append(float(parts[1].split(",")[0]))
            cur = 0
            i += 1
            continue
        # VALUE line (starts with tab): can be 'real,imag' (AC) or 'real' (TRAN)
        vm = re.match(r"^\t([+-]?[0-9.eE+-]+)(?:,([+-]?[0-9.eE+-]+))?", raw)
        if vm and cur < n:
            re_ = float(vm.group(1))
            im_ = float(vm.group(2)) if vm.group(2) is not None else 0.0
            series[cur].append((re_, im_))
            cur += 1
        else:
            cur = 0
        i += 1

    return np.array(eje), series, value_names


def _magnitudes(series_idx):
    import numpy as np
    return np.array([(a ** 2 + b ** 2) ** 0.5 for a, b in series_idx])


# ─────────────────────────────────────────────────────────────────────────────
# Calibration: RC low-pass -> -3 dB cutoff frequency
# ─────────────────────────────────────────────────────────────────────────────
def rc_frecuencia_corte(R_ohm, C_uF, start=10.0, stop=1e6, puntos=20000, decimales=2):
    netlist = (
        "RC low-pass (calibracion)\n"
        "V1 n1 0 AC 1\n"
        f"R1 n1 n2 {R_ohm}\n"
        f"C1 n2 0 {C_uF}u\n"
        f".ac dec {puntos} {start} {stop}\n"
        ".control\nset filetype=ascii\nrun\nwrite out.raw\nquit\n.endc\n.end\n"
    )
    tmp = _correr_raw(netlist)
    raw = _buscar_raw(tmp)
    freq, series, names = _parsear_raw(raw)
    idx = names.index("v(n2)")
    vmag = _magnitudes(series[idx])
    target = 0.7071067811865476  # -3dB relative to DC (=1.0, capacitor open)
    out = None
    for k in range(len(vmag) - 1):
        if vmag[k] >= target >= vmag[k + 1]:
            f0, f1 = freq[k], freq[k + 1]
            v0, v1 = vmag[k], vmag[k + 1]
            frac = (target - v0) / (v1 - v0) if v1 != v0 else 0.0
            out = f0 + (f1 - f0) * frac
            break
    if out is None:
        out = float(freq[0])
    return round(float(out), decimales)


def resonancia_lc(L_mH, C_uF, start=10.0, stop=1e5, puntos=20000, decimales=2):
    """RESONANCE frequency of the LC tank (derived observable, not input).

    Measures by AC sweep the frequency where the voltage magnitude over the tank is MAXIMUM
    (resonance). It is the cross-check against the analytical f = 1/(2π√(LC)): if the declared
    topology (series L + parallel C) is correct, the simulated frequency matches the
    analytical one within tolerance. A C in SERIES instead of parallel does NOT resonate at that f.
    """
    import math
    L = L_mH / 1000.0
    C = C_uF / 1e6
    netlist = (
        "Tanque resonante LC (frecuencia de resonancia)\n"
        "V1 n1 0 AC 1\n"
        f"L1 n1 n2 {L}\n"
        f"C1 n2 0 {C}\n"
        f"R1 n2 0 100\n"
        f".ac dec {puntos} {start} {stop}\n"
        ".control\nset filetype=ascii\nrun\nwrite out.raw\nquit\n.endc\n.end\n"
    )
    tmp = _correr_raw(netlist)
    raw = _buscar_raw(tmp)
    freq, series, names = _parsear_raw(raw)
    idx = names.index("v(n2)")
    vmag = _magnitudes(series[idx])
    # frequency where the magnitude is maximum = resonance
    j = int(__import__("numpy").argmax(vmag))
    f_res = float(freq[j])
    f_analitico = 1.0 / (2 * math.pi * math.sqrt(L * C))
    return {
        "f_simulada_Hz": round(f_res, decimales),
        "f_analitica_Hz": round(f_analitico, decimales),
        "error_relativo": round(abs(f_res - f_analitico) / f_analitico, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# THD: harmonic reduction of the LC resonant tank (advantage of the CVT)
# ─────────────────────────────────────────────────────────────────────────────
def thd_tanque_resonante(L_mH, C_uF, f_fund=50.0, decimales=1):
    """THD input vs output filtered by the LC tank tuned to f_fund.

    Injects fundamental (311 V peak) + 3rd (10%) + 5th (5%) harmonic as SERIES sources,
    passes through series L + parallel C, and measures by FFT how much it attenuates the harmonics.

    Returns (THD_input_pct, THD_output_pct). Physics by simulation (I-SIM), not arithmetic.
    """
    import numpy as np
    L = L_mH / 1000.0
    C = C_uF / 1e6
    V_fund = 311.0
    V3 = V_fund * 0.10
    V5 = V_fund * 0.05

    netlist = (
        "Tanque resonante LC (THD)\n"
        f"Vfund 1 0 SIN(0 {V_fund} {f_fund})\n"
        f"V3    1 2 SIN(0 {V3} {3 * f_fund})\n"
        f"V5    2 3 SIN(0 {V5} {5 * f_fund})\n"
        f"L1 3 4 {L}\n"
        f"C1 4 0 {C}\n"
        f"R1 4 0 100\n"
        ".tran 5u 60m 0 5u\n"
        ".control\nset filetype=ascii\nrun\nwrite out.raw\nquit\n.endc\n.end\n"
    )
    tmp = _correr_raw(netlist)
    raw = _buscar_raw(tmp)
    freq, series, names = _parsear_raw(raw)
    dt = float(np.median(np.diff(freq)))

    def _thd(sig):
        s = np.array(sig) - np.array(sig).mean()
        X = np.fft.rfft(s * np.hanning(len(s)))
        freqs = np.fft.rfftfreq(len(s), dt)
        mag = np.abs(X)
        b = np.where((freqs >= 40) & (freqs <= 70))[0]
        fund = b[int(np.argmax(mag[b]))]
        ff = freqs[fund]
        harm = 0.0
        for k in range(2, 16):
            j = int(np.argmin(np.abs(freqs - k * ff)))
            harm += mag[j] ** 2
        return np.sqrt(harm) / mag[fund] * 100

    idx_in = names.index("v(3)")
    idx_out = names.index("v(4)")
    thd_in = _thd([a for a, _ in series[idx_in]])
    thd_out = _thd([a for a, _ in series[idx_out]])
    return round(thd_in, decimales), round(thd_out, decimales)


if __name__ == "__main__":
    import math
    fc_medido = rc_frecuencia_corte(1000.0, 1.0)
    fc_analitico = 1.0 / (2 * math.pi * 1000.0 * 1e-6)
    err = abs(fc_medido - fc_analitico) / fc_analitico * 100
    print(f"frecuencia -3dB simulada : {fc_medido} Hz")
    print(f"frecuencia -3dB analítica: {fc_analitico:.2f} Hz")
    print(f"error relativo           : {err:.2f} %")
    print(f"ngspice version          : {version_ngspice()}")

    t_in, t_out = thd_tanque_resonante(30.81, 328.8)
    print(f"\nTHD entrada (con armónicos) : {t_in}%")
    print(f"THD salida filtrada por LC  : {t_out}%")
    print(f"Reducción de armónicos      : {t_in / t_out:.1f}x")
