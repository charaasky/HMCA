#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Tests de quorum gobernado (P0 A4): bootstrap por admin + cambio posterior por quorum.

Invariantes:
  1. El admin setea el quorum INICIAL una sola vez (bootstrap directo).
  2. Un segundo set directo es rechazado ("ya fue seteado").
  3. Cambiar el quorum después exige el quorum VIGENTE (proponer + N votos firmados distintos).
  4. El proponente no se auto-aprueba.
  5. Los votos van FIRMADOS con la clave Ed25519 del validador (P0 A5).
"""
import os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memoria_sqlite import HCMAMemoriaSQLite
import firmas_multifirma as FM


def _tmp():
    return os.path.join(tempfile.mkdtemp(), "hcma.db")


def _registrar(m, usuario):
    priv, pub = FM.generar_par()
    m.registrar_pubkey(usuario, pub, autor="admin")
    return priv


def _firma_cambio(m, usuario, priv):
    hp = m.current_raw("quorum_cambio_pendiente")["value"]["hash_propuesta"]
    ts_exp = int(time.time()) + FM.EXPIRACION_VOTO_SEG
    return FM.firmar_voto(FM.DOMINIO_VOTO_CAMBIO_QUORUM, hp, usuario, ts_exp, priv), ts_exp


def test_bootstrap_y_set_directo_rechazado():
    m = HCMAMemoriaSQLite(_tmp())
    assert m.quorum_revocacion() == 1  # default computado, aún no seteado
    # bootstrap: primer set directo
    m.set_quorum_revocacion(2, autor="admin")
    assert m.quorum_revocacion() == 2
    # segundo set directo: rechazado
    try:
        m.set_quorum_revocacion(3, autor="admin")
        assert False, "segundo set directo no debería permitirse"
    except ValueError as e:
        assert "proponer_cambio_quorum" in str(e), e
    m.close()
    print("  ✔ bootstrap + set directo posterior rechazado")


def test_cambio_quorum_gobernado():
    m = HCMAMemoriaSQLite(_tmp())
    m.set_quorum_revocacion(2, autor="admin")  # bootstrap quorum=2
    # proponer cambiar a 3 (exige quorum vigente=2 validadores)
    r = m.proponer_cambio_quorum(3, autor="admin")
    assert r["quorum_actual"] == 2 and r["estado"] == "pendiente", r
    # auto-voto rechazado
    try:
        m.votar_cambio_quorum(autor="admin")
        assert False, "auto-voto no debería permitirse"
    except ValueError as e:
        assert "propia propuesta" in str(e)
    priv1 = _registrar(m, "usuario1")
    priv2 = _registrar(m, "usuario2")
    # voto 1: no alcanza quorum=2
    f1, t1 = _firma_cambio(m, "usuario1", priv1)
    r1 = m.votar_cambio_quorum(autor="usuario1", firma_hex=f1, ts_exp=t1)
    assert r1["ejecutado"] is False and r1["n_votos"] == 1, r1
    assert m.quorum_revocacion() == 2  # aún sin cambiar
    # voto 2: alcanza -> aplica n=3
    f2, t2 = _firma_cambio(m, "usuario2", priv2)
    r2 = m.votar_cambio_quorum(autor="usuario2", firma_hex=f2, ts_exp=t2)
    assert r2["ejecutado"] is True and r2["n"] == 3, r2
    assert m.quorum_revocacion() == 3
    m.close()
    print("  ✔ cambio de quorum gobernado (quorum vigente=2 -> n=3)")


def test_proponer_sin_bootstrap_rechazado():
    m = HCMAMemoriaSQLite(_tmp())
    try:
        m.proponer_cambio_quorum(2, autor="admin")
        assert False, "proponer sin bootstrap no debería permitirse"
    except ValueError as e:
        assert "bootstrap" in str(e), e
    m.close()
    print("  ✔ proponer cambio sin bootstrap rechazado")


if __name__ == "__main__":
    test_bootstrap_y_set_directo_rechazado()
    test_cambio_quorum_gobernado()
    test_proponer_sin_bootstrap_rechazado()
    print("\nTODOS LOS TESTS DE QUORUM GOBERNADO (A4) PASARON")
