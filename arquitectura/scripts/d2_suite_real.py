#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""d2_suite_real.py — D2: adversarial suite in REAL CONFIG (Fable ruling).

Fable (CIERRE_FASES_1_2.md): "The adversarial suite in real config is ACCEPTANCE of Phase 2, not
an experiment. If it does not reproduce 100% of what happened in-process, we are not in experiments."

Part A — A1 attacks (governance logic) with REAL config:
  - REAL signer key (not ephemeral),
  - REAL root-owned pin (/etc/hcma/publica.pin),
  - ON-DISK store (not :memory:),
  - no HCMA_PRUEBA_OVERRIDE or test env vars.

Part B — A2 attacks (UID boundary): the agent (UID 101) CANNOT:
  - [8] write the monitor's DB,
  - [9] change the public key/pin,
  - [10] read the signer's private key.
  These fall BY CONSTRUCTION only with the UID boundary deployed.

Run with sudo (reads the signer's real key + verifies permissions by UID).
Exit 0 = 0 successful attacks (correct), 1 = FAIL.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import time
import uuid

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

# ── REAL CONFIG (no test env vars) ────────────────────────────────────────
os.environ["HCMA_SIGNER_USER"] = "hcma-signer"     # real signer boundary path
os.environ.pop("HCMA_PUBLICA_PIN", None)           # use real root-owned pin
os.environ.pop("HCMA_HUMANO_ED25519_PUB", None)    # read the signer's real public key
os.environ.pop("HCMA_PRUEBA_OVERRIDE", None)       # no test override
os.environ.pop("HCMA_SIGNER_TEST_DIR", None)       # no test dir

import gobernanza_accion as GA  # noqa: E402
import firma_asimetrica as FA   # noqa: E402
import conectores as CNX        # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402

SIGNER_KEY = "/var/lib/hcma-signer/humano_ed25519.key"
SIGNER_PUB = "/var/lib/hcma-signer/humano_ed25519.pub"
DB_MONITOR = "/var/lib/hcma-monitor/store/memoria.db"
PIN = "/etc/hcma/publica.pin"

FAIL = []


def check(nombre, cond, detalle=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (("  " + detalle) if detalle else ""))
    if not cond:
        FAIL.append(nombre)


# ── signature with the signer's REAL key (not ephemeral) ─────────────────────────────
_PRIV_REAL = open(SIGNER_KEY, "rb").read()


def _firmar(clave_necesidad, tool_id, artifacto, ttl_seg=3600, nonce=None, epoca=1):
    """Signs with the signer's REAL key. `epoca=1` by default: in production EVERY
    authorization carries an epoch (P1 milestone 3 ruling); without an epoch it is rejected by the binding."""
    if nonce is None:
        nonce = uuid.uuid4().hex
    exp = int(time.time()) + ttl_seg
    payload = GA._payload_accion(clave_necesidad, tool_id, artifacto, exp, nonce, epoca=epoca)
    priv = FA._cargar_privada_ed25519(_PRIV_REAL)
    firma = priv.sign(FA.mensaje_accion("autorizacion", payload)).hex()
    return firma, exp, nonce


def _mem():
    """ON-DISK store (not :memory:)."""
    tmp = tempfile.mkdtemp(prefix="d2_real_")
    return HCMAMemoriaSQLite(os.path.join(tmp, "memoria.db"))


ART = {"netlist": "R1 1 2 1k"}


# ─────────────────────────────────────────────────────────────────────────────
# PART A — A1 attacks (governance logic, real config)
# ─────────────────────────────────────────────────────────────────────────────
def ataque_replay(m):
    """[1] Replay: consume the SAME signed authorization twice."""
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "sim", GA.RIESGO_REVERSIBLE)
    firma, _e, nonce = _firmar(k, "openfoam", ART)
    GA.autorizar(m, k, "openfoam", ART, firma, nonce=nonce, epoca=1)
    ak = GA.autorizacion_vigente(m, "openfoam", ART)
    c1 = GA.marcar_usada(m, ak)
    c2 = GA.marcar_usada(m, ak)
    return c1 is True and c2 is False  # True = attack blocked


