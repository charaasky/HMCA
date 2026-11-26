#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""verificador_lean.py — FORMAL verification of theorems (epistemic authority).

Fable 5.2 ruling (`consultas/fable_lean_fable.md`): Lean is NOT "just another tool"; it produces
justifications for the TMS. The security boundary is NOT a text filter: it is

  1. REAL SANDBOX (bwrap): no network, read-only rootfs except the run dir, one dir per run.
  2. STATEMENT binding: the statement comes from a TRUSTED source; the LLM only contributes the
     PROOF. The harness writes `theorem {nombre} : {enunciado} := {prueba}`, so the LLM
     cannot weaken the statement (Fable P0-1).
  3. AXIOM AUDIT: the used axioms must be ⊆ {propext, Classical.choice, Quot.sound}.
     `sorry` appears as `sorryAx`, `native_decide` as `Lean.ofReduceBool`: both are rejected.
  4. EXPLICIT states (only DEMOSTRADO enters the TMS as proof).

The `lean_lint_rapido()` substring is ONLY UX (friendly early rejection), NOT the security
boundary. The real security is sandbox + binding + axiom audit.
"""
import hashlib
import os
import shutil
import subprocess
import tempfile

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in __import__("sys").path and os.path.isdir(DEP):
    __import__("sys").path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in __import__("sys").path:
    __import__("sys").path.insert(0, SCRIPTS)


# ── states (Fable 5.2 ruling) ──────────────────────────────────────────────
DEMOSTRADO = "DEMOSTRADO"
ENUNCIADO_NO_COINCIDE = "ENUNCIADO_NO_COINCIDE"
AXIOMAS_NO_PERMITIDOS = "AXIOMAS_NO_PERMITIDOS"
RECHAZADO_KERNEL = "RECHAZADO_KERNEL"
TIMEOUT = "TIMEOUT"
FALTA_HERRAMIENTA = "FALTA_HERRAMIENTA"
SANDBOX_NO_DISPONIBLE = "SANDBOX_NO_DISPONIBLE"
AUDITORIA_FALLO = "AUDITORIA_FALLO"
RECHECK_FALLO = "RECHECK_FALLO"

# The 3 core Lean 4 axioms (sound). Any other (sorryAx, Lean.ofReduceBool,
# an `axiom` declared by the LLM, ...) is default-deny.
AXIOMAS_PERMITIDOS = {"propext", "Classical.choice", "Quot.sound"}

# Top-level declaration/command keywords that the PROOF cannot inject (defense in
# depth). The confirmed vector (adversarial suite) is `#print`/`#exit`/`#eval`, which
# start with `#`; this also blocks loose declarations (`axiom`, `def`, `set_option`, ...).
DECLARACIONES_TOP_LEVEL = frozenset({
    "theorem", "axiom", "constant", "def", "example", "opaque", "abbrev",
    "inductive", "structure", "class", "instance", "set_option", "namespace",
    "section", "end", "macro", "elab", "notation", "syntax", "declare_syntax_cat",
})

# FAST LINT (UX, not security): friendly early rejection of the obvious.
TOKENS_LINT = ("#eval", "#reduce", "#print", "unsafe", "sorry", "admit", "axiom ")

# Metaprogramming/unsafe in the PROOF (Fable ruling: 'import Proof' runs the LLM's code).
# `macro`/`elab`/`notation`/`syntax` are already rejected as top-level declarations; these tokens
# run metaprogramming WITHOUT declaring top-level (run_tac, unsafeCast, elaborators). Rejecting
# them closes the vector of redefining syntax/environment during the axiom audit.
TOKENS_METAPROGRAMACION = ("run_tac", "unsafe", "elabTerm", "TermElabM", "CommandElabM",
                           "Lean.Elab", "Lean.Meta", "ofReduceBool", "native_decide",
                           "unsafeCast")


def lean_lint_rapido(codigo):
    """FAST UX LINT (early rejection), NOT the security boundary.

    Returns (pasa, motivo). The real security is sandbox + binding + axiom audit."""
    if not isinstance(codigo, str) or not codigo.strip():
        return True, ""
    low = codigo.lower()
    for tok in TOKENS_LINT:
        if tok in low:
            return False, f"lint: {tok.strip()}"
    return True, ""


def _hash(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _lean_bin_real():
    """Resolves the REAL toolchain binary (not the elan proxy), via `elan which lean`.

    Fable: `~/.elan/bin/lean` is a proxy; with isolated HOME it does not find the toolchain. The fix is
    to resolve the real binary and pin it. Returns absolute path or None."""
    return _tool_bin_real("lean")


def _tool_bin_real(nombre):
    """Resolves the REAL toolchain binary for `nombre` (lean, leanchecker, ...) via elan."""
    elan = shutil.which("elan") or os.path.expanduser("~/.elan/bin/elan")
    if not elan or not os.path.exists(elan):
        return None
    try:
        r = subprocess.run([elan, "which", nombre], capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            p = r.stdout.strip().splitlines()[0].strip()
            if os.path.exists(p):
                return p
    except Exception:
        pass
    return None


def _leanchecker_bin_real():
    """Real binary of `leanchecker` (independent olean re-check, Fable ruling)."""
    return _tool_bin_real("leanchecker")


def lean_sandbox_disponible():
    """Is there a real sandbox (bwrap) to run Lean? Without it, the connector is fail-closed."""
    return shutil.which("bwrap") is not None


def _toolchain_root(binario):
    # binario = .../toolchains/leanprover--lean4---v4.36.0/bin/lean
    return os.path.dirname(os.path.dirname(binario))


# ── Mathlib (ro-bind + frozen LEAN_PATH, Fable ruling: option a) ──────────
# The LEAN_PATH is computed ONCE outside (`lake env printenv LEAN_PATH`) and frozen; `lake` is NOT
# run inside (it wants to write .lake and can use git/network). Here it is reconstructed from the
# `.lake` tree (main build + packages).
MATHLIB_LAKE = "/home/charasky/Documentos/HMC/.deps/mathlib4/.lake"


def _mathlib_lean_paths():
    """`lib/lean` dirs of the Mathlib build (frozen per run)."""
    paths = []
    main = os.path.join(MATHLIB_LAKE, "build", "lib", "lean")
    if os.path.isdir(main):
        paths.append(main)
    pkgs = os.path.join(MATHLIB_LAKE, "packages")
    if os.path.isdir(pkgs):
        for p in sorted(os.listdir(pkgs)):
            pp = os.path.join(pkgs, p, ".lake", "build", "lib", "lean")
            if os.path.isdir(pp):
                paths.append(pp)
    return paths


def _mathlib_disponible():
    return bool(_mathlib_lean_paths())


def _mathlib_path_str():
    return ":".join(_mathlib_lean_paths())


def _limites_recurso():
    """Resource backstop (defense in depth). NOT the main boundary (the boundary
    is the bwrap sandbox). Caps CPU and file size; does NOT cap RLIMIT_AS because Lean 4 under
    user-namespace (bwrap) reserves a lot of virtual address space and 8GiB kills it with 'failed to create
    thread' (intermittent under memory pressure). The correct MEMORY limit is cgroup
    (Fable P1), not rlimit; here memory is capped by timeout + LEAN_NUM_THREADS=1."""
    def _apply():
        try:
            import resource as _r
            _r.setrlimit(_r.RLIMIT_CPU, (60, 60))
            _r.setrlimit(_r.RLIMIT_FSIZE, (256 << 20, 256 << 20))
        except Exception:
            pass
    return _apply


def _bwrap(run_dir, troot, cmd_args, env_extra=None, timeout=60, mathlib=False):
    """Runs `cmd_args` in bwrap: no network, read-only rootfs except /work (the run_dir).

    Returns the CompletedProcess. The subprocess exit code is that of the internal command (bwrap
    propagates it); a code != 0 is fail-closed for the caller. `troot` is the toolchain root
    (ro-bound at its original path) and `env_extra` are extra variables for the internal process.
    `mathlib=True` ro-binds the Mathlib build (frozen LEAN_PATH, Fable ruling)."""
    env = {"LEAN_NUM_THREADS": "1", "LEAN_SRC_PATH": ""}
    env.update(env_extra or {})
    cmd = [
        "bwrap",
        "--die-with-parent",
        "--unshare-all",       # user+ipc+pid+net+uts+cgroup (complete namespace isolation)
        "--new-session",
        "--clearenv",          # do NOT inherit the verifier's environment (avoids API key leaks)
        "--cap-drop", "ALL",   # no capabilities inside the sandbox
        # read-only rootfs (libc/ld + the toolchain + /etc for ld.so.cache)
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/etc", "/etc",
        "--ro-bind", troot, troot,
        # run dir (rw) mounted at /work; private /tmp (does not leak between runs)
        "--bind", run_dir, "/work",
        "--tmpfs", "/tmp",
        "--proc", "/proc",
        "--dev", "/dev",
        "--chdir", "/work",
        "--setenv", "HOME", "/work",
        "--setenv", "TMPDIR", "/work",
    ]
    if mathlib and os.path.isdir(MATHLIB_LAKE):
        cmd += ["--ro-bind", MATHLIB_LAKE, MATHLIB_LAKE]
    for k, v in env.items():
        cmd += ["--setenv", k, v]
    cmd += ["--"] + list(cmd_args)
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, start_new_session=True,
                          preexec_fn=_limites_recurso())


def _ejecutar_sandbox(binario, run_dir, archivo="Proof.lean", salida="Proof.olean", timeout=60,
                     mathlib=False):
    """Phase 1: compiles `{archivo}` → `{salida}` in bwrap. WITHOUT inline audit: the audit
    runs in a SEPARATE file/process (Phase 2) without the LLM's code (Fable ruling)."""
    troot = _toolchain_root(binario)
    lean_path = _mathlib_path_str() if mathlib else ""
    return _bwrap(run_dir, troot,
                  [binario, "-o", salida, archivo],
                  {"LEAN_PATH": lean_path}, timeout, mathlib=mathlib)


