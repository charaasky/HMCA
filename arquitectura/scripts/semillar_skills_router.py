#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""semillar_skills_router.py — seeds minimal CANONICAL skills for the semantic router.

Writes `skill:<id>` (verified, fuente='ingeniero', meta.tipo='skill') with a DESCRIPTION that
feeds the embeddings index and `skill_block`. Idempotent (skips if already current).

Only stdlib + memoria_sqlite (does NOT use skill_lib, so it runs with the monitor stopped).

Usage (monitor STOPPED, as hcma-monitor):
    sudo systemctl stop hcma-monitor
    sudo install -m 0600 -o hcma-monitor -g hcma-monitor \
        /home/charasky/Documentos/HMC/arquitectura/scripts/semillar_skills_router.py \
        /var/lib/hcma-monitor/lib/
    sudo -u hcma-monitor python3 /var/lib/hcma-monitor/lib/semillar_skills_router.py \
        /var/lib/hcma-monitor/store/memoria.db
    sudo systemctl start hcma-monitor
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402

SKILLS = {
    "luminaria_led": "dimensionar luminaria led: potencia flujo eficacia corriente tension seccion cable (P=flujo/eficacia, I=P/V, S=I/J)",
    "cargador_bateria": "tiempo de carga de una bateria: capacidad corriente horas (t = capacidad / corriente)",
    "multimetro": "medicion de corriente por shunt: voltaje resistencia ohmios (I = Vshunt / Rshunt)",
    "ley_ohm": "ley de ohm y potencia electrica: voltaje corriente resistencia (V = I*R, P = V*I)",
    "corriente": "corriente electrica: potencia tension ley de ohm amperios voltaje",
}


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else "/var/lib/hcma-monitor/store/memoria.db"
    if not os.path.exists(db):
        print(f"ERROR: no existe {db}")
        sys.exit(1)
    m = HCMAMemoriaSQLite(db, crear_esquema=False, read_only=False)
    try:
        n = 0
        for sid, desc in SKILLS.items():
            if m.current(f"skill:{sid}") is not None:
                print(f"  ya registrado: skill:{sid}")
                continue
            m.write(f"skill:{sid}", desc, fuente="ingeniero", verified=True,
                    meta={"tipo": "skill"})
            print(f"  registrado: skill:{sid}")
            n += 1
        print(f"\nSkills sembrados: {n} (total canónicos: {len(SKILLS)})")
    finally:
        m.close()


if __name__ == "__main__":
    main()
