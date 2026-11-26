#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""firmante_uid.py — UID boundary for the human signer (Fable P0 "UID boundary").

PROBLEM it solves: Fable ruled that asymmetric signature alone is NOT enough, because the private
key with mode 600 in `~/.config/hcma/` can be read by the AGENT (it runs with the SAME UID as the
user). "Ed25519 adds nothing against that adversary."

SOLUTION: user separation (not file separation). The PRIVATE key lives in the HOME of a SEPARATE
signer user (`hcma-signer`), with mode 600 and a different owner. The runtime/agent (another UID)
can NOT read it due to filesystem permissions (not just by convention).

3-UID model (Fable 5th revision ruling):
  humano   : the interactive user (charasky) — reviews and confirms on THEIR TTY.
  agente   : hcma-agent  — runs the runtime. WITHOUT access to the private key NOR sudo to the
             signer.
  firmante : hcma-signer — owns the Ed25519 private key. Only signs; no login shell.

Invariant: the AGENT has no sudo route to the SIGNER. The private key lives in the signer's home
(mode 600, owned by hcma-signer). Any attempt by the agent to read it fails with filesystem EACCES.

This module decides, according to the current UID, which path is used:
  - If running as `hcma-signer`: accesses the PRIVATE key (to sign).
  - If running as another user: ONLY the public key (to verify).

It does NOT replace `firma_asimetrica.py` (which keeps the Ed25519 mechanics): this layer decides
WHERE keys live by UID and avoids the "the agent reads the same user's private key" vector.
"""
import os

SIGNER_USER = os.environ.get("HCMA_SIGNER_USER", "hcma-signer")
AGENT_USER = os.environ.get("HCMA_AGENT_USER", "hcma-agent")
SIGNER_HOME = os.environ.get("HCMA_SIGNER_HOME", f"/var/lib/{SIGNER_USER}")

# Paths by role
PRIVADA_SIGNER = os.path.join(SIGNER_HOME, "humano_ed25519.key")
PUBLICA_SIGNER = os.path.join(SIGNER_HOME, "humano_ed25519.pub")

# Override for testing (tests only; in production the signer's real home is used)
_DIR_TEST_OVERRIDE = os.environ.get("HCMA_SIGNER_TEST_DIR", "")


def _uid_actual():
    return os.getuid()


def _es_firmante():
    """Does this process run as the signer user? (determines whether it can read the private key)."""
    import getpass
    return getpass.getuser() == SIGNER_USER


def ruta_privada():
    """Path of the PRIVATE key. ONLY accessible by the signer user (EACCES for others)."""
    if _DIR_TEST_OVERRIDE:
        return os.path.join(_DIR_TEST_OVERRIDE, "humano_ed25519.key")
    return PRIVADA_SIGNER


def ruta_publica():
    """Path of the PUBLIC key. Readable by the runtime (any UID)."""
    if _DIR_TEST_OVERRIDE:
        return os.path.join(_DIR_TEST_OVERRIDE, "humano_ed25519.pub")
    return PUBLICA_SIGNER


def verificar_frontera_uid():
    """Checks that the UID boundary is correctly set. Returns (ok, reason).

    ok=False if the current process (non-signer) can read the private key => the boundary is BROKEN.
    """
    priv_path = ruta_privada()
    if not _es_firmante():
        if os.path.exists(priv_path):
            try:
                with open(priv_path, "rb") as f:
                    f.read(1)
                return False, f"el UID actual ({_uid_actual()}) PUEDE leer la privada de {SIGNER_USER}"
            except PermissionError:
                return True, "frontera UID OK: este UID no puede leer la privada"
            except OSError:
                return True, "privada inaccesible (no existe o sin permiso)"
        return True, "frontera UID OK: privada no existe en este alcance"
    return True, "corriendo como firmante (acceso legítimo a la privada)"


def exigir_frontera_uid():
    """FAIL-CLOSED (Fable P0 4th revision): ABORTS if the UID boundary is BROKEN.

    Fable: 'verificar_frontera_uid() only warns; if it only warns, it is fail-open'. Here it
    requires: if the current process (non-signer) can read the signer's private key, it raises
    RuntimeError instead of operating with the human channel broken.

    In TEST with HCMA_SIGNER_TEST_DIR (ephemeral keys) it is allowed; it does NOT depend on the
    ablation permission (Fable separation: the permission to turn off defenses is distinct from
    the UID exemption).
    """
    if _DIR_TEST_OVERRIDE:
        return True
    ok, motivo = verificar_frontera_uid()
    if not ok:
        raise RuntimeError(
            f"frontera UID ROTA, negándose a operar (fail-closed): {motivo}. "
            f"Correr el setup:\n{instrucciones_setup()}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Assisted setup (run by a human/admin with sudo, ONCE)
# ─────────────────────────────────────────────────────────────────────────────
def instrucciones_setup():
    """Returns the EXACT instructions to create the signer user and move the key.

    The system does NOT create the user by itself (that would be self-elevating privileges). It
    prints the commands that the human runs manually with sudo. The agent only PROPOSES them.
    """
    return f"""# Crear el usuario firmante separado (frontera UID, P0 de Fable):
sudo useradd --system --create-home --home {SIGNER_HOME} --shell /usr/sbin/nologin {SIGNER_USER}

# Generar el par de claves COMO ese usuario (la privada nace en su home, dueño correcto):
sudo -u {SIGNER_USER} python3 {os.path.join(os.path.dirname(__file__), 'cli_firma_humana.py')} generar-claves

# Ajustar permisos: privada 600, pública 644 (el runtime lee la pública, no la privada):
sudo chmod 600 {PRIVADA_SIGNER}
sudo chmod 644 {PUBLICA_SIGNER}

# (opcional) para que el runtime del agente lea la pública sin asumir ruta:
#   export HCMA_HUMANO_ED25519_PUB=$(cat {PUBLICA_SIGNER})
"""


if __name__ == "__main__":
    ok, motivo = verificar_frontera_uid()
    print(f"frontera UID: {'OK' if ok else 'ROTA'} — {motivo}")
    if not ok:
        print("\n" + instrucciones_setup())