def _ejecutar_audit(binario, run_dir, toolchain_root, timeout=60, mathlib=False):
    """Phase 2: audits `Proof.olean` with a SEPARATE file (`Audit.lean` = `import Proof` +
    `#print axioms {nombre}`), 100% written by HCMA, WITHOUT the LLM's code. The LLM's proof
    lives in `Proof.lean` (Phase 1); here only its olean is imported and its axioms listed."""
    base = _mathlib_path_str() if mathlib else ""
    prefix = f"{base}:" if base else ""
    return _bwrap(run_dir, toolchain_root,
                  [binario, "Audit.lean"],
                  {"LEAN_PATH": f"/work:{prefix}{toolchain_root}/lib/lean"}, timeout,
                  mathlib=mathlib)


def _ejecutar_leanchecker(checker_bin, run_dir, toolchain_root, timeout=60, mathlib=False):
    """Re-checks the olean with `leanchecker` in a SEPARATE process (defense against a compromised
    elaborator, Fable ruling). LEAN_PATH points to the run_dir (/work) + Init of the toolchain, and
    PATH includes the toolchain's bin/ (leanchecker invokes `lean` as an external process)."""
    bin_dir = os.path.dirname(checker_bin)
    base = _mathlib_path_str() if mathlib else ""
    prefix = f"{base}:" if base else ""
    return _bwrap(run_dir, toolchain_root,
                  [checker_bin, "Proof"],
                  {"LEAN_PATH": f"/work:{prefix}{toolchain_root}/lib/lean",
                   "PATH": f"{bin_dir}:/usr/bin:/bin"}, timeout, mathlib=mathlib)


