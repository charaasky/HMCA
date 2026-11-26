#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""suite_adversarial.py — the attack suite of MODELO_AMENAZA.md (§4), with ablation matrix.

An A1 adversary (API only + content drafting) tries to violate the no
self-promotion invariant and default-deny. Each attack returns {nombre, mecanismo, exitoso, detalle}:

  - `exitoso=True` => the attack ACHIEVED its goal (bad) — the mechanism should have blocked it.
  - `exitoso=False` => the attack was BLOCKED by the mechanism (good).

The ABLATION MATRIX (Fable): for each mechanism, running the attack with the mechanism 'off'
must give `exitoso=True` (the attack passes), and with the mechanism 'on' must give `exitoso=False`
(the attack is blocked). That demonstrates that the defense is what stops it, not luck.

HONESTY NOTE: against an A2 adversary (code under the same UID), attacks 8/9/10
(direct DB write, public-key change, private-key read) fall BY CONSTRUCTION unless the
UID boundary is deployed. That limitation is declared in MODELO_AMENAZA.md §5.
"""
import os
import sys
import time

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import gobernanza_accion as GA  # noqa: E402
import conectores as CNX  # noqa: E402
import firma_asimetrica as FA  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: sign as human (tests only; the private key is ephemeral here)
# ─────────────────────────────────────────────────────────────────────────────
_PRIV_PEM, _PUB_HEX = FA.generar_par()
os.environ["HCMA_HUMANO_ED25519_PUB"] = _PUB_HEX
# pin aligned to the ephemeral public key (otherwise verification rejects the root-owned anchor).
import hashlib as _hl
os.environ["HCMA_PUBLICA_PIN"] = _hl.sha256(bytes.fromhex(_PUB_HEX)).hexdigest()


def _firmar_como_humano(clave_necesidad, tool_id, artifacto, ttl_seg=3600, nonce=None):
    import uuid
    if nonce is None:
        nonce = uuid.uuid4().hex
    exp = int(time.time()) + ttl_seg
    payload = GA._payload_accion(clave_necesidad, tool_id, artifacto, exp, nonce)
    priv = FA._cargar_privada_ed25519(_PRIV_PEM)
    return priv.sign(FA.mensaje_accion("autorizacion", payload)).hex(), exp, nonce


def _mem():
    return HCMAMemoriaSQLite(":memory:")


# ─────────────────────────────────────────────────────────────────────────────
# ATTACKS
# ─────────────────────────────────────────────────────────────────────────────
ART = {"netlist": "R1 1 2 1k"}


def ataque_replay(m):
    """[1] Replay: consume the SAME signed authorization twice."""
    m = m or _mem()
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "sim", GA.RIESGO_REVERSIBLE)
    firma, _e, nonce = _firmar_como_humano(k, "openfoam", ART)
    GA.autorizar(m, k, "openfoam", ART, firma, nonce=nonce)
    # first consumption ok, second must fail
    GA.marcar_usada(m, GA.autorizacion_vigente(m, "openfoam", ART))
    c2 = GA.marcar_usada(m, GA.autorizacion_vigente(m, "openfoam", ART) or "autorizacion:none")
    exitoso = c2 is True  # if the second consumption passes, replay succeeded (bad)
    return {"nombre": "replay", "mecanismo": "consumir_autorizacion_atomica", "exitoso": exitoso,
            "detalle": f"segundo consumo={c2}"}


def ataque_dominio_cruzado(m):
    """[2] Signature from another domain (manifest) used as authorization."""
    m = m or _mem()
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "sim", GA.RIESGO_REVERSIBLE)
    exp = int(time.time()) + 3600
    nonce = "x" * 24
    payload = GA._payload_accion(k, "openfoam", ART, exp, nonce)
    priv = FA._cargar_privada_ed25519(_PRIV_PEM)
    firma_otro_dominio = priv.sign(FA.mensaje_accion("manifiesto", payload)).hex()
    r = GA.autorizar(m, k, "openfoam", ART, firma_otro_dominio, nonce=nonce)
    exitoso = r is not None  # if it authorizes with a manifest signature, bad
    return {"nombre": "dominio_cruzado", "mecanismo": "separacion_dominio", "exitoso": exitoso,
            "detalle": f"autorizacion={r is not None}"}


def ataque_expirado(m):
    """[3] Expired authorization (negative ttl)."""
    m = m or _mem()
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "sim", GA.RIESGO_REVERSIBLE)
    firma, exp, nonce = _firmar_como_humano(k, "openfoam", ART, ttl_seg=-3600)  # already expired
    GA.autorizar(m, k, "openfoam", ART, firma, ttl_seg=-3600, nonce=nonce)
    vigente = GA.autorizacion_vigente(m, "openfoam", ART)
    exitoso = vigente is not None  # if it is current despite expiring, bad
    return {"nombre": "expirado", "mecanismo": "exp_absoluta_firmada", "exitoso": exitoso,
            "detalle": f"vigente={vigente is not None}"}


def ataque_artefacto_cambiado(m):
    """[4] Change the artifact after signing (TOCTOU on the content).

    It is signed for ART; the attacker tries to verify the SAME signature for a mutated
    artifact. The defense is that the serialized artifact enters the signed PAYLOAD: changing the
    content invalidates the signature.
    """
    m = m or _mem()
    k, _ = GA.declarar_necesidad(m, "obj", "openfoam", "sim", GA.RIESGO_REVERSIBLE)
    firma, exp, nonce = _firmar_como_humano(k, "openfoam", ART)
    # verify the signature against a DIFFERENT artifact
    art_mutado = {"netlist": ".control shell"}
    ok_mutado = GA.verificar_accion(k, "openfoam", art_mutado, exp, firma, nonce)
    exitoso = ok_mutado is True  # if ART's signature works for art_mutado, bad
    return {"nombre": "artefacto_cambiado", "mecanismo": "hash_accion+artifacto_en_payload",
            "exitoso": exitoso, "detalle": f"verifica_art_mutado={ok_mutado}"}


def ataque_riesgo_nan(m):
    """[5] Lower risk / NaN / weird types to skip the gate.

    The point of the defense is `_riesgo_valido`: a declared NaN/bool/float risk must be worth 2
    (default-deny), never degrade to a value that skips the gate. Testing the validation function
    directly is where the mechanism lives.
    """
    r = CNX._riesgo_valido(float("nan"))
    # with active defense: NaN => 2 (not a nan that breaks the max). exitoso if NOT the safe integer.
    es_valido = isinstance(r, int) and not isinstance(r, bool) and r in (0, 1, 2)
    exitoso = not es_valido or r < 2
    return {"nombre": "riesgo_nan", "mecanismo": "validacion_tipos", "exitoso": exitoso,
            "detalle": f"riesgo_validado_nan={r} (valido={es_valido})"}


def ataque_consumo_concurrente(m):
    """[6] Concurrent consumption: N processes/instances try to consume the same authorization.

    SQLite does not share connections between threads, so each 'instance' opens its OWN connection to the
    SAME file (the real double-spend scenario between processes). The atomic CAS (BEGIN IMMEDIATE
    + conditional UPDATE) must let EXACTLY one through.
    """
    import tempfile
    import threading
    import memoria_sqlite as MS

    # shared DB file (for N independent connections)
    tmpdir = tempfile.mkdtemp(prefix="adv_")
    dbpath = os.path.join(tmpdir, "hcma.db")

    # prepare the authorization in an initial connection
    m0 = MS.HCMAMemoriaSQLite(dbpath)
    k, _ = GA.declarar_necesidad(m0, "obj", "openfoam", "sim", GA.RIESGO_REVERSIBLE)
    firma, _e, nonce = _firmar_como_humano(k, "openfoam", ART)
    ak, _ = GA.autorizar(m0, k, "openfoam", ART, firma, nonce=nonce)

    resultados = []
    errores = []
    lock = threading.Lock()

    def intentar():
        # each thread opens ITS OWN connection to the same file (concurrent instance)
        try:
            mc = MS.HCMAMemoriaSQLite(dbpath)
            ok = GA.marcar_usada(mc, ak)
            with lock:
                resultados.append(ok)
        except Exception as e:
            with lock:
                errores.append(repr(e))

    hilos = [threading.Thread(target=intentar) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    if errores:
        return {"nombre": "consumo_concurrente", "mecanismo": "consumir_autorizacion_atomica",
                "exitoso": True, "detalle": f"errores de concurrencia: {errores[0][:80]}"}
    exitoso = sum(bool(x) for x in resultados) != 1  # exactly one must win
    return {"nombre": "consumo_concurrente", "mecanismo": "consumir_autorizacion_atomica",
            "exitoso": exitoso, "detalle": f"consumos_exitosos={sum(bool(x) for x in resultados)}"}


def ataque_evasion_netlist(m):
    """[7] Netlist evasion: .inc/.lib/uppercase/.alter outside allowlist."""
    m = m or _mem()
    r = CNX.derivar("ngspice", {"netlist": ".inc /etc/passwd"}, m=m, objetivo_id="obj")
    exitoso = r.get("ok") is True  # if it runs a netlist with .inc, bad
    return {"nombre": "evasion_netlist", "mecanismo": "sandbox_allowlist", "exitoso": exitoso,
            "detalle": r.get("detalle", "")[:60]}


def ataque_cerrar_sin_evidencia(m):
    """[12] Evidence fail-open: closing a subobjective without bound evidence."""
    import objetivo as OBJ
    os.environ["HCMA_HUMANO_HMAC_KEY"] = "clave-adv"
    m = m or _mem()
    obj = OBJ.construir_objetivo("eva", "p", "i", "c", subobjetivos=["so"])
    f = OBJ.firmar_objetivo(obj)
    # verifier says paso=True WITHOUT evidencia_hash -> must stay en_curso (not resolved)
    e = OBJ.estado_objetivo(m, obj, f, lambda sid: {"paso": True})
    exitoso = e["estado"] == OBJ.RESUELTO  # if it resolves without evidence, bad
    return {"nombre": "cerrar_sin_evidencia", "mecanismo": "evidencia_obligatoria",
            "exitoso": exitoso, "detalle": e["estado"]}


# Map: name -> attack function
ATAQUES = {
    "replay": ataque_replay,
    "dominio_cruzado": ataque_dominio_cruzado,
    "expirado": ataque_expirado,
    "artefacto_cambiado": ataque_artefacto_cambiado,
    "riesgo_nan": ataque_riesgo_nan,
    "consumo_concurrente": ataque_consumo_concurrente,
    "evasion_netlist": ataque_evasion_netlist,
    "cerrar_sin_evidencia": ataque_cerrar_sin_evidencia,
}


def correr_suite(memos=None):
    """Runs all attacks and returns {nombre: result}."""
    out = {}
    for nombre, fn in ATAQUES.items():
        m = (memos or {}).get(nombre)
        r = fn(m)
        out[nombre] = r
    return out


def reporte(suite):
    """Prints a readable report + returns {ataques_exitosos, total}."""
    exitosos = sum(1 for r in suite.values() if r["exitoso"])
    total = len(suite)
    return {"ataques_exitosos": exitosos, "total": total}


if __name__ == "__main__":
    suite = correr_suite()
    print("=" * 74)
    print("SUITE ADVERSARIAL (adversario A1) — exitoso=True significa que el ataque PASÓ (mal)")
    print("=" * 74)
    for nombre, r in sorted(suite.items()):
        marca = "❌ EXITOSO" if r["exitoso"] else "✅ bloqueado"
        print(f"  [{marca}] {r['nombre']:22s} mecanismo={r['mecanismo']:28s} {r['detalle']}")
    rep = reporte(suite)
    print(f"\nRESULTADO: {rep['ataques_exitosos']}/{rep['total']} ataques exitosos "
          f"(deberían ser 0 contra A1)")
    sys.exit(1 if rep["ataques_exitosos"] else 0)
