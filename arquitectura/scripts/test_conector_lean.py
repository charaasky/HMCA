#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_conector_lean.py — test del verificador FORMAL de teoremas (dictamen Fable 5.2).

La frontera de seguridad es SANDBOX (bwrap) + BINDING del enunciado + AUDITORÍA DE AXIOMAS, NO un
filtro de texto. Este test falsa:

QUÉ LO FALSARÍA:
  - Si una prueba FALSA derivara DEMOSTRADO, el kernel no estaría type-checkeando.
  - Si `sorry` derivara DEMOSTRADO, la auditoría de axiomas estaría rota.
  - Si el enunciado confiable se pudiera debilitar (proof de otro teorema), el binding fallaría.
  - Si `lean_lint_rapido` se tomara como frontera de seguridad en vez de UX.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import conectores as CNX            # noqa: E402
import verificador_lean as VL       # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")
    return cond


print("=" * 74)
print("[1] Lean está CABLEADO como autoridad (conector + riesgo acotado)")
print("=" * 74)
check("1a. lean en CONECTORES", "lean" in CNX.CONECTORES, list(CNX.CONECTORES))
check("1b. riesgo estático = 1 (acotado/sandbox, no 0)",
      CNX.RIESGO_POR_CONECTOR.get("lean") == 1, CNX.RIESGO_POR_CONECTOR.get("lean"))

print("\n[2] SANDBOX real (bwrap) disponible (fail-closed si no)")
check("2a. lean_sandbox_disponible()", VL.lean_sandbox_disponible() is True)

print("\n[3] LINT RÁPIDO es UX (rechazo temprano), NO la frontera de seguridad")
check("3a. prueba pura pasa el lint", VL.lean_lint_rapido("fun p q h => ⟨h.right, h.left⟩")[0] is True)
check("3b. #eval se marca en el lint", VL.lean_lint_rapido("#eval IO.println 1")[0] is False)

print("\n[4] BINDING del enunciado: el LLM NO puede debilitar el enunciado")
r_ok = VL.conector_lean({
    "nombre": "demo_comm",
    "enunciado": "∀ p q : Prop, p ∧ q → q ∧ p",
    "prueba": "fun p q h => ⟨h.right, h.left⟩",
})
check("4a. prueba correcta => DEMOSTRADO", r_ok["estado"] == VL.DEMOSTRADO, r_ok)
check("4b. axiomas vacíos (constructivo)", r_ok["axiomas"] == [], r_ok["axiomas"])

# proof de OTRO teorema (True) contra el enunciado pedido => el kernel lo rechaza
r_bind = VL.conector_lean({
    "nombre": "t",
    "enunciado": "(1 + 1 : Nat) = 2",
    "prueba": "trivial",  # inhabitante de True, no de (1+1:Nat)=2
})
check("4c. proof de otro enunciado => RECHAZADO_KERNEL (binding)",
      r_bind["estado"] == VL.RECHAZADO_KERNEL, r_bind)

print("\n[5] KERNEL rechaza lo falso")
r_falsa = VL.conector_lean({"nombre": "t", "enunciado": "(1 + 1 : Nat) = 3", "prueba": "rfl"})
check("5a. (1+1:Nat)=3 => RECHAZADO_KERNEL", r_falsa["estado"] == VL.RECHAZADO_KERNEL, r_falsa)

print("\n[6] AUDITORÍA DE AXIOMAS: sorry/axiom NO pasan")
r_sorry = VL.conector_lean({"nombre": "t", "enunciado": "(1 + 1 : Nat) = 2", "prueba": "by sorry"})
check("6a. sorry => AXIOMAS_NO_PERMITIDOS", r_sorry["estado"] == VL.AXIOMAS_NO_PERMITIDOS, r_sorry)
check("6b. axiomas = [sorryAx]", r_sorry["axiomas"] == ["sorryAx"], r_sorry["axiomas"])

print("\n[7] ENUNCIADO vacío/inválido => ENUNCIADO_NO_COINCIDE")
r_vacio = VL.conector_lean({"nombre": "t", "enunciado": "", "prueba": "rfl"})
check("7a. enunciado vacío => ENUNCIADO_NO_COINCIDE",
      r_vacio["estado"] == VL.ENUNCIADO_NO_COINCIDE, r_vacio)

print("\n[8] integración vía derivar (dispatcher + gate de riesgo)")
r_der = CNX.derivar("lean", {"nombre": "d", "enunciado": "(1 + 1 : Nat) = 2", "prueba": "rfl"})
check("8a. derivar('lean') => ok=True", r_der["ok"] is True, r_der)
check("8b. observables con procedencia (hash_enunciado + sandbox)",
      bool(r_der.get("observables", {}).get("hash_enunciado"))
      and r_der.get("observables", {}).get("sandbox") == "bwrap", r_der.get("observables"))
check("8c. re-check leanchecker registrado",
      r_der.get("observables", {}).get("recheck") == "leanchecker", r_der.get("observables"))

print("\n[9] RECHECK independiente (leanchecker) detecta olean corrupto")
import tempfile as _tf
binario = VL._lean_bin_real()
checker = VL._leanchecker_bin_real()
if binario and checker:
    d = _tf.mkdtemp(prefix="hcma_recheck_")
    with open(os.path.join(d, "Proof.lean"), "w", encoding="utf-8") as f:
        f.write("theorem t : (1 + 1 : Nat) = 2 := rfl\n")
    r_ok = VL._ejecutar_sandbox(binario, d)
    check("9a. lean -o produce olean",
          r_ok.returncode == 0 and os.path.exists(os.path.join(d, "Proof.olean")), r_ok)
    rc_ok = VL._ejecutar_leanchecker(checker, d, VL._toolchain_root(binario))
    check("9b. leanchecker acepta olean sano", rc_ok.returncode == 0, rc_ok)
    olean = os.path.join(d, "Proof.olean")
    with open(olean, "w") as f:
        pass  # corromper el olean: truncar a 0 (elaborador 'comprometido' no puede re-chequear)
    rc_bad = VL._ejecutar_leanchecker(checker, d, VL._toolchain_root(binario))
    check("9c. leanchecker RECHAZA olean corrupto (elaborador no confiable)",
          rc_bad.returncode != 0, rc_bad)
else:
    check("9a (SKIP). leanchecker no disponible", False)

print("\n[10] MODO DESARROLLO (contexto confiable + teorema)")
r_dev = VL.conector_lean({
    "nombre": "usa_contexto",
    "contexto": "def doble (n : Nat) : Nat := n + n\ntheorem doble_id (n : Nat) : doble n = n + n := rfl",
    "enunciado": "∀ n : Nat, doble n = n + n",
    "prueba": "fun n => doble_id n",
})
check("10a. contexto + teorema => DEMOSTRADO", r_dev["estado"] == VL.DEMOSTRADO, r_dev)
check("10b. hash_contexto registrado (procedencia)",
      bool(r_dev.get("observables", {}).get("hash_contexto")), r_dev.get("observables"))

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
