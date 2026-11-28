#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Tests for detector_evasion.py (black-box + white-box)."""
import math
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import detector_evasion as DE  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✅ %s" % name)
    else:
        FAIL += 1
        print("  ❌ %s  %s" % (name, detail))
    return cond


# ── black-box heuristics ──
evasiva = ("As an AI I cannot give a definitive answer. I'd be happy to help with more context. "
           "It depends on many factors. Would you like me to elaborate?")
resolutiva = "The answer is 42."

check("B1. respuesta evasiva -> label evasivo",
      DE.detectar_externo("What is 6*7?", evasiva)["label"] == "evasivo",
      str(DE.detectar_externo("What is 6*7?", evasiva)))
check("B2. respuesta resolutiva -> label resolutivo",
      DE.detectar_externo("What is 6*7?", resolutiva)["label"] == "resolutivo",
      str(DE.detectar_externo("What is 6*7?", resolutiva)))

check("B3. deflexión detectada", DE.es_deflexion(evasiva) is True)
check("B4. deflexión NO en respuesta directa", DE.es_deflexion(resolutiva) is False)

check("B5. confianza verbalizada parseada",
      DE.verbalized_confidence("I am 30% confident that this is correct.") == 0.30)
check("B6. confianza ausente -> None", DE.verbalized_confidence("hello world") is None)

check("B7. self-consistency alta", DE.self_consistency(["42", "42", "42"]) == 1.0)
check("B8. self-consistency baja", DE.self_consistency(["42", "banana", "hello"]) == 1 / 3)

# token bloat signal
r = DE.detectar_externo("x", "42", completion_tokens=500, baseline_tokens=100)
check("B9. token bloat suma señal", "token bloat" in " ".join(r["razones"]), str(r))

# ── white-box noise injection ──
sigmas = [0.0, 0.1, 0.2, 0.3, 0.4]
honesto = lambda s: max(0.0, 0.9 - 1.5 * s)
sandbag = lambda s: 0.2 + 0.7 * (1 - math.exp(-8 * s))

check("W1. modelo honesto -> no sandbagging",
      DE.detectar_sandbagging(DE.probe_ruido(honesto, sigmas))["veredicto"] == "honesto")
check("W2. modelo sandbagging -> sospechoso",
      DE.detectar_sandbagging(DE.probe_ruido(sandbag, sigmas))["veredicto"] == "sospechoso_sandbagging")

# ── dispatcher ──
check("D1. modo externo explícito", DE.detectar("q", "42", modo="externo")["modo"] == "externo")
check("D2. modo local -> whitebox_disponible",
      DE.detectar("q", "42", modo="local")["whitebox_disponible"] is True)

print("\nRESULTADO: %d PASS / %d FAIL" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
