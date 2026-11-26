#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""backfill_alias.py — backfill of alias edges from the legacy format (Fable ruling 5.2).

Usage:
  # 1) Detect candidates (read-only, does not write):
  python3 backfill_alias.py --detect --db /var/lib/hcma-monitor/store/memoria.db

  # 2) Confirm edges (sends the crear_alias op to the monitor, the only writer):
  python3 backfill_alias.py --confirm guiya=perra --confirm luna=perra --retire

The confirmed target is written via `crear_alias` with source 'usuario' (human confirmation),
so the edge becomes verified and `resolve_alias` resolves the surface again. With `--retire`
the legacy namespace (bare S and S:*) is also retired.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import alias_backfill as AB  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402


def _monitor_pedir(op):
    import socket
    import monitor_ipc as IPC
    sock = os.environ.get("HCMA_MONITOR_SOCK", "/run/hcma/gobernanza.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10.0)
    s.connect(sock)
    try:
        cli = IPC.ClienteMonitor(sock=s)
        return cli.pedir(op)
    finally:
        s.close()


def _parse_confirmaciones(confirms):
    """Converts ['guiya=perra', ...] into {surface: target}. Rejects entries without '='."""
    out = {}
    for c in confirms:
        if "=" not in c:
            print(f"uso incorrecto: '{c}' (esperado surface=target)", file=sys.stderr)
            return None
        s, t = c.split("=", 1)
        s, t = s.strip(), t.strip()
        if not s or not t:
            print(f"uso incorrecto: '{c}' (surface y target no vacíos)", file=sys.stderr)
            return None
        out[s] = t
    return out


def main(argv):
    p = argparse.ArgumentParser(description="backfill de aristas alias (formato legacy)")
    p.add_argument("--detect", action="store_true", help="detectar candidatos (solo lectura)")
    p.add_argument("--retire", action="store_true", help="retirar namespace legacy tras confirmar")
    p.add_argument("--db", default="/var/lib/hcma-monitor/store/memoria.db",
                   help="ruta de la store (default producción)")
    p.add_argument("--confirm", action="append", default=[], metavar="surface=target",
                   help="arista a materializar (repetible)")
    a = p.parse_args(argv[1:])

    if a.detect:
        m = HCMAMemoriaSQLite(a.db, crear_esquema=False, read_only=True)
        try:
            cands = AB.detectar_aliases_legacy(m)
            por_nombre = AB.detectar_alias_por_nombre(m)
        finally:
            m.close()
        print("Candidatos de alias (formato legacy):")
        if not cands and not por_nombre:
            print("  (ninguno)")
        for c in cands:
            tag = "CONFLICTO -> confirmar a mano" if c["conflicto"] else "unánime"
            print(f"  surface={c['surface']!r} target_mayoritario={c['target_mayoritario']!r} "
                  f"votos={c['votos']} [{tag}]")
            for f in c["formas"]:
                print(f"      - {f}")
        if por_nombre:
            print("Candidatos por nombre (E:nombre = V):")
            for c in por_nombre:
                print(f"  alias:{c['surface']!r} -> {c['target']!r}  (origen {c['origen']})")
        print("\nConfirmar con: python3 backfill_alias.py --confirm surface=target [--retire]")
        return 0

    # alias edges (human-confirmed): written via crear_alias (there are no prop:* edges).
    confirmaciones = _parse_confirmaciones(a.confirm)
    if confirmaciones is None:
        return 2
    if not confirmaciones:
        print("nada que hacer. usá --detect o --confirm surface=target.", file=sys.stderr)
        return 2

    for s, t in confirmaciones.items():
        r = _monitor_pedir({"tipo": "crear_alias", "surface": s, "target": t,
                            "fuente": "usuario", "verified": True})
        print(f"alias:{s} -> {t}: {r}")

    # P0.3: retire the legacy namespace of each confirmed surface (via monitor).
    if a.retire:
        m = HCMAMemoriaSQLite(a.db, crear_esquema=False, read_only=True)
        try:
            claves = []
            for s in confirmaciones:
                claves += [r["key"] for r in m.conn.execute(
                    "SELECT key FROM facts WHERE (key=? OR key LIKE ?) AND authority=1 AND stale=0",
                    (s, s + ":%")).fetchall()]
        finally:
            m.close()
        for k in claves:
            r = _monitor_pedir({"tipo": "retirar", "key": k, "motivo": "superseded por alias"})
            print(f"retirar {k}: {r}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
