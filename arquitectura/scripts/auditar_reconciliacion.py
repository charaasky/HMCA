#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""auditar_reconciliacion.py — POINT-IN-TIME audit (sudo, read-only) of execution ↔ authorization.

Option A (Fable ruling `fable_dashboard_fable.md`): the audit is not a live panel; it is a
point-in-time read with privileges over TWO append-only ledgers of TWO different UIDs, which the agent
(101) cannot touch:

  - ejecuciones   : /var/lib/hcma-proxy/ledger/ejecucion.log   (UID 997, proxy)
  - autorizaciones: /var/lib/hcma-monitor/nonces.log            (UID 999, monitor)

Reconciles: every risk>=1 execution must match a consumed nonce (valid authorization).
An orphan in either direction is a finding. Reports chain integrity of both ledgers.

Usage:  sudo python3 auditar_reconciliacion.py [ejecuciones.log] [nonces.log]
Exit 0 = no violations or broken chains · 1 = findings.
"""
import hashlib
import json
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from ledger_ejecucion import LedgerEjecucion  # noqa: E402
from reconciliacion import reconciliar          # noqa: E402

EJEC_DEF = "/var/lib/hcma-proxy/ledger/ejecucion.log"
NONCE_DEF = "/var/lib/hcma-monitor/nonces.log"

HALLAZGOS = []


def hallazgo(msg):
    print("  ❌ " + msg)
    HALLAZGOS.append(msg)


def ok(msg):
    print("  ✅ " + msg)


def _hash_nonce_linea(prev, nonce, tool_id, hash_accion, ts, epoca):
    canon = json.dumps({"prev": prev, "nonce": nonce, "tool_id": tool_id,
                        "hash_accion": hash_accion, "ts": ts, "epoca": epoca},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def leer_nonces(path):
    """Reads the nonces log (hash-chained JSONL) and returns (registros, integra)."""
    if not os.path.exists(path):
        return [], False  # absent => finding (the fail-closed monitor would block it)
    registros = []
    prev = ""
    integra = True
    for i, linea in enumerate(open(path, encoding="utf-8")):
        linea = linea.strip()
        if not linea:
            continue
        try:
            rec = json.loads(linea)
        except json.JSONDecodeError:
            integra = False
            continue
        esperado = _hash_nonce_linea(prev, rec.get("nonce", ""), rec.get("tool_id", ""),
                                     rec.get("hash_accion", ""), rec.get("ts", ""),
                                     rec.get("epoca", 0))
        if rec.get("hash") != esperado or rec.get("prev", "") != prev:
            integra = False
        registros.append({"tool_id": rec.get("tool_id", ""),
                          "hash_accion": rec.get("hash_accion", ""),
                          "nonce": rec.get("nonce", ""),
                          "epoca": rec.get("epoca", 0)})
        prev = rec.get("hash", "")
    return registros, integra


def main():
    ej = sys.argv[1] if len(sys.argv) > 1 else EJEC_DEF
    no = sys.argv[2] if len(sys.argv) > 2 else NONCE_DEF

    print("=" * 74)
    print("AUDITORÍA DE RECONCILIACIÓN (ejecución ↔ autorización)")
    print("=" * 74)

    # 1. execution ledger (proxy)
    try:
        entradas, integra_ej = LedgerEjecucion(ej).leer()
    except OSError as e:
        hallazgo(f"no se puede leer el ledger de ejecuciones (¿corriste con sudo?): {e}")
        sys.exit(1)
    if integra_ej:
        ok(f"ledger de ejecuciones íntegro ({len(entradas)} entradas)")
    else:
        hallazgo("ledger de ejecuciones con cadena ROTA o truncado")

    # 2. nonces log (monitor)
    try:
        nonces, integra_no = leer_nonces(no)
    except OSError as e:
        hallazgo(f"no se puede leer el log de nonces (¿sudo?): {e}")
        sys.exit(1)
    if os.path.exists(no) and integra_no:
        ok(f"log de nonces íntegro ({len(nonces)} autorizaciones consumidas)")
    elif not os.path.exists(no):
        hallazgo("log de nonces AUSENTE (el monitor no debería estar aceptando autorizaciones)")
    else:
        hallazgo("log de nonces con cadena ROTA o truncado")

    # 3. reconciliation
    ejecs = [{"tool_id": e["tool_id"], "hash_accion": e["hash_accion"],
              "nonce": e["nonce"], "epoca": e["epoca"]} for e in entradas]
    r = reconciliar(ejecs, nonces)
    print("\n  ejecuciones:", r["n_ejecuciones"],
          "| autorizaciones:", r["n_autorizaciones"],
          "| emparejadas:", r["emparejadas"])
    if r["violaciones"] == 0:
        ok("0 ejecuciones sin autorización válida")
    else:
        hallazgo(f"{r['violaciones']} ejecución(es) SIN autorización válida (P0)")
        for x in r["ejecuciones_sin_autorizacion"][:10]:
            hallazgo(f"    - tool={x.get('tool_id')} nonce={x.get('nonce')!r} "
                     f"hash={x.get('hash_accion', '')[:12]}…")
    if r["autorizaciones_sin_ejecucion"]:
        hallazgo(f"{len(r['autorizaciones_sin_ejecucion'])} autorización(es) consumida(s) sin "
                 f"ejecución observada (a explicar)")

    print("\n" + "=" * 74)
    if HALLAZGOS:
        print(f"RESULTADO: {len(HALLAZGOS)} hallazgos")
        print("=" * 74)
        sys.exit(1)
    print("RESULTADO: limpio (sin violaciones ni cadenas rotas)")
    print("=" * 74)
    sys.exit(0)


if __name__ == "__main__":
    main()
