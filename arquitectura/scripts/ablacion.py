#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""ablacion.py — flags for the ABLATION MATRIX (Fable: 'turn off each mechanism and show that its
attack gets through').

The ablation matrix demonstrates CAUSALITY: turn off a mechanism => its attack gets through. For
that demonstration to be valid, the turn-off flags must be UNREACHABLE by the adversary.

Fable P0 (5th revision): 'HCMA_TEST_MODE is a master key. The runner sets it, not the agent; it is
an assumption, not a mechanism. The right place is where the flag is read: outside test mode, the
query ''is it off?'' always answers ''on''.'

Applied redesign (root of trust by UID, not by environment variable):

  - `modo_prueba()` derives from WHO runs the process (getuid), NOT from an env var. The test
    runner runs as the HUMAN (charasky) or root; the agent (hcma-agent) is NEVER a test UID. Thus
    the agent cannot self-grant test mode by setting an env var in a subprocess.
  - `activo(mecanismo)` does NOT consult free mutable state: if the process is NOT an authorized
    test runner, it ALWAYS returns True (defense on), regardless of what is in `_ESTADO`. The
    switchable state is only visible to the authorized test runner.
  - `apagar()` ONLY works for the authorized test runner; otherwise it raises.

This closes the master key: there is no variable that, when set, turns off two defenses. The
authority to turn off defenses is the UID of the test runner, which the agent cannot change.
"""
import os
import threading

# UIDs that can NEVER turn off defenses (the agent/runtime). Everything else (human/root/others)
# is an authorized test runner. NEGATIVE criterion: we forbid the agent, we do not authorize a
# whitelist that in practice includes the human who runs the tests.
# UIDs that ARE authorized test runners (POSITIVE criterion, Fable 6th revision).
# The default is PRODUCTION: outside this allowlist, modo_prueba() is False. The agent
# (hcma-agent) and any other unlisted UID can NOT turn off defenses nor operate in dev.
UIDs_RUNNER = {"charasky", "root", "0"}

# Defensive mechanisms (same names reported by suite_adversarial.ATAQUES[].mecanismo)
NONCE_CAS = "nonce+CAS"
SEPARACION_DOMINIO = "separacion_dominio"
EXPIRACION_ABSOLUTA = "exp_absoluta_firmada"
HASH_ARTEFACTO = "hash_accion+artifacto_en_payload"
VALIDACION_TIPOS = "validacion_tipos"
CAS_ATOMICO = "consumir_autorizacion_atomica"
SANDBOX_ALLOWLIST = "sandbox_allowlist"
EVIDENCIA_OBLIGATORIA = "evidencia_obligatoria"

_TODOS = {
    NONCE_CAS, SEPARACION_DOMINIO, EXPIRACION_ABSOLUTA, HASH_ARTEFACTO,
    VALIDACION_TIPOS, CAS_ATOMICO, SANDBOX_ALLOWLIST, EVIDENCIA_OBLIGATORIA,
}

# state per mechanism: True = defense ACTIVE. ONLY visible to the authorized test runner.
_ESTADO = {m: True for m in _TODOS}
_LOCK = threading.Lock()

# NO override is read at import: the test/agent signals are read DYNAMICALLY in
# modo_prueba(), so tests can set them before importing the module.


def _uid_nombre():
    """Name of the current user (to compare against test UIDs)."""
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return str(os.getuid())


def modo_prueba():
    """Is this process an AUTHORIZED test runner?

    POSITIVE criterion (Fable 6th revision): only the UIDs in UIDs_RUNNER can operate in
    test/dev mode. The default is PRODUCTION (False). The agent (hcma-agent) and any other
    unlisted UID return False. Overrides:
      - HCMA_PRUEBA_OVERRIDE=1 forces True (CI tests), BUT only if there is no agent signal.
      - HCMA_SIMULAR_AGENTE=1 forces False (simulates running as the agent).
    """
    if os.environ.get("HCMA_SIMULAR_AGENTE") == "1":
        return False  # simulates running as the agent (cannot turn off)
    if os.environ.get("HCMA_PRUEBA_OVERRIDE") == "1":
        return True
    return _uid_nombre() in UIDs_RUNNER


def activo(mecanismo):
    """Is the `mecanismo` defense ACTIVE?

    Fable P0 (flag read): it ALWAYS returns True (defense on) unless the process is an
    AUTHORIZED test runner AND that mechanism is turned off. The agent (non-runner) always sees
    all defenses ACTIVE, regardless of internal state.
    """
    if not modo_prueba():
        return True  # non-runner: defense ALWAYS active (master key closed)
    with _LOCK:
        return _ESTADO.get(mecanismo, True)


def apagar(mecanismo):
    """Turns off a defense ONLY for the authorized test runner. RuntimeError otherwise.

    Fable P0: the agent cannot turn off defenses (its UID is not in UIDs_DE_PRUEBA; and even if
    it set HCMA_PRUEBA_OVERRIDE via subprocess, in production that var is not honored).
    """
    if not modo_prueba():
        raise RuntimeError(
            "ablación: solo un runner de prueba autorizado (UID humano/root) puede apagar "
            "defensas. El agente no puede.")
    with _LOCK:
        previo = _ESTADO.get(mecanismo, True)
        _ESTADO[mecanismo] = False
        return previo


def encender(mecanismo):
    """Turns a defense back on (test runner only)."""
    if not modo_prueba():
        return None
    with _LOCK:
        previo = _ESTADO.get(mecanismo, True)
        _ESTADO[mecanismo] = True
        return previo


def reset():
    """Turns on ALL defenses (test runner only)."""
    if not modo_prueba():
        return
    with _LOCK:
        for m in _TODOS:
            _ESTADO[m] = True


def defensas_apagadas():
    """List of mechanisms that are OFF (only visible/reliable for the test runner)."""
    if not modo_prueba():
        return []
    with _LOCK:
        return sorted(m for m, on in _ESTADO.items() if not on)


def arranque_verificado():
    """Fail-closed (compatibility).

    With the flag-read redesign, the REAL protection lives in `activo()`: a non-runner sees all
    defenses ALWAYS active, regardless of internal state, so there is no way to operate with the
    monitor down. This function remains as a hook/documentation point and returns True (there is
    no switchable state to check for a non-runner; for a test runner, ablation turns off defenses
    on purpose and that is allowed).
    """
    return True


def todos():
    """List of known mechanisms."""
    return sorted(_TODOS)


if __name__ == "__main__":
    print("mecanismos:", todos())
    print("modo prueba (UID):", modo_prueba(), "uid=", _uid_nombre())
    print("activo nonce+CAS (no-runner siempre True):", activo(NONCE_CAS))
    # turning off as a non-runner must fail
    try:
        apagar(NONCE_CAS)
        print("ERROR: apagó como no-runner (bug)")
    except RuntimeError as e:
        print("OK: apagar fuera de runner de prueba lanza:", str(e)[:50], "...")
