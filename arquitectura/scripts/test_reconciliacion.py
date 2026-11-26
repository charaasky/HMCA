#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_reconciliacion.py — controles positivos del ledger + reconciliación (P0 Fable).

Verifica que el detector NO es ciego (control positivo de Fable):
  - una ejecución SIN autorización => violación (defensa apagada => el detector marca >0);
  - una ejecución CON autorización válida => limpio (violaciones=0);
  - autorización consumida sin ejecución => se reporta como huérfana;
  - época distinta => no empareja (binding);
  - el ledger detecta manipulación (hash chain rota => integra=False).

Exit 0 = OK, 1 = FAIL.
"""
import os
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from ledger_ejecucion import LedgerEjecucion  # noqa: E402
from reconciliacion import reconciliar         # noqa: E402

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (("  " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


# ── ledger: append + integridad ──────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="rec_")
lg = LedgerEjecucion(os.path.join(tmp, "ejec.log"))
lg.registrar("http", "h1", "n1", 1)
lg.registrar("http", "h2", "n2", 1)
entradas, integra = lg.leer()
check("ledger: 2 entradas leídas", len(entradas) == 2)
check("ledger: cadena íntegra tras append", integra is True)

# tamper: alterar una línea del archivo => cadena rota
with open(lg.path, "r", encoding="utf-8") as f:
    lineas = f.readlines()
lineas[0] = lineas[0].replace('"n1"', '"nX"')
with open(lg.path, "w", encoding="utf-8") as f:
    f.writelines(lineas)
_, integra_rota = lg.leer()
check("ledger: detecta manipulación (cadena rota)", integra_rota is False)

# ── reconciliación: control positivo (defensa apagada => detector marca) ─────
ejec_sin_aut = [{"tool_id": "http", "hash_accion": "hX", "nonce": "nX", "epoca": 1}]
r = reconciliar(ejec_sin_aut, [], epoca_vigente=1)
check("control positivo: ejecución sin autorización => violación detectada",
      r["violaciones"] == 1 and r["ok"] is False, f"violaciones={r['violaciones']}")

# ── reconciliación: emparejado limpio ────────────────────────────────────────
ejecs = [{"tool_id": "http", "hash_accion": "h1", "nonce": "n1", "epoca": 1}]
auts = [{"tool_id": "http", "hash_accion": "h1", "nonce": "n1", "epoca": 1}]
r = reconciliar(ejecs, auts, epoca_vigente=1)
check("emparejado: violaciones=0", r["violaciones"] == 0 and r["ok"] is True)

# ── autorización consumida sin ejecución ─────────────────────────────────────
r = reconciliar([], auts, epoca_vigente=1)
check("autorización sin ejecución => huérfana reportada",
      len(r["autorizaciones_sin_ejecucion"]) == 1)

# ── época distinta => no empareja (binding) ──────────────────────────────────
ejec_epoca2 = [{"tool_id": "http", "hash_accion": "h1", "nonce": "n1", "epoca": 2}]
r = reconciliar(ejec_epoca2, auts, epoca_vigente=1)
check("época distinta => no empareja (violación)",
      r["violaciones"] == 1, f"violaciones={r['violaciones']}")

# ── replay: mismo nonce dos veces => la segunda es huérfana ──────────────────
ejec_replay = [
    {"tool_id": "http", "hash_accion": "h1", "nonce": "n1", "epoca": 1},
    {"tool_id": "http", "hash_accion": "h1", "nonce": "n1", "epoca": 1},
]
r = reconciliar(ejec_replay, auts, epoca_vigente=1)
check("replay de nonce => segunda ejecución es violación",
      r["violaciones"] == 1, f"violaciones={r['violaciones']}")

print("\n" + "=" * 74)
print(f"RESULTADO test_reconciliacion: {len(FAIL)} fallos" + ("" if not FAIL else f" -> {FAIL}"))
print("=" * 74)
sys.exit(1 if FAIL else 0)
