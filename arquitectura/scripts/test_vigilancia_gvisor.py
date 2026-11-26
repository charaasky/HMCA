#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_vigilancia_gvisor.py — clasificación y registro de escapes DENTRO de gVisor."""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import vigilancia_gvisor as VG                # noqa: E402
import incidentes as INC                      # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        FAIL.append(name)


print("=== [1] clasificación de líneas de log (vectores) ===")
check("1a. red", VG.clasificar_linea("connect(...) => EPERM (network=none)") == ("red", "red"))
check("1b. fs_host",
      VG.clasificar_linea("open(/var/lib/hcma-signer/humano_ed25519.key) => ENOENT")
      == ("fs_host", "fs_host"))
check("1c. privilegios", VG.clasificar_linea("setuid(0) => EPERM") == ("privilegios", "privilegios"))
check("1d. recursos", VG.clasificar_linea("clone(...) => ENOMEM") == ("recursos", "recursos"))
check("1e. proc_sys",
      VG.clasificar_linea("write(/proc/sys/kernel/hostname) => EPERM") == ("proc_sys", "proc_sys"))
check("1f. línea benigna → None", VG.clasificar_linea("read(fd) => ok (benigno)") is None)
# blacklist de paths sensibles (heredada del LSM eBPF framework secure)
check("1g. kernel_mem (escape/rootkit)",
      VG.clasificar_linea("open(/dev/mem) => EPERM") == ("kernel_mem", "kernel_mem"))
check("1h. data_wipe", VG.clasificar_linea("open(/dev/sda) => EPERM") == ("data_wipe", "data_wipe"))
check("1i. fs_host (persistencia/backdoor)",
      VG.clasificar_linea("open(/etc/sudoers) => EPERM") == ("fs_host", "fs_host"))
check("1j. recon (discovery)", VG.clasificar_linea("cat /etc/shadow") == ("recon", "recon"))
check("1k. tampering", VG.clasificar_linea("unlink(/var/log/auth.log)") == ("tampering", "tampering"))

print("\n=== [2] clasificación de exit code (muerte anómala) ===")
check("2a. SIGKILL (-9)", VG.clasificar_exit(-9) == ("sigkill", "recursos"))
check("2b. SIGXCPU (-24)", VG.clasificar_exit(-24) == ("cpu_excedida", "recursos"))
check("2c. 137 (128+9)", VG.clasificar_exit(137) == ("sigkill", "recursos"))
check("2d. exit normal 0 → None", VG.clasificar_exit(0) is None)

print("\n=== [3] procesar_log registra + bloquea + idempotente ===")
m = HCMAMemoriaSQLite(":memory:")
log = ["connect(...) => EPERM", "open(/var/lib/hcma-signer/humano_ed25519.key) => ENOENT"]
inc = VG.procesar_log(m, "s1", "agente-a", log)
check("3a. 2 incidentes registrados", len(inc) == 2, inc)
check("3b. agente bloqueado por agent_id", INC.agente_bloqueado(m, agent_id="agente-a") is True)
check("3c. 2 incidentes activos", len(INC.incidentes_activos(m)) == 2)
inc2 = VG.procesar_log(m, "s1", "agente-a", log)
check("3d. idempotente (no duplica)", len(inc2) == 0)

print("\n=== [4] procesar_exit registra incidente de recursos ===")
m2 = HCMAMemoriaSQLite(":memory:")
r = VG.procesar_exit(m2, "s1", "agente-a", -9)
check("4a. exit SIGKILL → incidente", r is not None and len(r) == 1, r)
check("4b. agente bloqueado", INC.agente_bloqueado(m2, agent_id="agente-a") is True)
check("4c. exit normal no registra", VG.procesar_exit(m2, "s1", "agente-a", 0) is None)

print(f"\nRESULTADO: {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