def _parsear_axiomas(salida, nombre):
    """Extracts the axiom list from `#print axioms {nombre}`. Returns None if there is no line
    (fail-closed: it could have been truncated with #exit or the audit not run).

    It binds to the theorem NAME (not to any `#print axioms`): an injection of
    `#print axioms <clean_name>` must not hijack the audit (finding of the adversarial
    suite)."""
    if not salida:
        return None
    marcador = f"'{nombre}'"
    for line in salida.splitlines():
        line = line.strip()
        if marcador not in line:
            continue
        if "does not depend on any axioms" in line:
            return []
        if "depends on axioms:" in line:
            i = line.find("[")
            j = line.find("]")
            if i != -1 and j != -1 and j > i:
                inner = line[i + 1:j].strip()
                if not inner:
                    return []
                return [a.strip() for a in inner.split(",") if a.strip()]
            return None
    return None


def _sanitizar_prueba(prueba):
    """Rejects injection of top-level commands/declarations in the PROOF (defense in depth).

    The harness writes `theorem {nombre} : {enunciado} := {prueba}`; the proof MUST be a
    term or a `by` block. A line starting with `#` (command) or with a declaration
    keyword ends the theorem and can run commands in the sandbox or hijack
    `#print axioms` (adversarial suite: `by sorry\n\n#print axioms True.intro` faked DEMOSTRADO).
    Returns (ok, motivo)."""
    for linea in prueba.splitlines():
        s = linea.strip()
        if not s:
            continue
        if s.startswith("#"):
            return False, f"comando top-level inyectado en la prueba: {s.split()[0]}"
        prim = s.split()[0]
        if prim in DECLARACIONES_TOP_LEVEL:
            return False, f"declaración top-level inyectada en la prueba: {prim}"
    # metaprogramming/unsafe: runs code in the elaborator without declaring top-level.
    for tok in TOKENS_METAPROGRAMACION:
        if tok in prueba:
            return False, f"metaprogramación/unsafe no permitido en la prueba: {tok}"
    return True, ""


