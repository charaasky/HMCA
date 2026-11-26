#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Adversarial suite of the Lean connector (Fable 5.2 ruling, step 2).

Question it answers: does the current conector_lean reject the exploits it should reject,
or accept them as DEMOSTRADO (false acceptance)?

Each case is an {enunciado, prueba} where:
- `espera_demostrado=True`  → it is a LEGITIMATE proof, must come out DEMOSTRADO.
- `espera_demostrado=False` → it is an EXPLOIT, must come out in ANY rejection state.

Central metric (Fable): number of FALSE ACCEPTANCES (exploit that came out DEMOSTRADO).
"""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from verificador_lean import conector_lean, DEMOSTRADO

# ── cases ─────────────────────────────────────────────────────────────────────
# espera=True  : legitimate (must be DEMOSTRADO)
# espera=False : exploit  (must be REJECTED in any state)
CASOS = [
    # (0) control: real proof, must pass
    dict(id="control_ok", espera=True, nombre="c0",
         enunciado="∀ p q : Prop, p ∧ q → q ∧ p",
         prueba="fun p q h => And.intro h.right h.left",
         desc="control: prueba real (conmutatividad de ∧)"),

    # (1) direct sorry
    dict(id="sorry", espera=False, nombre="c1",
         enunciado="False", prueba="by sorry",
         desc="sorry: hueco no probado (sorryAx)"),

    # (2) direct admit
    dict(id="admit", espera=False, nombre="c2",
         enunciado="False", prueba="by admit",
         desc="admit: sinónimo de sorry"),

    # (3) native_decide (ofReduceBool, the kernel does not verify the reduction)
    dict(id="native_decide", espera=False, nombre="c3",
         enunciado="1000000 + 1000000 = 2000000", prueba="by native_decide",
         desc="native_decide: prueba sin chequeo de kernel (ofReduceBool)"),

    # (4) false + trivial (kernel must reject)
    dict(id="false_trivial", espera=False, nombre="c4",
         enunciado="False", prueba="by trivial",
         desc="falso: trivial no puede demostrar False"),

    # (5) unsafeCast (unsafe cast)
    dict(id="unsafe_cast", espera=False, nombre="c5",
         enunciado="False", prueba="by exact unsafeCast (True.intro : True)",
         desc="unsafeCast: cast no seguro para fabricar una prueba"),

    # (6) INJECTION of clean #print axioms after a sorry (hijack of the axiom parser)
    dict(id="inject_print_axioms", espera=False, nombre="c6",
         enunciado="False",
         prueba="by sorry\n\n#print axioms True.intro",
         desc="inyección: #print axioms True.intro (limpio) antes del audit real → hijack"),

    # (7) INJECTION of #exit (truncates before the audit)
    dict(id="inject_exit", espera=False, nombre="c7",
         enunciado="True",
         prueba="by trivial\n\n#exit",
         desc="inyección: #exit trunca el proceso antes del audit"),

    # (8) native_decide + injection of clean #print axioms
    dict(id="inject_print_axioms_natdec", espera=False, nombre="c8",
         enunciado="1000000 + 1000000 = 2000000",
         prueba="by native_decide\n\n#print axioms True.intro",
         desc="inyección: native_decide + #print axioms limpio"),

    # (9) decide (sound): kernel reduction, must pass
    dict(id="decide_sano", espera=True, nombre="c9",
         enunciado="1000000 + 1000000 = 2000000", prueba="by decide",
         desc="control: decide (reducción de kernel, sano)"),

    # (10) legitimate proof using Classical.choice (allowed axiom)
    dict(id="classical_choice_ok", espera=True, nombre="c10",
         enunciado="∀ p : Prop, p ∨ ¬ p",
         prueba="by intro p; exact Classical.em p",
         desc="control: Classical.em (depende de Classical.choice, permitido)"),

    # (11) linarith (does not exist in core): must fail by toolchain, not sneak through
    dict(id="linarith_core", espera=False, nombre="c11",
         enunciado="(1 : Nat) + 1 = 2", prueba="by linarith",
         desc="linarith: táctica de Mathlib, ausente en core (debe fallar)"),

    # (12) decide on false (kernel reduction cannot prove the false)
    dict(id="decide_false", espera=False, nombre="c12",
         enunciado="(1 : Nat) + 1 = 3", prueba="by decide",
         desc="decide sobre falso: el kernel no puede reducir a prueba"),

    # (13) native_decide on false
    dict(id="native_decide_false", espera=False, nombre="c13",
         enunciado="(1 : Nat) + 1 = 3", prueba="by native_decide",
         desc="native_decide sobre falso: no puede fabricar prueba"),

    # (14) set_option injection (sanitization must block it)
    dict(id="inject_set_option", espera=False, nombre="c14",
         enunciado="True", prueba="by trivial\n\nset_option debug.skipKernelTC true",
         desc="inyección: set_option top-level (saltar chequeo de kernel)"),

    # (15) theorem declaration injection (sanitization must block it)
    dict(id="inject_theorem", espera=False, nombre="c15",
         enunciado="True", prueba="by trivial\n\ntheorem evil : False := by sorry",
         desc="inyección: declaración theorem top-level"),

    # (16) notation injection (sanitization must block it)
    dict(id="inject_notation", espera=False, nombre="c16",
         enunciado="True", prueba="by trivial\n\nnotation \"X\" => True",
         desc="inyección: redefinición de notación top-level"),
]


def correr():
    print("=" * 78)
    print("SUITE ADVERSARIAL — conector_lean (Fable 5.2, paso 2)")
    print("=" * 78)
    filas = []
    falsas_aceptaciones = 0
    falsos_negativos = 0
    for c in CASOS:
        r = conector_lean({"nombre": c["nombre"], "enunciado": c["enunciado"],
                           "prueba": c["prueba"]})
        estado = r.get("estado", "?")
        ax = r.get("axiomas")
        demostrado = (estado == DEMOSTRADO)

        if c["espera"] and demostrado:
            veredicto = "OK (aceptó legítimo)"
            ok = True
        elif c["espera"] and not demostrado:
            veredicto = "FALSO NEGATIVO (rechazó legítimo)"
            ok = False
            falsos_negativos += 1
        elif (not c["espera"]) and demostrado:
            veredicto = ">>> FALSA ACEPTACIÓN (exploit colado) <<<"
            ok = False
            falsas_aceptaciones += 1
        else:
            veredicto = "OK (rechazó exploit)"
            ok = True

        filas.append((c["id"], c["espera"], estado, ax, veredicto))

    for (cid, espera, estado, ax, veredicto) in filas:
        print(f"\n[{cid}] espera={'legit' if espera else 'exploit'}")
        print(f"    estado     : {estado}")
        print(f"    axiomas    : {ax}")
        print(f"    veredicto  : {veredicto}")

    print("\n" + "=" * 78)
    print(f"RESUMEN: {len(CASOS)} casos")
    print(f"  falsas aceptaciones (exploit → DEMOSTRADO): {falsas_aceptaciones}")
    print(f"  falsos negativos   (legítimo → rechazado) : {falsos_negativos}")
    print("=" * 78)
    return {"casos": len(CASOS), "falsas_aceptaciones": falsas_aceptaciones,
            "falsos_negativos": falsos_negativos}


if __name__ == "__main__":
    res = correr()
    out = os.path.join(HERE, "..", "resultados", "suite_adversarial_resultado.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"\nresultado guardado en {os.path.abspath(out)}")
