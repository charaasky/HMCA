#!/usr/bin/env python3
"""Genera la política SIN firmar de px-motor (luego `policygen -sign` la firma).

Configurable por variables de entorno (idénticas a las que usa deploy.sh):
  PXMOTOR_CGROUP          cgroup systemd del sujeto (default: system.slice/hcma-firecracker.service)
  PXMOTOR_UID             UID del sujeto (default: 995, hcma-firecracker)
  PXMOTOR_EXE             binario del sujeto (default: firecracker)
  PXMOTOR_ALLOW           binarios auxiliares permitidos, separados por coma (default: python3)
  PXMOTOR_MODE            alert | kill (default: alert)
  PXMOTOR_OUTPUT          ruta del JSONL de alertas (default: /var/lib/px-motor/alerts.jsonl)
  PXMOTOR_STATE           archivo de anti-rollback con el serial aceptado (default: /var/lib/px-motor/policy.serial)
  PXMOTOR_PIN_FINGERPRINT 1 para atar la política a ESTA máquina (default: 1)
  PXMOTOR_VALID_DAYS      días de validez de not_after (default: 365)
  PXMOTOR_BIN             ruta del binario px-motor (para -fingerprint; default: /usr/local/bin/px-motor)

El serial es MONÓTONO: lee el estado anti-rollback y propone +1. La firma la hace policygen.
"""
import json
import os
import subprocess
import sys
import time


def _serial_actual(state_path: str) -> int:
    try:
        with open(state_path, encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return 0


def _fingerprint(binario: str) -> str:
    try:
        out = subprocess.run(
            [binario, "-fingerprint"], capture_output=True, text=True, timeout=15
        )
        return out.stdout.strip()
    except Exception:
        return ""


def main() -> int:
    cgroup = os.environ.get("PXMOTOR_CGROUP", "system.slice/hcma-firecracker.service")
    uid = int(os.environ.get("PXMOTOR_UID", "995"))
    exe = os.environ.get("PXMOTOR_EXE", "firecracker")
    allow = [a.strip() for a in os.environ.get("PXMOTOR_ALLOW", "python3").split(",") if a.strip()]
    mode = os.environ.get("PXMOTOR_MODE", "alert")
    output = os.environ.get("PXMOTOR_OUTPUT", "/var/lib/px-motor/alerts.jsonl")
    state = os.environ.get("PXMOTOR_STATE", "/var/lib/px-motor/policy.serial")
    pin = os.environ.get("PXMOTOR_PIN_FINGERPRINT", "1") == "1"
    days = int(os.environ.get("PXMOTOR_VALID_DAYS", "365"))
    binario = os.environ.get("PXMOTOR_BIN", "/usr/local/bin/px-motor")

    if mode not in ("alert", "kill"):
        print(f"PXMOTOR_MODE inválido: {mode} (se espera alert|kill)", file=sys.stderr)
        return 2

    serial = _serial_actual(state) + 1
    not_after = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + days * 86400))
    fingerprint = _fingerprint(binario) if pin else ""

    politica = {
        "serial": serial,
        "not_after": not_after,
        "fingerprint": fingerprint,
        "subject": {"cgroup": cgroup, "uid": uid, "exe": exe, "allow": allow},
        "rules": {"mode": mode, "active": []},
        "sinks": {"output": output},
    }
    json.dump(politica, sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