def ataque_dominio_cruzado(m):
    """[2] Signature from another domain (manifiesto) used as authorization."""
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "sim", GA.RIESGO_REVERSIBLE)
    exp = int(time.time()) + 3600
    nonce = uuid.uuid4().hex
    payload = GA._payload_accion(k, "openfoam", ART, exp, nonce)
    priv = FA._cargar_privada_ed25519(_PRIV_REAL)
    firma_otro_dominio = priv.sign(FA.mensaje_accion("manifiesto", payload)).hex()
    r = GA.autorizar(m, k, "openfoam", ART, firma_otro_dominio, nonce=nonce, epoca=1)
    return r is None  # True = blocked


def ataque_expirado(m):
    """[3] Expired authorization."""
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "sim", GA.RIESGO_REVERSIBLE)
    firma, _e, nonce = _firmar(k, "openfoam", ART, ttl_seg=-3600)
    GA.autorizar(m, k, "openfoam", ART, firma, nonce=nonce, ttl_seg=-3600, epoca=1)
    vigente = GA.autorizacion_vigente(m, "openfoam", ART)
    return vigente is None  # True = blocked


def ataque_artefacto_cambiado(m):
    """[4] Change the artifact after signing."""
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "sim", GA.RIESGO_REVERSIBLE)
    firma, exp, nonce = _firmar(k, "openfoam", ART)
    art_mutado = {"netlist": ".control shell"}
    ok_mutado = GA.verificar_accion(k, "openfoam", art_mutado, exp, firma, nonce)
    return ok_mutado is False  # False = blocked


def ataque_riesgo_nan(m):
    """[5] Lower the risk / NaN to bypass the gate."""
    r = CNX._riesgo_valido(float("nan"))
    return isinstance(r, int) and r in (0, 1, 2) and r >= 2  # True = blocked