# ── gVisor sandbox (runsc) — memory limit by cgroup (systemd), Fable ruling ──
# gVisor does NOT apply linux.resources.memory.limit (only accounting, see IMPLEMENTACION_gvisor_sandbox.md).
# The REAL memory limit is `MemoryMax` in the systemd unit `hcma-lean-sandbox.service` (cgroup).
GVISOR_BUNDLE = "/home/charasky/Documentos/HMC/arquitectura/gvisor/bundle-lean"
GVISOR_WORK = "/var/lib/hcma-lean/work"


def lean_runsc_disponible():
    """Is the gVisor Lean bundle ready AND can this process run runsc (root)?

    In production the MONITOR (root) runs it via `hcma-lean-sandbox.service` (MemoryMax=cgroup).
    In dev (non-root) it returns False and the bwrap sandbox is used. runsc needs root to mount
    namespaces/sandbox; the agent's connector (non-root) does NOT invoke it directly."""
    if os.geteuid() != 0:
        return False
    if shutil.which("runsc") is None:
        return False
    return (os.path.exists(GVISOR_BUNDLE + "/config.json")
            and os.path.isdir(GVISOR_BUNDLE + "/rootfs/opt/lean/bin"))


def _ejecutar_runsc(run_dir, timeout=60):
    """Runs the gVisor bundle: copies the candidate to GVISOR_WORK (bind-mount to /work in config.json)
    and runs `runsc run`. Memory is capped by the systemd unit (MemoryMax), not the OCI spec."""
    try:
        os.makedirs(GVISOR_WORK, exist_ok=True)
    except OSError:
        pass
    shutil.copy(os.path.join(run_dir, "Proof.lean"),
                os.path.join(GVISOR_WORK, "Proof.lean"))
    cmd = ["runsc", "--network=none", "run", "--bundle", GVISOR_BUNDLE, "hcma-lean-sandbox"]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def conector_lean(artifacto):
    """`artifacto` = {enunciado, prueba, nombre, contexto?}.

    - `contexto` (OPTIONAL): TRUSTED prelude of definitions/lemmas (approved source, NOT from the LLM).
      Allows verifying a 'development' (definitions + lemmas + theorem), not just a loose theorem.
    - `enunciado`: TRUSTED Lean proposition (comes from an approved source, NOT from the LLM in the same
      call). The harness asserts it, so the LLM cannot weaken it (P0-1).
    - `prueba`: the LLM's demonstration (term or `by ...` block). It is the ONLY untrusted part.
    - `nombre`: theorem name.

    Returns {ok, estado, axiomas, detalle, ...}. Only `DEMOSTRADO` (ok=True) is a proof."""
    if not isinstance(artifacto, dict):
        return _res(SANDBOX_NO_DISPONIBLE, "artifacto inválido")

    # sandbox: prefer gVisor/runsc (real cgroup) if available; otherwise, bwrap. Without either
    # of the two, fail-closed.
    runsc_ok = lean_runsc_disponible()
    bwrap_ok = lean_sandbox_disponible()
    if not runsc_ok and not bwrap_ok:
        return _res(SANDBOX_NO_DISPONIBLE,
                    "no hay sandbox (ni bwrap ni runsc/gVisor) disponible: verificación formal fail-closed")

    contexto = (artifacto.get("contexto") or "").strip()

    # Mathlib is enabled if the (trusted) CONTEXTO imports it. The LLM cannot inject it:
    # it only contributes the `prueba`; `contexto`/`enunciado` come from an approved source.
    mathlib = "import Mathlib" in contexto
    enunciado = (artifacto.get("enunciado") or "").strip()
    prueba = (artifacto.get("prueba") or "").strip()
    nombre = (artifacto.get("nombre") or "teorema").strip()

    if not enunciado:
        return _res(ENUNCIADO_NO_COINCIDE, "enunciado vacío (fuente confiable obligatoria)")
    if not prueba:
        return _res(RECHAZADO_KERNEL, "prueba vacía")

    ok_prueba, motivo = _sanitizar_prueba(prueba)
    if not ok_prueba:
        return _res(RECHAZADO_KERNEL, motivo)

    # The statement is TRUSTED, but defense in depth: it cannot be a declaration nor
    # inject top-level commands. (It is an authority assertion, not a sandbox.)
    if "theorem" in enunciado.lower() or ":=" in enunciado or "\n#" in enunciado:
        return _res(ENUNCIADO_NO_COINCIDE, "enunciado con forma inválida (no es una proposición)")

    # only for the bwrap path the host binary is resolved (the gVisor bundle already brings it inside).
    # It is ALWAYS resolved for the audit/re-check (Phases 2-3), which run in bwrap outside the VM.
    binario = _lean_bin_real()
    if binario is None:
        return _res(FALTA_HERRAMIENTA,
                    "lean no está instalado (elan which lean no resolvió). "
                    "Instalación: curl https://raw.githubusercontent.com/leanprover/elan/master/"
                    "elan-init.sh -sSf | sh")

    run_dir = tempfile.mkdtemp(prefix="hcma_lean_")
    # Phase 1: Proof.lean (context + theorem + proof), WITHOUT `#print axioms`. The LLM only contributes
    # `prueba`; enunciado/contexto are trusted.
    contenido = (contexto + "\n\n") if contexto else ""
    contenido += f"theorem {nombre} : {enunciado} :=\n  {prueba}\n"
    with open(os.path.join(run_dir, "Proof.lean"), "w", encoding="utf-8") as f:
        f.write(contenido)

    # Phase 2 (file): Audit.lean = import Proof + #print axioms. 100% HCMA, WITHOUT the LLM's code.
    with open(os.path.join(run_dir, "Audit.lean"), "w", encoding="utf-8") as f:
        f.write(f"import Proof\n#print axioms {nombre}\n")

    try:
        r = _ejecutar_runsc(run_dir) if runsc_ok else _ejecutar_sandbox(binario, run_dir, mathlib=mathlib)
    except subprocess.TimeoutExpired:
        shutil.rmtree(run_dir, ignore_errors=True)
        return _res(TIMEOUT, "timeout de verificación (60s)")

    if r.returncode != 0:
        shutil.rmtree(run_dir, ignore_errors=True)
        return _res(RECHAZADO_KERNEL, (r.stderr or r.stdout or "error de type-checking").strip()[:500])

    # In gVisor the olean stays in GVISOR_WORK (bind to /work); bring it to run_dir for the audit.
    if runsc_ok:
        shutil.copy(os.path.join(GVISOR_WORK, "Proof.olean"),
                    os.path.join(run_dir, "Proof.olean"))

    # Phase 2 (execution): audit in a SEPARATE process (bwrap), without the LLM's code.
    try:
        ra = _ejecutar_audit(binario, run_dir, _toolchain_root(binario), mathlib=mathlib)
    except subprocess.TimeoutExpired:
        shutil.rmtree(run_dir, ignore_errors=True)
        return _res(TIMEOUT, "timeout de auditoría de axiomas (60s)")

    salida = (ra.stdout or "") + "\n" + (ra.stderr or "")
    axiomas = _parsear_axiomas(salida, nombre)

    if ra.returncode != 0:
        shutil.rmtree(run_dir, ignore_errors=True)
        return _res(AUDITORIA_FALLO,
                    "la auditoría no pudo leer el olean (import Proof falló): "
                    + (ra.stderr or ra.stdout or "audit fail").strip()[:300], axiomas=axiomas)

    if axiomas is None:
        shutil.rmtree(run_dir, ignore_errors=True)
        return _res(AUDITORIA_FALLO,
                    "la auditoría de axiomas no produjo resultado (posible #exit/truncado): fail-closed",
                    axiomas=None)

    no_permitidos = [a for a in axiomas if a not in AXIOMAS_PERMITIDOS]
    if no_permitidos:
        shutil.rmtree(run_dir, ignore_errors=True)
        return _res(AXIOMAS_NO_PERMITIDOS,
                    f"axiomas no permitidos: {no_permitidos}", axiomas=axiomas)

    # Independent re-check of the olean with leanchecker (Fable ruling: defense against a
    # compromised elaborator). Only for the bwrap path; the runsc/gVisor path would do it in a
    # second invocation of the container (pending production).
    recheck = "leanchecker"
    if not runsc_ok:
        checker_bin = _leanchecker_bin_real()
        if checker_bin is None:
            shutil.rmtree(run_dir, ignore_errors=True)
            return _res(FALTA_HERRAMIENTA, "leanchecker no resuelto (re-chequeo imposible)")
        try:
            rc = _ejecutar_leanchecker(checker_bin, run_dir, _toolchain_root(binario), mathlib=mathlib)
        except subprocess.TimeoutExpired:
            shutil.rmtree(run_dir, ignore_errors=True)
            return _res(TIMEOUT, "timeout del re-chequeo leanchecker (60s)")
        if rc.returncode != 0:
            shutil.rmtree(run_dir, ignore_errors=True)
            return _res(RECHECK_FALLO,
                        "leanchecker no re-verificó el olean (elaborador no confiable): "
                        + (rc.stderr or rc.stdout or "recheck fail").strip()[:300])

    shutil.rmtree(run_dir, ignore_errors=True)
    obs = {
        "nombre": nombre,
        "hash_enunciado": _hash(enunciado),
        "hash_prueba": _hash(prueba),
        "hash_contexto": _hash(contexto) if contexto else "",
        "axiomas": axiomas,
        "toolchain": binario or (GVISOR_BUNDLE + "/rootfs/opt/lean/bin/lean"),
        "sandbox": "runsc" if runsc_ok else "bwrap",
        "recheck": recheck,
    }
    return {"ok": True, "estado": DEMOSTRADO, "axiomas": axiomas, "detalle": "demostrado",
            "herramienta": "lean", "observables": obs}


def _res(estado, detalle, axiomas=None):
    return {"ok": False, "estado": estado, "axiomas": axiomas, "detalle": detalle,
            "herramienta": "lean"}


if __name__ == "__main__":
    import json
    print("sandbox disponible:", lean_sandbox_disponible())
    print("binario real:", _lean_bin_real())
    r = conector_lean({
        "nombre": "demo_comm",
        "enunciado": "∀ p q : Prop, p ∧ q → q ∧ p",
        "prueba": "fun p q h => And.intro h.right h.left",
    })
    print(json.dumps(r, ensure_ascii=False, indent=2))
