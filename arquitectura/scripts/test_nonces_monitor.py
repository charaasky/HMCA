#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_nonces_monitor.py — registro de nonces append-only, fail-closed, fuera del store.

P0 de Fable (6ta revisión + fable_nonce_fable.md): el nonce consumido vive en un log monótono,
propiedad del monitor, fuera del store versionado. Un restore del store NO lo reverte.

QUÉ LO FALSARÍA:
  - Si un nonce ya consumido se pudiera re-consumir (replay).
  - Si el log corrupto/cadena rota abriera sin abortar (fail-closed).
  - Si el log ausente se tratara como 'vacío válido' en producción.
"""
import os
import sys
import tempfile

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import nonces_monitor as NM  # noqa: E402

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


tmp = tempfile.mkdtemp(prefix="nonces_test_")
PATH = os.path.join(tmp, "nonces.log")

print("=" * 74)
print("[1] consumo monótono + replay")
r = NM.RegistroNonces(path=PATH)
r.abrir(fail_closed=False)
check("1a. consumir abc => True", r.consumir("abc", "openfoam", "h1") is True)
check("1b. consumir abc de nuevo => False (replay)", r.consumir("abc", "openfoam", "h1") is False)
check("1c. consumir def => True", r.consumir("def", "openfoam", "h2") is True)

print("\n[2] persistencia + cadena: reabrir recupera los consumidos")
r2 = NM.RegistroNonces(path=PATH)
r2.abrir(fail_closed=True)
check("2a. abc consumido tras reabrir", r2.consumido("abc") is True)
check("2b. def consumido tras reabrir", r2.consumido("def") is True)
check("2c. xyz NO consumido", r2.consumido("xyz") is False)

print("\n[3] fail-closed: log ausente en producción ABORTA")
r3 = NM.RegistroNonces(path=os.path.join(tmp, "no_existe.log"))
try:
    r3.abrir(fail_closed=True)
    check("3a. log ausente => NonceLogError", False, "no abortó")
except NM.NonceLogError:
    check("3a. log ausente => NonceLogError", True)

print("\n[4] fail-closed: cadena rota ABORTA")
with open(PATH, "a", encoding="utf-8") as f:
    f.write('{"nonce":"corrupto","hash":"falso_que_no_verifica"}\n')
r4 = NM.RegistroNonces(path=PATH)
try:
    r4.abrir(fail_closed=True)
    check("4a. cadena rota => NonceLogError", False, "no abortó")
except NM.NonceLogError:
    check("4a. cadena rota => NonceLogError", True)

print("\n[5] fail-closed: TRUNCADO DE COLA (borrar ultimas lineas) => ABORTA")
# creamos un log limpio con 2 nonces validos, y su ancla de cabeza
PATH2 = os.path.join(tmp, "nonces2.log")
r5 = NM.RegistroNonces(path=PATH2)
r5.abrir(fail_closed=False)
r5.consumir("a", "openfoam", "h1")
r5.consumir("b", "openfoam", "h2")
# el ancla de cabeza ahora refleja la ultima (b). Truncamos la cola (borramos b).
with open(PATH2, "r", encoding="utf-8") as f:
    lineas = f.read().splitlines()
with open(PATH2, "w", encoding="utf-8") as f:
    f.write(lineas[0] + "\n")  # solo la primera linea (a)
r6 = NM.RegistroNonces(path=PATH2)
try:
    r6.abrir(fail_closed=True)
    check("5a. log truncado (cola borrada) => NonceLogError", False, "no abortó")
except NM.NonceLogError:
    check("5a. log truncado (cola borrada) => NonceLogError", True)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
