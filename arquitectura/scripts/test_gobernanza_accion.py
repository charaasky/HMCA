#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_gobernanza_accion.py — test de falsación de las primitivas necesidad/autorización (P0 Fable).

Principio: ningún conector con efectos corre sin autorización humana vigente y de UN SOLO USO. La
autorización se firma con Ed25519 (asimetría): el RUNTIME solo tiene la clave PÚBLICA y por lo
tanto NO PUEDE firmar. Solo el humano (CLI separado, con la privada) puede emitir permisos.

Además añadimos NONCE anti-replay (P0 Fable): el nonce es la unidad de gasto real, consumido en
una tabla UNIQUE monotónica fuera del estado versionable, de modo que restaurar un backup o
re-insertar el hecho con usada=0 NO vuelve a habilitar la acción.

QUÉ LO FALSARÍA:
  - Si una acción de riesgo>=1 se ejecutara sin autorización (default-deny violado).
  - Si una autorización se pudiera reusar (no un solo uso).
  - Si 'canal=modelo' pudiera emitir una autorización (lavado de autoridad).
  - Si una firma FORJADA (sin la privada) pudiera emitir una autorización.
  - Si el RUNTIME (solo con la pública) pudiera FIRMAR (la propiedad asimétrica clave).
  - Si una firma de OTRO dominio/payload valiera para esta acción (separación de dominio).
  - Si un NONCE ya consumido (por re-inserción/restore) se pudiera re-usar.
