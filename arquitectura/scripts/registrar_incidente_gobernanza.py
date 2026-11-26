#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""registrar_incidente_gobernanza.py — governance incident due to the phantom WAL (22-nov-2026).

P2 of the Fable ruling (fable_wal_fantasma_fable.md): audit the window in which the MONITOR
operated with old `autorizacion:*` while the fail-closed control gave green. That was a
CONTROL INTEGRITY failure (the checkpoint attested bytes of the .db but operated on .db + WAL),
not an agent escape: therefore it is a GOVERNANCE incident (vector=gobernanza), not an escape.

Registers the incident and transitions it through the complete documented cycle:
  detectado → bloqueado → en_revision → parcheado → rehabilitado
  (no quarantine: there is no agent to isolate; what was "blocked" was the monitor by stopping it).

Each transition stays in the hash chain (facts_log) and the fact is `incidente:wal_fantasma_2026-11-22`
(verified=False, fuente=monitor: it is audit EVIDENCE, not canonical truth).

It is run as `hcma-monitor` (owner of the store) with the MONITOR STOPPED (single-writer), using
the library deployed at /var/lib/hcma-monitor/lib. It is NOT run against the dev store.

Usage (after `sudo systemctl stop hcma-monitor`):
  sudo -u hcma-monitor env PYTHONPATH=/var/lib/hcma-monitor/lib \
      python3 /var/lib/hcma-monitor/lib/registrar_incidente_gobernanza.py
"""
import os
import sys

STORE = os.environ.get(
    "HCMA_MONITOR_STORE", "/var/lib/hcma-monitor/store/memoria.db")

INCIDENTE_ID = "wal_fantasma_2026-11-22"


def main():
    from memoria_sqlite import HCMAMemoriaSQLite
    import incidentes as INC

    m = HCMAMemoriaSQLite(STORE)

    # idempotent: if already registered, do not retry (duplicate write raises ValueError).
    if INC.estado(m, INCIDENTE_ID) is not None:
        print(f"(ya existe) incidente {INCIDENTE_ID} en estado {INC.estado(m, INCIDENTE_ID)}")
        m.close()
        return 0

    INC.registrar(
        m,
        INCIDENTE_ID,
        modo_escape="wal_fantasma",
        vector="gobernanza",
        session_id="humano:charasky",
        evidencia={
            "ventana_desde": "2026-11-22T15:01:00Z",
            "ventana_hasta": "2026-11-22T16:28:00Z",
            "store_digest_atestiguado": "cfd76a5a38afc885a9fa491c4185864583f05bc85705a9d06e5584ce38f2b33a",
            "n_facts_esperado": 130,
            "n_facts_visto": 29,
            "wal_mtime": "2026-11-15T12:55:00Z",
            "wal_bytes": 708672,
            "sintoma": "store leída como facts=29 (autorizacion:*/necesidad:*/sim:* viejos) "
                       "en vez de 130; control fail-closed daba verde",
        },
        problema=(
            "El monitor operó durante ~1h27m con un WAL fantasma (nov-15) que pisaba los 130 "
            "facts con 29 viejos, mientras el control fail-closed (checkpoint por digest del .db) "
            "daba verde. El checkpoint atestiguaba bytes del .db pero el monitor operaba sobre "
            ".db + WAL; un -wal ajeno con magic/salts/checksums válidos no era detectado."
        ),
        detalle=(
            "Causa raíz: el cutover copió memoria.db debajo de un memoria.db-wal viejo que quedó "
            "en el directorio destino. SQLite aplica el WAL si sus encabezados son válidos, sin "
            "atarlo a 'su' .db. Mitigación aplicada: (1) cutover ahora borra -wal/-shm del destino "
            "antes de instalar y usa copia atómica; (2) el monitor (P1) verifica además el estado "
            "lógico (n_facts, n_facts_log, head_facts_log) y la integridad de la cadena facts_log."
        ),
        comando=(
            "diagnóstico: python3 -c \"import sqlite3; "
            "sqlite3.connect('file:/var/lib/hcma-monitor/store/memoria.db?mode=ro', uri=True)\" "
            "→ facts=29; copia sin WAL → facts=130"
        ),
    )

    INC.bloquear(m, INCIDENTE_ID, "humano:charasky",
                 motivo="monitor detenido para auditar la ventana de integridad")
    INC.revisar(m, INCIDENTE_ID, "humano:charasky",
                detalle="dictamen Fable 5.2 (fable_wal_fantasma_fable.md)")
    INC.parchear(m, INCIDENTE_ID, "humano:charasky",
                 mitigacion=(
                     "cutover_estado.sh borra -wal/-shm del destino antes de instalar + copia "
                     "atómica (.new + mv); hcma_monitor_daemon.verificar_checkpoint ahora valida "
                     "estado lógico (n_facts/n_facts_log/head_facts_log) y cadena facts_log; "
                     "checkpoint_cutover calcula head por seq (no por version); setgid 2750 en el "
                     "dir del store para que el WAL nuevo nazca con grupo hcma-audit."
                 ))
    INC.rehabilitar(m, INCIDENTE_ID, "humano:charasky")

    print(f"incidente registrado y rehabilitado: {INCIDENTE_ID}")
    print(f"  estado final: {INC.estado(m, INCIDENTE_ID)}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
