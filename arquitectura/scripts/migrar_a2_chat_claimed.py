#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""A2 migration (one-time): degrades facts that the old chat wrote as verified to claimed.

Candidate: fuente='usuario' AND verified=1 AND authority=1 AND stale=0 AND meta.usuario non-empty.

Usage (with the monitor STOPPED, to respect the single-writer invariant):
    sudo systemctl stop hcma-monitor
    sudo -u hcma-monitor python3 /home/charasky/Documentos/HMC/arquitectura/scripts/migrar_a2_chat_claimed.py \
        /var/lib/hcma-monitor/store/memoria.db
    sudo systemctl start hcma-monitor

It is idempotent (a second pass degrades 0). Everything stays in the hash chain as op=degradar_chat.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else "/var/lib/hcma-monitor/store/memoria.db"
    if not os.path.exists(db):
        print(f"ERROR: no existe {db}")
        sys.exit(1)
    m = HCMAMemoriaSQLite(db, crear_esquema=False, read_only=False)
    try:
        n = m.migrar_chat_verified_a_claimed()
        print(f"Migración A2: {n} hecho(s) degradado(s) de verified a claimed.")
    finally:
        m.close()


if __name__ == "__main__":
    main()
