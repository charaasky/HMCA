#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Tests de firmas Ed25519 por validador (P0 A5): multi-firma REAL, no multi-fila.

Invariantes cubiertos:
  1. Voto sin pubkey registrada => rechazado (fail-closed).
  2. Voto sin firma => rechazado.
  3. Firma forjada => rechazado.
  4. Voto expirado => rechazado.
  5. Firma sobre OTRA propuesta (hash distinto) => rechazado.
  6. Voto firmado válido alcanza quorum y ejecuta el borrado.
  7. Separación de dominio: firma de cambio_quorum NO sirve como voto de revocación (y viceversa).
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402
import firmas_multifirma as FM  # noqa: E402


def _tmp():
    return os.path.join(tempfile.mkdtemp(), "hcma.db")


def _registrar(m, usuario):
    """Registra una clave nueva para `usuario` y devuelve (priv_pem, pub_hex)."""
    priv, pub = FM.generar_par()
    m.registrar_pubkey(usuario, pub, autor="admin")
    return priv, pub


def _firma_revocacion(m, key, usuario, priv):
    pend = m._revocacion_pendiente(key)
    assert pend is not None, "no hay propuesta pendiente"
    hp = pend["value"]["hash_propuesta"]
    ts_exp = int(time.time()) + FM.EXPIRACION_VOTO_SEG
    firma = FM.firmar_voto(FM.DOMINIO_VOTO_REVOCACION, hp, usuario, ts_exp, priv)
    return firma, ts_exp


def test_voto_sin_pubkey_rechazado():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("x", 1, fuente="usuario", verified=True)
    m.proponer_revocacion("x", autor="admin", motivo="m")
    try:
        m.votar_revocacion("x", autor="fantasma", firma_hex="0" * 128, ts_exp=int(time.time()) + 60)
        assert False, "voto sin pubkey registrada no debería permitirse"
    except ValueError as e:
        assert "sin clave pública registrada" in str(e), str(e)
    m.close()
    print("  ✔ voto sin pubkey registrada rechazado (fail-closed)")


def test_voto_sin_firma_y_firma_forjada_rechazados():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("x", 1, fuente="usuario", verified=True)
    m.proponer_revocacion("x", autor="admin", motivo="m")
    _registrar(m, "usuario1")
    # sin firma
    try:
        m.votar_revocacion("x", autor="usuario1", firma_hex=None, ts_exp=int(time.time()) + 60)
        assert False, "voto sin firma no debería permitirse"
    except ValueError as e:
        assert "sin firma" in str(e), str(e)
    # firma forjada
    try:
        m.votar_revocacion("x", autor="usuario1", firma_hex="0" * 128, ts_exp=int(time.time()) + 60)
        assert False, "firma forjada no debería permitirse"
    except ValueError as e:
        assert "firma inválida" in str(e), str(e)
    assert m.current("x") is not None  # nada se ejecutó
    m.close()
    print("  ✔ voto sin firma y firma forjada rechazados")


def test_voto_expirado_rechazado():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("x", 1, fuente="usuario", verified=True)
    m.proponer_revocacion("x", autor="admin", motivo="m")
    priv, _ = _registrar(m, "usuario1")
    pend = m._revocacion_pendiente("x")
    hp = pend["value"]["hash_propuesta"]
    ts_exp = int(time.time()) - 10  # ya expiró
    firma = FM.firmar_voto(FM.DOMINIO_VOTO_REVOCACION, hp, "usuario1", ts_exp, priv)
    try:
        m.votar_revocacion("x", autor="usuario1", firma_hex=firma, ts_exp=ts_exp)
        assert False, "voto expirado no debería permitirse"
    except ValueError as e:
        assert "expirado" in str(e), str(e)
    m.close()
    print("  ✔ voto expirado rechazado")


def test_firma_sobre_otra_propuesta_rechazada():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("a", 1, fuente="usuario", verified=True)
    m.write("b", 2, fuente="usuario", verified=True)
    m.proponer_revocacion("a", autor="admin", motivo="ma")
    m.proponer_revocacion("b", autor="admin", motivo="mb")
    priv, _ = _registrar(m, "usuario1")
    # firma para la propuesta de "a", pero intenta votar "b" (hash distinto)
    hp_a = m._revocacion_pendiente("a")["value"]["hash_propuesta"]
    ts_exp = int(time.time()) + 60
    firma = FM.firmar_voto(FM.DOMINIO_VOTO_REVOCACION, hp_a, "usuario1", ts_exp, priv)
    try:
        m.votar_revocacion("b", autor="usuario1", firma_hex=firma, ts_exp=ts_exp)
        assert False, "firma de otra propuesta no debería validar"
    except ValueError as e:
        assert "firma inválida" in str(e), str(e)
    m.close()
    print("  ✔ firma sobre otra propuesta (hash distinto) rechazada")


