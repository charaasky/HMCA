#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""red_team_lean.py — red team suite (Fable 5.2 ruling, step 6).

Validates that the hardening (two-phase audit + hardened bwrap) really blocks or
detects the attack vectors. Each case MUST fail: if the attack passes, it is a GAP.

Covers Fable's vectors (the testable ones):
  1. native_decide (ofReduceBool)          → rejected (axiom or kernel)
  2. unsafeCast                            → kernel rejects
  3. forged axioms message                → #print axioms injection (sanitization + 2 phases)
  4. skipKernelTC with false lemma        → set_option injection (sanitization)
  5. notation that changes the statement  → notation injection (sanitization)
  6. hand-written .olean                  → leanchecker rejects
  7. fork bomb / filling /tmp             → timeout+RLIMIT (bwrap dev: NO pid limit = GAP)
  +  environment leak (--clearenv)        → /usr/bin/env with no secrets
  +  /proc isolation (--unshare-pid)      → only PIDs of the own namespace
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

import verificador_lean as VL  # noqa: E402

PASS = 0
FAIL = 0
GAPS = []


def check(nombre, bloqueado, detalle=""):
    global PASS, FAIL
    if bloqueado:
        PASS += 1
        print(f"  ✅ {nombre}")
    else:
        FAIL += 1
        GAPS.append(nombre)
        print(f"  ❌ {nombre}  ← GAP  {detalle}")
    return bloqueado


def _run_sandbox(cmd_args, env_extra=None):
    binario = VL._lean_bin_real()
    troot = VL._toolchain_root(binario)
    return VL._bwrap("/tmp", troot, cmd_args, env_extra or {})


print("=" * 74)
print("RED TEAM — conector Lean (debe BLOQUEAR todo)")
print("=" * 74)

print("\n[1] Soundness — native_decide (ofReduceBool)")
r = VL.conector_lean({"nombre": "rt1", "enunciado": "1000000 + 1000000 = 2000000",
                      "prueba": "by native_decide"})
check("1. native_decide rechazado", r["estado"] != VL.DEMOSTRADO, r["estado"])

print("\n[2] Soundness — unsafeCast (safe refiere a unsafe)")
r = VL.conector_lean({"nombre": "rt2", "enunciado": "False",
                      "prueba": "by exact unsafeCast (True.intro : True)"})
check("2. unsafeCast → RECHAZADO_KERNEL", r["estado"] == VL.RECHAZADO_KERNEL, r["estado"])

print("\n[3] Mensaje de axiomas falsificado (inyección #print axioms)")
r = VL.conector_lean({"nombre": "rt3", "enunciado": "False",
                      "prueba": "by sorry\n\n#print axioms True.intro"})
check("3. falsificar axiomas → NO DEMOSTRADO", r["estado"] != VL.DEMOSTRADO, r["estado"])

print("\n[4] skipKernelTC con lema falso (inyección set_option)")
r = VL.conector_lean({"nombre": "rt4", "enunciado": "True",
                      "prueba": "by trivial\n\nset_option debug.skipKernelTC true"})
check("4. set_option skipKernelTC → RECHAZADO_KERNEL", r["estado"] == VL.RECHAZADO_KERNEL, r["estado"])

print("\n[5] Notación que cambia el enunciado (inyección notation)")
r = VL.conector_lean({"nombre": "rt5", "enunciado": "True",
                      "prueba": "by trivial\n\nnotation \"X\" => True"})
check("5. notation → RECHAZADO_KERNEL", r["estado"] == VL.RECHAZADO_KERNEL, r["estado"])

print("\n[6] .olean escrito a mano (leanchecker lo rechaza)")
binario = VL._lean_bin_real()
checker = VL._leanchecker_bin_real()
if binario and checker:
    d = tempfile.mkdtemp(prefix="hcma_rt_olean_")
    with open(os.path.join(d, "Proof.olean"), "wb") as f:
        f.write(b"\x00\x00\x00\x00garbage not an olean")
    rc = VL._ejecutar_leanchecker(checker, d, VL._toolchain_root(binario))
    check("6. olean corrupto → leanchecker reject", rc.returncode != 0,
          f"returncode={rc.returncode}")
    shutil.rmtree(d, ignore_errors=True)
else:
    check("6. (SKIP) leanchecker no disponible", False)

print("\n[7] Fork bomb / llenado de /tmp (límites de recursos)")
rc = _run_sandbox(["sh", "-c", "dd if=/dev/zero of=/tmp/big bs=1M count=300 2>/dev/null; stat -c %s /tmp/big 2>/dev/null"])
size = int((rc.stdout or "0").strip().splitlines()[-1] or "0")
# RLIMIT_FSIZE=256MB bounds EACH file; the total of /tmp (tmpfs=RAM) is NOT bounded (honest note)
check("7a. llenar /tmp acotado por archivo (RLIMIT_FSIZE=256MB)", 0 < size <= 256 << 20,
      f"tamaño={size} bytes")
# bwrap dev has NO pid limit (cgroup): a real fork bomb is NOT bounded. It is declared an honest
# GAP; in production gVisor covers it (systemd TasksMax unit). A real fork bomb is not run.
check("7b. fork bomb acotado por límite de pids", False,
      "bwrap dev sin pids.max; lo cubre gVisor/cgroup en producción (pendiente systemd-run)")

print("\n[8] Fuga de entorno (--clearenv)")
os.environ["HCMA_RT_SECRET"] = "supersecreto"
rc = _run_sandbox(["/usr/bin/env"])
check("8. entorno sin secretos", "HCMA_RT_SECRET" not in (rc.stdout or ""),
      "filtró HCMA_RT_SECRET")

print("\n[9] Aislamiento de /proc (--unshare-pid)")
rc = _run_sandbox(["sh", "-c", "ls /proc | grep -E '^[0-9]+$' | wc -l"])
n = int((rc.stdout or "0").strip() or "0")
check("9. /proc solo muestra el namespace propio (pocos PIDs)", 0 < n <= 10,
      f"PIDs visibles={n} (si es ~160, no aísla)")

print("\n" + "=" * 74)
print(f"RESULTADO: {PASS} bloqueados / {FAIL} GAPS")
if GAPS:
    print("GAPS:", GAPS)
print("=" * 74)
sys.exit(1 if FAIL else 0)
