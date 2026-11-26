#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_firmante_oraculo.py — P0 de Fable: el firmante NO debe actuar como oráculo.

Fable: 'la frontera UID protege la exfiltración de la clave, no su uso. Si el runtime puede
pedirle al firmante que firme por IPC o sudo, A2 firma lo que quiera. La confirmación humana
tiene que ocurrir del lado del firmante, en una TTY que el agente no controle.'

QUÉ LO FALSARÍA:
  - Si `cmd_firmar_interactivo` firmara SIN una TTY interactiva (inyectable por pipe/sudo).
  - Si la confirmación no exigiera una respuesta humana explícita.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import cli_firma_humana as CLI  # noqa: E402

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
print("[1] P0: firmar-interactivo SIN TTY => RECHAZA (no inyectable por pipe)")


def _correr_sin_tty():
    """Ejecuta cmd_firmar_interactivo con stdin NO-tty y captura SystemExit."""
    import io
    stdin_orig = sys.stdin
    sys.stdin = io.StringIO("")  # no es tty
    try:
        CLI.cmd_firmar_interactivo("necesidad:eva:openfoam", "openfoam", '{"netlist":"R1 1 2 1k"}')
    except SystemExit as e:
        return e.code
    finally:
        sys.stdin = stdin_orig
    return None


code = _correr_sin_tty()
check("1a. sin TTY => SystemExit 4 (rechazo)", code == 4, code)

print("\n[2] P0: _es_tty(0) detecta correctamente (sin TTY => False)")
check("2a. stdin StringIO no es TTY", CLI._es_tty(0) is False)

print("\n[3] la vía de producción es 'firmar-interactivo' (exige TTY + confirmación)")
import inspect
src = inspect.getsource(CLI.cmd_firmar_interactivo)
check("3a. cmd_firmar_interactivo exige _es_tty(0) y lee de /dev/tty",
      "_es_tty(0)" in src and "/dev/tty" in src, "missing tty/confirm")

print("\n[4] P0 item 3: la vía BATCH 'firmar' NO está expuesta como comando de producción")
import io
import contextlib
stdout = io.StringIO()
main_src = inspect.getsource(CLI)
check("4a. __main__ no expone el comando 'firmar' batch (solo generar-claves + firmar-interactivo)",
      '"firmar-interactivo"' in main_src and 'elif args[0] == "firmar"' not in main_src,
      "la via batch sigue expuesta")

print("\n[5] P0 item 6: NÚCLEO ÚNICO de firma (batch e interactivo convergen a _firmar_nucleo)")
nucleo_src = inspect.getsource(CLI._firmar_nucleo)
check("5a. existe _firmar_nucleo (atador de contexto)", "_firmar_nucleo" in inspect.getsource(CLI))
check("5b. _firmar_nucleo valida contenido y ataca el contexto completo",
      "contenido_seguro" in nucleo_src and "nonce" in nucleo_src)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