def test_separacion_de_dominio():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("x", 1, fuente="usuario", verified=True)
    m.proponer_revocacion("x", autor="admin", motivo="m")
    priv, _ = _registrar(m, "usuario1")
    hp = m._revocacion_pendiente("x")["value"]["hash_propuesta"]
    ts_exp = int(time.time()) + 60
    # firma como cambio_quorum (dominio equivocado) sobre el mismo hash
    firma = FM.firmar_voto(FM.DOMINIO_VOTO_CAMBIO_QUORUM, hp, "usuario1", ts_exp, priv)
    try:
        m.votar_revocacion("x", autor="usuario1", firma_hex=firma, ts_exp=ts_exp)
        assert False, "firma de otro dominio no debería validar"
    except ValueError as e:
        assert "firma inválida" in str(e), str(e)
    m.close()
    print("  ✔ separación de dominio (cambio_quorum ≠ revocación)")


def test_flujo_firmado_completo_ejecuta():
    m = HCMAMemoriaSQLite(_tmp())
    m.set_quorum_revocacion(2, autor="admin")
    m.write("color", "gris", fuente="usuario", verified=True)
    m.proponer_revocacion("color", autor="admin", motivo="dato malo")
    priv1, _ = _registrar(m, "usuario1")
    priv2, _ = _registrar(m, "usuario2")
    # voto 1 firmado (no alcanza quorum=2)
    f1, t1 = _firma_revocacion(m, "color", "usuario1", priv1)
    r1 = m.votar_revocacion("color", autor="usuario1", firma_hex=f1, ts_exp=t1)
    assert r1["ejecutada"] is False and r1["n_votos"] == 1, r1
    assert m.current("color") is not None
    f2, t2 = _firma_revocacion(m, "color", "usuario2", priv2)
    r2 = m.votar_revocacion("color", autor="usuario2", firma_hex=f2, ts_exp=t2)
    assert r2["ejecutada"] is True and r2["n_votos"] == 2, r2
    assert m.esta_revocada("color")
    assert m.current("color") is None
    m.close()
    print("  ✔ flujo firmado completo (quorum=2) ejecuta tombstone")


def test_voto_cambio_quorum_firmado():
    m = HCMAMemoriaSQLite(_tmp())
    m.set_quorum_revocacion(2, autor="admin")
    m.proponer_cambio_quorum(3, autor="admin")
    priv1, _ = _registrar(m, "usuario1")
    priv2, _ = _registrar(m, "usuario2")
    pend = m.current_raw("quorum_cambio_pendiente")
    hp = pend["value"]["hash_propuesta"]
    ts_exp = int(time.time()) + 60
    f1 = FM.firmar_voto(FM.DOMINIO_VOTO_CAMBIO_QUORUM, hp, "usuario1", ts_exp, priv1)
    r1 = m.votar_cambio_quorum(autor="usuario1", firma_hex=f1, ts_exp=ts_exp)
    assert r1["ejecutado"] is False and r1["n_votos"] == 1, r1
    f2 = FM.firmar_voto(FM.DOMINIO_VOTO_CAMBIO_QUORUM, hp, "usuario2", ts_exp, priv2)
    r2 = m.votar_cambio_quorum(autor="usuario2", firma_hex=f2, ts_exp=ts_exp)
    assert r2["ejecutado"] is True and r2["n"] == 3, r2
    assert m.quorum_revocacion() == 3
    m.close()
    print("  ✔ cambio de quorum con votos firmados")


if __name__ == "__main__":
    test_voto_sin_pubkey_rechazado()
    test_voto_sin_firma_y_firma_forjada_rechazados()
    test_voto_expirado_rechazado()
    test_firma_sobre_otra_propuesta_rechazada()
    test_separacion_de_dominio()
    test_flujo_firmado_completo_ejecuta()
    test_voto_cambio_quorum_firmado()
    print("\nTODOS LOS TESTS DE FIRMAS MULTI-FIRMA (A5) PASARON")
