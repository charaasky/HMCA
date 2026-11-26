#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Verificación property-based de los invariantes HCMA (paso 4).

Genera secuencias ALEATORIAS de escrituras (inputs, derivaciones, correcciones,
unverified, no-ops) y verifica, tras cada operación, los invariantes:

  A. Versiones globalmente únicas y monótonas.
  B. A lo sumo UN hecho por clave con authority=True.
  C. authority=True ⇒ verified=True ∧ stale=False.
  D. Linaje sano: todo hecho canónico tiene sus dependencias vigentes en versión exacta
     (esto es la invalidación transitiva: si una dep se supersede, el derivado pierde authority).

Además, compara RAM vs SQLite: la MISMA secuencia debe producir el MISMO state().
"""
import os, sys, random, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prototipo import HCMAMemoria
from memoria_sqlite import HCMAMemoriaSQLite

CLAVES_IN = ["in_a", "in_b", "in_c"]
CLAVES_DER = ["d_x", "d_y"]


def _todos(h):
    if hasattr(h, "facts"):
        out = []
        for facts in h.facts.values():
            out.extend(facts)
        return out
    rows = h.conn.execute("SELECT * FROM facts").fetchall()
    return [h._fact(r) for r in rows]


def chequear_invariantes(h):
    facts = _todos(h)
    errs = []
    versions = [f["version"] for f in facts]
    if len(versions) != len(set(versions)):
        errs.append("A: versión duplicada")
    by_key = {}
    for f in facts:
        by_key.setdefault(f["key"], []).append(f)
    cur = {}
    for k, fs in by_key.items():
        auths = [f for f in fs if f["authority"]]
        if len(auths) > 1:
            errs.append(f"B: {k} tiene {len(auths)} authority")
        for f in auths:
            if not f["verified"] or f["stale"]:
                errs.append(f"C: {k}@v{f['version']} authority sin verified/¬stale")
            cur[k] = f
    for f in facts:
        if f["authority"]:
            for dk, dv in (f.get("derived_from") or {}).items():
                dep = cur.get(dk)
                if dep is None or dep["version"] != dv:
                    errs.append(f"D: {f['key']}@v{f['version']} linaje roto a {dk}@v{dv}")
    return errs


def _op(rng, h):
    op = rng.choice(["input", "input", "derivar", "derivar", "unverified", "noop"])
    if op == "input":
        k = rng.choice(CLAVES_IN)
        h.write(k, round(rng.uniform(0, 1000), 2), unit="", fuente="usuario", verified=True)
    elif op == "derivar":
        disp = [k for k in CLAVES_IN + CLAVES_DER if h.current(k) is not None]
        if not disp:
            return
        deps = rng.sample(disp, rng.randint(1, min(2, len(disp))))
        linaje = {k: h.current(k)["version"] for k in deps}
        target = rng.choice(CLAVES_DER)
        h.write(target, round(rng.uniform(0, 1000), 2), unit="", fuente="calculadora",
                verified=True, derived_from=linaje)
    elif op == "unverified":
        k = rng.choice(CLAVES_IN + CLAVES_DER)
        h.write(k, round(rng.uniform(0, 1000), 2), unit="", fuente="especialista",
                verified=False)
    elif op == "noop":
        k = rng.choice(CLAVES_IN)
        f = h.current(k)
        if f is not None:
            h.write(k, f["value"], unit=f.get("unit", ""), fuente="usuario", verified=True)


def main():
    # 1) invariantes en RAM (muchas secuencias)
    print("1) Invariantes sobre RAM (secuencias aleatorias)...")
    total_ops = 0
    for seq in range(300):
        rng = random.Random(1000 + seq)
        h = HCMAMemoria()
        for _ in range(40):
            _op(rng, h)
            total_ops += 1
            errs = chequear_invariantes(h)
            if errs:
                print(f"   ✗ secuencia {seq} violó invariantes: {errs}")
                return 1
    print(f"   ✔ {total_ops} operaciones, 300 secuencias, 0 violaciones (A/B/C/D)")

    # 2) consistencia RAM vs SQLite (misma secuencia → mismo state)
    print("2) Consistencia RAM vs SQLite (misma secuencia → mismo state)...")
    for seq in range(80):
        rng = random.Random(5000 + seq)
        ram = HCMAMemoria()
        db = HCMAMemoriaSQLite(os.path.join(tempfile.mkdtemp(), "t.db"))
        for _ in range(20):
            op = rng.choice(["input", "input", "derivar", "derivar", "unverified", "noop"])
            # dibujar los parámetros UNA sola vez y aplicar la MISMA op a ambas memorias
            if op == "input":
                k = rng.choice(CLAVES_IN)
                v = round(rng.uniform(0, 1000), 2)
                ram.write(k, v, fuente="usuario", verified=True)
                db.write(k, v, fuente="usuario", verified=True)
            elif op == "derivar":
                disp = [k for k in CLAVES_IN + CLAVES_DER if ram.current(k) is not None]
                if disp:
                    deps = rng.sample(disp, rng.randint(1, min(2, len(disp))))
                    t = rng.choice(CLAVES_DER)
                    v = round(rng.uniform(0, 1000), 2)
                    linaje = {k: ram.current(k)["version"] for k in deps}
                    ram.write(t, v, fuente="calculadora", verified=True, derived_from=linaje)
                    db.write(t, v, fuente="calculadora", verified=True, derived_from=linaje)
            elif op == "unverified":
                k = rng.choice(CLAVES_IN + CLAVES_DER)
                v = round(rng.uniform(0, 1000), 2)
                ram.write(k, v, fuente="especialista", verified=False)
                db.write(k, v, fuente="especialista", verified=False)
            else:  # noop
                k = rng.choice(CLAVES_IN)
                f = ram.current(k)
                if f is not None:
                    ram.write(k, f["value"], fuente="usuario", verified=True)
                    db.write(k, f["value"], fuente="usuario", verified=True)
            if ram.state() != db.state():
                print(f"   ✗ secuencia {seq}: state() difiere RAM vs SQLite")
                print("     RAM :", sorted(ram.state()))
                print("     DB  :", sorted(db.state()))
                db.close()
                return 1
        db.close()
    print("   ✔ 80 secuencias × 20 ops, state() idéntico RAM vs SQLite")

    print("\n✅ PASO 4 OK: invariantes HCMA verificados property-based (RAM + SQLite).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