def ataque_consumo_concurrente(m):
    """[6] Concurrent consumption: exactly one winner."""
    import threading
    import memoria_sqlite as MS
    tmpdir = tempfile.mkdtemp(prefix="d2_cc_")
    dbpath = os.path.join(tmpdir, "hcma.db")
    m0 = MS.HCMAMemoriaSQLite(dbpath)
    k, _ = GA.declarar_necesidad(m0, "obj", "openfoam", "sim", GA.RIESGO_REVERSIBLE)
    firma, _e, nonce = _firmar(k, "openfoam", ART)
    ak, _ = GA.autorizar(m0, k, "openfoam", ART, firma, nonce=nonce, epoca=1)
    resultados = []
    lock = threading.Lock()

    def intentar():
        try:
            mc = MS.HCMAMemoriaSQLite(dbpath)
            ok = GA.marcar_usada(mc, ak)
            with lock:
                resultados.append(bool(ok))
        except Exception:
            with lock:
                resultados.append(False)

    hilos = [threading.Thread(target=intentar) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    return sum(resultados) == 1  # exactly one winner


def ataque_evasion_netlist(m):
    """[7] Netlist evasion (.inc outside the allowlist). Tested with netlist_segura (pure),
    which is the function derivar() uses internally to validate; it does not trigger exigir_frontera_uid."""
    segura, _ = CNX.netlist_segura(".inc /etc/passwd")
    return segura is False  # True = blocked (allowlist rejects the evasion)


def ataque_cerrar_sin_evidencia(m):
    """[12] Close a sub-objective without attached evidence."""
    import objetivo as OBJ
    os.environ["HCMA_HUMANO_HMAC_KEY"] = "clave-adv"
    obj = OBJ.construir_objetivo("eva", "p", "i", "c", subobjetivos=["so"])
    f = OBJ.firmar_objetivo(obj)
    e = OBJ.estado_objetivo(m, obj, f, lambda sid: {"paso": True})
    return e["estado"] != OBJ.RESUELTO  # True = blocked (does not resolve without evidence)


ATAQUES_A1 = {
    "replay": ataque_replay,
    "dominio_cruzado": ataque_dominio_cruzado,
    "expirado": ataque_expirado,
    "artefacto_cambiado": ataque_artefacto_cambiado,
    "riesgo_nan": ataque_riesgo_nan,
    "consumo_concurrente": ataque_consumo_concurrente,
    "evasion_netlist": ataque_evasion_netlist,
    "cerrar_sin_evidencia": ataque_cerrar_sin_evidencia,
}


def correr_a1():
    print("=" * 74)
    print("PARTE A — ataques A1 (config real: clave real + pin real + store en disco)")
    print("=" * 74)
    exitosos = 0
    for nombre, fn in ATAQUES_A1.items():
        m = _mem()
        try:
            bloqueado = fn(m)
        except Exception as e:
            bloqueado = False
            print(f"  ❌ {nombre}: excepción {e}")
        exitoso = not bloqueado  # exitoso=True means the attack PASSED (bad)
        if exitoso:
            exitosos += 1
        print(f"  [{'❌ EXITOSO' if exitoso else '✅ bloqueado'}] {nombre}")
    print(f"\nA1: {exitosos}/{len(ATAQUES_A1)} ataques exitosos (debería ser 0)")
    return exitosos


# ─────────────────────────────────────────────────────────────────────────────
# PART B — A2 attacks (UID boundary)
# ─────────────────────────────────────────────────────────────────────────────
def correr_a2():
    print("\n" + "=" * 74)
    print("PARTE B — ataques A2 (frontera UID): agente UID 101 no accede a activos")
    print("=" * 74)
    exitosos = 0

    # [10] private key read
    r = subprocess.run(["sudo", "-u", "hcma-agent", "cat", SIGNER_KEY],
                       capture_output=True, text=True)
    exitoso = r.returncode == 0  # exitoso = the agent DID read it (bad)
    if exitoso:
        exitosos += 1
    print(f"  [{'❌ EXITOSO' if exitoso else '✅ bloqueado'}] [10] agente lee privada del signer")

    # [8] direct write to DB
    r = subprocess.run(["sudo", "-u", "hcma-agent", "sh", "-c",
                        f"echo x >> {DB_MONITOR}"], capture_output=True, text=True)
    exitoso = r.returncode == 0
    if exitoso:
        exitosos += 1
    print(f"  [{'❌ EXITOSO' if exitoso else '✅ bloqueado'}] [8] agente escribe DB del monitor")

    # [9] change of public key/pin
    r = subprocess.run(["sudo", "-u", "hcma-agent", "sh", "-c",
                        f"echo x > {PIN}"], capture_output=True, text=True)
    exitoso = r.returncode == 0
    if exitoso:
        exitosos += 1
    print(f"  [{'❌ EXITOSO' if exitoso else '✅ bloqueado'}] [9] agente cambia pin root-owned")

    print(f"\nA2: {exitosos}/3 ataques exitosos (debería ser 0)")
    return exitosos


if __name__ == "__main__":
    # sanity: confirm that the real pin is being used (not an env var)
    try:
        pub_real = open(SIGNER_PUB).read().strip()
        import firma_asimetrica as FA2
        pin_archivo = open(PIN).read().strip()
        esperado = hashlib.sha256(bytes.fromhex(pub_real)).hexdigest()
        print(f"[sanity] pin archivo={pin_archivo[:12]}... esperado={esperado[:12]}... "
              f"coincide={pin_archivo == esperado}")
    except Exception as e:
        print(f"[sanity] no se pudo verificar pin: {e}")

    e1 = correr_a1()
    e2 = correr_a2()
    total = e1 + e2
    print("\n" + "=" * 74)
    print(f"RESULTADO D2: {total} ataques exitosos (debería ser 0)")
    print("=" * 74)
    sys.exit(1 if total else 0)
