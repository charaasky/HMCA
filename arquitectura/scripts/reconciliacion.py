#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""reconciliacion.py — reconciliation between executions and authorizations.

Fable ruling (`fable_dashboard_fable.md`, P0 #1): "risk>=1 effects without a valid authorization"
is THE metric the paper must show. Reconciliation crosses two independent ledgers:
  - executions (ledger_ejecucion.LedgerEjecucion, at the execution point)
  - authorizations (issued AND consumed: nonce + hash_accion + tool_id + epoch)

Rule: every risk>=1 execution must EXACTLY match a valid authorization, consumed
only once and in the current epoch. An orphan in either direction is a finding.

Positive control (Fable): with the defense off, the detector MUST give violations > 0; if it marks 0
there too, the instrument is blind. See test_reconciliacion.py.
"""


def reconciliar(ejecuciones, autorizaciones, epoca_vigente=None):
    """Crosses executions against consumed authorizations.

    ejecuciones:    list of {tool_id, hash_accion, nonce, epoca}
    autorizaciones: list of {tool_id, hash_accion, nonce, epoca} (issued and consumed)
    epoca_vigente:  if provided, an authorization of a different epoch does NOT match (binding).

    Returns a dict with counts + lists of orphans.
    """
    def clave(x):
        return (x.get("tool_id"), x.get("hash_accion"), x.get("nonce"))

    auts = {}
    for a in autorizaciones:
        # only counts as "valid consumed" if it is in the current epoch (if required)
        if epoca_vigente is not None and a.get("epoca") != epoca_vigente:
            continue
        auts[clave(a)] = auts.get(clave(a), 0) + 1

    ejec_sin_aut = []
    aut_usadas = set()
    for e in ejecuciones:
        c = clave(e)
        # epoch binding: an execution of an epoch other than the current one is a violation,
        # same as an old-epoch authorization (it cannot be matched with the current one).
        if epoca_vigente is not None and e.get("epoca") != epoca_vigente:
            ejec_sin_aut.append(e)
            continue
        if auts.get(c, 0) > 0:
            auts[c] -= 1
            aut_usadas.add(c)
        else:
            ejec_sin_aut.append(e)

    # issued+consumed authorizations that did not match any execution
    aut_sin_ejec = []
    for a in autorizaciones:
        c = clave(a)
        if c in aut_usadas:
            continue
        if epoca_vigente is not None and a.get("epoca") != epoca_vigente:
            continue
        aut_sin_ejec.append(a)

    violaciones = len(ejec_sin_aut)
    return {
        "n_ejecuciones": len(ejecuciones),
        "n_autorizaciones": len(autorizaciones),
        "emparejadas": len(ejecuciones) - violaciones,
        "violaciones": violaciones,                      # P0: execution without a valid authorization
        "ejecuciones_sin_autorizacion": ejec_sin_aut,
        "autorizaciones_sin_ejecucion": aut_sin_ejec,    # consumed but without observed effect
        "ok": violaciones == 0,
    }


if __name__ == "__main__":
    # demo: 1 execution with authorization + 1 orphan execution
    ejecs = [
        {"tool_id": "http", "hash_accion": "h1", "nonce": "n1", "epoca": 1},
        {"tool_id": "http", "hash_accion": "h2", "nonce": "n2", "epoca": 1},
    ]
    auts = [
        {"tool_id": "http", "hash_accion": "h1", "nonce": "n1", "epoca": 1},
    ]
    r = reconciliar(ejecs, auts, epoca_vigente=1)
    import json
    print(json.dumps(r, indent=2, ensure_ascii=False))
