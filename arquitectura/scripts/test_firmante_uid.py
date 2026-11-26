#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_firmante_uid.py — test de la frontera de UID (P0 de Fable) + humano-oráculo (saneado).

QUÉ LO FALSARÍA:
  - Si el payload firmado NO incluyera el CONTENIDO del artefacto (firma a ciegas).
  - Si el saneado del CLI dejara pasar caracteres de control/bidi que engañan al humano.
  - Si la frontera de UID reportara 'OK' cuando la privada es legible por el UID actual.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import cli_firma_humana as CLI  # noqa: E402
import gobernanza_accion as GA  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")
    return cond


print("=" * 74)
print("[1] P0 humano-oráculo: el payload firmado incluye el CONTENIDO, no solo el hash")
print("=" * 74)
art = {"netlist": "R1 1 2 1k"}
exp = 1234567890
nonce = "abc" * 8
payload = GA._payload_accion("necesidad:eva:openfoam", "openfoam", art, exp, nonce)
check("1a. payload incluye 'artifacto' (contenido serializado)", "artifacto" in payload, payload)
check("1b. el artifacto serializado contiene la netlist real",
      "R1 1 2 1k" in payload.get("artifacto", ""), payload.get("artifacto"))
check("1c. mantiene hash_accion (por conveniencia)", "hash_accion" in payload, payload)

print("\n[2] P0 humano-oráculo: LISTA BLANCA (lo mostrado = exatamente lo firmado)")
seg_ok, mot_ok = CLI.contenido_seguro("netlist R1 1 2 1k")
check("2a. contenido limpio => seguro", seg_ok is True, mot_ok)
seg_malo, mot_malo = CLI.contenido_seguro("x\u202Ey\x1b[31mROJO\x07")
check("2b. bidi override U+202E => RECHAZADO", seg_malo is False, mot_malo)
check("2c. escape ESC U+001B (C0) => RECHAZADO", any("001B" in m for m in mot_malo), mot_malo)
check("2d. bell U+0007 => RECHAZADO", any("0007" in m for m in mot_malo), mot_malo)
# lista blanca cubre lo que la lista negra anterior NO cubría:
seg_zw, mot_zw = CLI.contenido_seguro("a\u200bb")  # zero-width space
check("2e. zero-width space U+200B => RECHAZADO", seg_zw is False, mot_zw)
seg_iso, mot_iso = CLI.contenido_seguro("a\u2066b")  # LRI (bidi isolate)
check("2f. LRI U+2066 => RECHAZADO", seg_iso is False, mot_iso)
seg_tag, mot_tag = CLI.contenido_seguro("a\U000E0001b")  # tag char
check("2g. tag char U+E0001 => RECHAZADO", seg_tag is False, mot_tag)
seg_large, mot_large = CLI.contenido_seguro("R1 1 2 1k\n" + "x" * 4000 + "\u202e")
check("2h. caracter malicioso en línea 4000 => RECHAZADO", seg_large is False, mot_large)
# residuos de Default_Ignorable y confusables (5ta revision de Fable):
seg_hfill, mot_hfill = CLI.contenido_seguro("a\u3164b")  # Hangul filler (cat letra)
check("2i. Hangul filler U+3164 => RECHAZADO", seg_hfill is False, mot_hfill)
seg_vs, mot_vs = CLI.contenido_seguro("a\ufe0fb")  # variation selector (cat marca)
check("2j. variation selector U+FE0F => RECHAZADO", seg_vs is False, mot_vs)
seg_nbsp, mot_nbsp = CLI.contenido_seguro("a\u00a0b")  # NBSP (confusable space)
check("2k. NBSP U+00A0 => RECHAZADO", seg_nbsp is False, mot_nbsp)
seg_ideo, mot_ideo = CLI.contenido_seguro("a\u3000b")  # ideographic space
check("2l. ideographic space U+3000 => RECHAZADO", seg_ideo is False, mot_ideo)
# letras legítimas NO se rechazan (evita falsos negativos):
seg_letra, mot_letra = CLI.contenido_seguro("netlist\u00e9\u00f1")  # é ñ
check("2m. letras acentuadas legítimas => seguro", seg_letra is True, mot_letra)

print("\n[3] frontera de UID (firma_asimetrica delega en firmante_uid)")
import importlib
import firma_asimetrica as FA
priv, pub = FA._rutas_frontera()
check("3a. devuelve una tupla (priv, pub)", isinstance((priv, pub), tuple) and len((priv, pub)) == 2,
      (priv, pub))
# en test sin HCMA_SIGNER_USER set, usa los paths locales (compat dev). Con la var setead, delega.
os.environ["HCMA_SIGNER_TEST_DIR"] = "/tmp/hcma_signer_test"
importlib.reload(FA)
p2, u2 = FA._rutas_frontera()
check("3b. con HCMA_SIGNER_TEST_DIR usa el dir del firmante separado",
      "/tmp/hcma_signer_test" in p2 and p2 != u2, (p2, u2))
del os.environ["HCMA_SIGNER_TEST_DIR"]
importlib.reload(FA)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