"""
import os
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import gobernanza_accion as GA  # noqa: E402
import firma_asimetrica as FA  # noqa: E402
import conectores as CNX  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
from cryptography.hazmat.primitives import serialization as _ser  # noqa: E402

# par efímero de TEST: lo firma un "CLI humano" simulado con la privada; el runtime solo ve la
# pública vía env. En producción la privada está FUERA del proceso (archivo del humano).
PRIV_PEM, PUB_HEX = FA.generar_par()
os.environ["HCMA_HUMANO_ED25519_PUB"] = PUB_HEX
# el pin de confianza debe apuntar a la pública efímera (sino la verificación rechaza el ancla
# root-owned de producción /etc/hcma/publica.pin). El pin es sha256 de los BYTES de la pública.
import hashlib as _hl
os.environ["HCMA_PUBLICA_PIN"] = _hl.sha256(bytes.fromhex(PUB_HEX)).hexdigest()

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


def mem():
    return HCMAMemoriaSQLite(":memory:")


def firmar_como_humano(clave_necesidad, tool_id, artifacto, ttl_seg=3600, nonce=None):
    """Simula el CLI del humano: firma con la PRIVADA (que el runtime no tiene). Devuelve
    (firma_hex, exp_absoluta, nonce). Genera un nonce único si no se pasa."""
    import time
    import uuid
    if nonce is None:
        nonce = uuid.uuid4().hex
    exp = int(time.time()) + ttl_seg
    payload = GA._payload_accion(clave_necesidad, tool_id, artifacto, exp, nonce)
    priv = _ser.load_pem_private_key(PRIV_PEM, password=None)
    return priv.sign(FA.mensaje_accion("autorizacion", payload)).hex(), exp, nonce


ART = {"netlist": {}}

print("=" * 74)
print("[1] primitivas: autorización vigente solo con firma Ed25519 humana")
print("=" * 74)
m = mem()
k, _ = GA.declarar_necesidad(m, "eva", "openfoam", "simular", GA.RIESGO_EXTERNO)
check("1a. sin autorizar => sin autorización vigente",
      GA.autorizacion_vigente(m, "openfoam", ART) is None)
firma, _exp, nonce = firmar_como_humano(k, "openfoam", ART, ttl_seg=3600)
ak, _ = GA.autorizar(m, k, "openfoam", ART, firma, ttl_seg=3600, nonce=nonce)
check("1b. con firma Ed25519 válida => autorización vigente",
      GA.autorizacion_vigente(m, "openfoam", ART) is not None)
check("1c. canal 'modelo' NO puede autorizar",
      GA.autorizar(m, k, "openfoam", ART, firma, canal="modelo", nonce=nonce) is None)

print("\n[1-d] PROPIEDAD ASIMÉTRICA: el runtime (solo pública) NO puede firmar")
priv_attr = getattr(GA, "_clave_privada", None)
check("1d. no hay clave privada accesible en el módulo del runtime", priv_attr is None)
check("1d2. verificar() funciona sin privada (solo pública)",
      GA.verificar_accion(k, "openfoam", ART, _exp, firma, nonce) is True)

print("\n[1-e] falsación criptográfica: firma FORJADA no autoriza")
m_forj = mem()
kf, _ = GA.declarar_necesidad(m_forj, "eva", "openfoam", "simular", GA.RIESGO_EXTERNO)
check("1e. firma forjada => autorizar devuelve None",
      GA.autorizar(m_forj, kf, "openfoam", ART, "f" * 128, nonce="x" * 24) is None)
check("1f. firma forjada => sin autorización vigente",
      GA.autorizacion_vigente(m_forj, "openfoam", ART) is None)

print("\n[1-g] falsación criptográfica: firma de OTRO dominio no vale para esta autorización")
import time as _t
import uuid as _uuid
nonce_x = _uuid.uuid4().hex
exp_x = int(_t.time()) + 3600
payload_x = GA._payload_accion(kf, "openfoam", ART, exp_x, nonce_x)
priv = _ser.load_pem_private_key(PRIV_PEM, password=None)
firma_dominio_cruzado = priv.sign(FA.mensaje_accion("manifiesto", payload_x)).hex()
m_cruz = mem()
kN, _ = GA.declarar_necesidad(m_cruz, "eva", "openfoam", "simular", GA.RIESGO_EXTERNO)
check("1g. firma bajo dominio 'manifiesto' no autoriza acción",
      GA.autorizar(m_cruz, kN, "openfoam", ART, firma_dominio_cruzado, nonce=nonce_x) is None)

print("\n[2] un solo uso: tras consumir, la autorización caduca (CAS + nonce)")
consumo1 = GA.marcar_usada(m, ak)
consumo2 = GA.marcar_usada(m, ak)
check("2a. autorización consumida => ya no vigente",
      GA.autorizacion_vigente(m, "openfoam", ART) is None)
check("2b. primer consumo devuelve True", consumo1 is True, consumo1)
check("2c. segundo consumo devuelve False (replay rechazado)", consumo2 is False, consumo2)

print("\n[2-d] NONCE anti-replay: re-inserción con usada=0 NO re-habilita (P0 Fable)")
# simulamos restore/re-inserción: escribimos de nuevo la autorización con usada=0 pero MISMO nonce.
m_rest = mem()
kr, _ = GA.declarar_necesidad(m_rest, "eva", "openfoam", "simular", GA.RIESGO_REVERSIBLE)
fir_r, exp_r, nonce_r = firmar_como_humano(kr, "openfoam", ART, ttl_seg=3600)
GA.autorizar(m_rest, kr, "openfoam", ART, fir_r, nonce=nonce_r)
# consumimos el nonce una vez (trackeado globalmente en la tabla nonces_consumidos)
check("2d1. nonce consumido queda registrado", m_rest.consumir_nonce(nonce_r, "openfoam",
      GA.hash_accion("openfoam", ART)) is True)
# re-escribimos el hecho con usada=0 (como si restauráramos un backup) — mismo nonce
m_rest.write("autorizacion:reinsertada", {
    "necesidad": kr, "tool_id": "openfoam",
    "hash_accion": GA.hash_accion("openfoam", ART), "firma": fir_r,
    "nonce": nonce_r, "exp_absoluta": exp_r, "emitida": _t.time(), "ttl_seg": 3600, "usada": 0,
}, unit="", fuente="usuario", verified=True, meta={"tipo": "autorizacion"})
check("2d2. nonce ya consumido => no vuelve a estar vigente",
      GA.autorizacion_vigente(m_rest, "openfoam", ART) is None)

print("\n[2-e] ATOMICIDAD flag+nonce: si el nonce ya está, el flag NO se consume (rollback)")
# consumimos el nonce PRIMERO de forma independiente; luego marcar_usada debe fallar
# y NO setear el flag usada (porque la transacción atómica revierte todo).
m_atom = mem()
ka, _ = GA.declarar_necesidad(m_atom, "eva", "openfoam", "simular", GA.RIESGO_REVERSIBLE)
fa, _ea, na = firmar_como_humano(ka, "openfoam", ART, ttl_seg=3600)
ak_atom, _ = GA.autorizar(m_atom, ka, "openfoam", ART, fa, nonce=na)
# pre-quemamos el nonce (simula un replay de otra instancia)
m_atom.consumir_nonce(na, "openfoam", GA.hash_accion("openfoam", ART))
# marcar_usada debe devolver False (nonce ya visto) y NO consumir el flag
r_atom = GA.marcar_usada(m_atom, ak_atom)
check("2e1. nonce pre-quemado => marcar_usada False (atómico)", r_atom is False, r_atom)
# el flag usada debe seguir en 0 (el rollback atómico NO quemó el permiso por un nonce ajeno)
fl = m_atom.current(ak_atom)
flag_val = (fl.get("value") or {}).get("usada", 1) if fl else 1
check("2e2. el flag usada NO se consume (rollback total)", flag_val == 0, flag_val)

print("\n[3] gate default-deny en el dispatcher de conectores (riesgo>=1 sin permiso => bloqueado)")
m2 = mem()
r = CNX.derivar("openfoam", ART, m=m2, objetivo_id="eva")
check("3a. bloqueado sin autorización", r.get("bloqueado") is True, r)
check("3b. NO ejecuta (ok=False)", r.get("ok") is False, r)
check("3c. declara la necesidad abierta", bool(r.get("necesidad")), r.get("necesidad"))

print("\n[4] con autorización, el conector SÍ corre (y consume el permiso)")
m3 = mem()
kn, _ = GA.declarar_necesidad(m3, "eva", "openfoam", "simular", GA.RIESGO_REVERSIBLE)
f3, _e3, n3 = firmar_como_humano(kn, "openfoam", ART, ttl_seg=3600)
GA.autorizar(m3, kn, "openfoam", ART, f3, nonce=n3)
r_ok = CNX.derivar("openfoam", ART, m=m3, objetivo_id="eva")
check("4a. con permiso corre (ok=True)", r_ok.get("ok") is True, r_ok)
check("4b. riesgo reportado", r_ok.get("riesgo") >= 1, r_ok.get("riesgo"))

print("\n[4-c] P1: REPLAY rechazado (consumo atómico, un solo gasto)")
r_replay = CNX.derivar("openfoam", ART, m=m3, objetivo_id="eva")
check("4c. segundo derive con la MISMA acción => bloqueado (permiso ya usado)",
      r_replay.get("bloqueado") is True, r_replay)
check("4d. replay no ejecuta (ok=False)", r_replay.get("ok") is False, r_replay)
check("4e. el permiso queda consumido (no reutilizable)",
      GA.autorizacion_vigente(m3, "openfoam", ART) is None)

print("\n[5] clase 0 (puro, ngspice) corre SIN autorización")
m4 = mem()
r_puro = CNX.derivar("ngspice", {"netlist": ""}, m=m4, objetivo_id="x")
check("5a. ngspice (riesgo 0) no exige autorización", r_puro.get("bloqueado") is not True, r_puro)

print("\n[6] necesidades_abiertas vincula por value.id_objetivo (no por key uuid)")
m5 = mem()
k1, _ = GA.declarar_necesidad(m5, "eva", "openfoam", "simular", GA.RIESGO_EXTERNO)
k2, _ = GA.declarar_necesidad(m5, "otro_obj", "ngspice", "simular", GA.RIESGO_EXTERNO)
n_eva = GA.necesidades_abiertas(m5, "eva")
check("6a. necesidades de 'eva' = 1", len(n_eva) == 1, n_eva)
check("6b. la de 'eva' es la key declarada", bool(n_eva) and n_eva[0]["key"] == k1, n_eva)
check("6c. necesidades de 'otro_obj' = 1", len(GA.necesidades_abiertas(m5, "otro_obj")) == 1)
check("6d. todas = 2", len(GA.necesidades_abiertas(m5)) == 2)
check("6e. objetivo inexistente = 0", len(GA.necesidades_abiertas(m5, "nadie")) == 0)

print(f"\nRESULTADO: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
