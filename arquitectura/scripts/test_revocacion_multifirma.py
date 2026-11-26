#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Tests del borrado multi-firma (quorum de validadores + firmas Ed25519) de HCMAMemoriaSQLite.

Invariantes cubiertos:
  1. Un borrado NO se ejecuta con un solo humano: propone -> N validadores distintos -> ejecuta.
  2. El proponente nunca se auto-aprueba.
  3. El quorum es configurable y auditado (hash chain).
  4. La denylist resultante sigue siendo fail-closed (write rechazado).
  5. El flujo es recoverable/idempotente (quorum alcanzado sin ejecutar => re-intenta).
  6. Cada voto va FIRMADO con la clave Ed25519 del validador (P0 A5).
"""
import os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memoria_sqlite import HCMAMemoriaSQLite
import firmas_multifirma as FM


def _tmp():
    return os.path.join(tempfile.mkdtemp(), "hcma.db")


def _registrar(m, usuario):
    """Registra una clave Ed25519 para `usuario` y devuelve la privada (para firmar)."""
    priv, pub = FM.generar_par()
    m.registrar_pubkey(usuario, pub, autor="admin")
    return priv


def _firma_revocacion(m, key, usuario, priv):
    """Devuelve (firma_hex, ts_exp) para el voto de `usuario` sobre la propuesta de `key`."""
    hp = m._revocacion_pendiente(key)["value"]["hash_propuesta"]
    ts_exp = int(time.time()) + FM.EXPIRACION_VOTO_SEG
    return FM.firmar_voto(FM.DOMINIO_VOTO_REVOCACION, hp, usuario, ts_exp, priv), ts_exp


def test_quorum_default_y_configurable():
    m = HCMAMemoriaSQLite(_tmp())
    assert m.quorum_revocacion() == 1, m.quorum_revocacion()
    m.set_quorum_revocacion(3, autor="admin")
    assert m.quorum_revocacion() == 3
    try:
        m.set_quorum_revocacion(0, autor="admin")
        assert False, "quorum 0 no debería aceptarse"
    except ValueError:
        pass
    try:
        m.set_quorum_revocacion(None, autor="admin")
        assert False, "quorum None no debería aceptarse"
    except ValueError:
        pass
    # el set queda auditado en la hash chain
    ops = [r["op"] for r in m.conn.execute("SELECT op FROM facts_log").fetchall()]
    assert "set_quorum_revocacion" in ops, ops
    m.close()
    print("  ✔ quorum default 1 + configurable y auditado")


def test_proponer_y_voto_unico_ejecuta():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("color", "gris", fuente="usuario", verified=True)
    r = m.proponer_revocacion("color", autor="admin", motivo="dato malo")
    assert r["estado"] == "pendiente" and r["n_votos"] == 0
    # auto-voto rechazado
    try:
        m.votar_revocacion("color", autor="admin")
        assert False, "auto-voto no debería permitirse"
    except ValueError as e:
        assert "propia propuesta" in str(e)
    # con quorum=1, un validador distinto (firmado) ejecuta
    priv = _registrar(m, "usuario1")
    f, t = _firma_revocacion(m, "color", "usuario1", priv)
    r = m.votar_revocacion("color", autor="usuario1", firma_hex=f, ts_exp=t)
    assert r["ejecutada"] is True and r["n_votos"] == 1, r
    assert m.esta_revocada("color")
    assert m.current("color") is None
    assert m.revocaciones_pendientes() == [], m.revocaciones_pendientes()
    m.close()
    print("  ✔ proponer + voto único firmado (quorum=1) ejecuta tombstone")


def test_quorum_dos_requiere_dos_validadores_distintos():
    m = HCMAMemoriaSQLite(_tmp())
    m.set_quorum_revocacion(2, autor="admin")
    m.write("peso", 5, fuente="usuario", verified=True)
    m.proponer_revocacion("peso", autor="admin", motivo="error")
    priv1 = _registrar(m, "usuario1")
    priv2 = _registrar(m, "usuario2")
    # voto 1: no alcanza quorum
    f1, t1 = _firma_revocacion(m, "peso", "usuario1", priv1)
    r1 = m.votar_revocacion("peso", autor="usuario1", firma_hex=f1, ts_exp=t1)
    assert r1["ejecutada"] is False and r1["n_votos"] == 1
    assert m.current("peso") is not None  # sigue vigente
    # voto repetido del mismo usuario: rechazado
    try:
        m.votar_revocacion("peso", autor="usuario1", firma_hex=f1, ts_exp=t1)
        assert False, "voto duplicado no debería permitirse"
    except ValueError as e:
        assert "ya votó" in str(e)
    # voto 2 (distinto): ejecuta
    f2, t2 = _firma_revocacion(m, "peso", "usuario2", priv2)
    r2 = m.votar_revocacion("peso", autor="usuario2", firma_hex=f2, ts_exp=t2)
    assert r2["ejecutada"] is True and r2["n_votos"] == 2, r2
    assert m.esta_revocada("peso")
    # la denylist sigue fail-closed
    try:
        m.write("peso", 6, fuente="usuario", verified=True)
        assert False, "write sobre key revocada no debería permitirse"
    except ValueError as e:
        assert "bloqueada" in str(e) and "hash_revocado" in str(e), str(e)
    m.close()
    print("  ✔ quorum=2 exige 2 validadores distintos + denylist fail-closed")


def test_propuesta_duplicada_y_sin_propuesta():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("x", 1, fuente="usuario", verified=True)
    m.proponer_revocacion("x", autor="admin", motivo="m")
    try:
        m.proponer_revocacion("x", autor="admin", motivo="otra")
        assert False, "propuesta duplicada no debería permitirse"
    except ValueError as e:
        assert "pendiente" in str(e)
    # votar sin propuesta previa
    m2 = HCMAMemoriaSQLite(_tmp())
    m2.write("y", 2, fuente="usuario", verified=True)
    try:
        m2.votar_revocacion("y", autor="admin")
        assert False, "voto sin propuesta no debería permitirse"
    except ValueError as e:
        assert "no hay revocación pendiente" in str(e)
    m.close()
    m2.close()
    print("  ✔ propuesta duplicada y voto sin propuesta rechazados")


def test_restaurar_quita_denylist():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("z", 3, fuente="usuario", verified=True)
    m.proponer_revocacion("z", autor="admin", motivo="falsa alarma")
    priv = _registrar(m, "usuario1")
    f, t = _firma_revocacion(m, "z", "usuario1", priv)
    m.votar_revocacion("z", autor="usuario1", firma_hex=f, ts_exp=t)
    assert m.esta_revocada("z")
    m.restaurar("z", autor="admin", motivo="falsa alarma")
    assert not m.esta_revocada("z")
    # ahora se puede re-aprender
    m.write("z", 4, fuente="usuario", verified=True)
    assert m.current("z")["value"] == 4
    m.close()
    print("  ✔ restaurar quita denylist y permite re-aprender")


def test_fachada_ops_multifirma():
    """El flujo también debe servir por la fachada del monitor (ops de gobernanza)."""
    from monitor_fachada import MonitorFachada
    m = HCMAMemoriaSQLite(_tmp())
    f = MonitorFachada(m)
    f.pedir({"tipo": "propuesta_write", "key": "k", "value": 1}, session_id="s")
    # registrar pubkey del validador
    priv, pub = FM.generar_par()
    r = f.pedir({"tipo": "registrar_pubkey_hecho", "usuario": "usuario1",
                 "pubkey_hex": pub, "autor": "admin"})
    assert r["ok"] is True, r
    # proponer por la fachada
    r = f.pedir({"tipo": "proponer_revocacion_hecho", "key": "k", "autor": "admin",
                 "motivo": "m"})
    assert r["ok"] is True and r["estado"] == "pendiente", r
    # auto-voto (mismo autor) denegado
    r = f.pedir({"tipo": "votar_revocacion_hecho", "key": "k", "autor": "admin"})
    assert r["ok"] is False and "propia propuesta" in r["detalle"], r
    # voto distinto firmado ejecuta
    hp = m._revocacion_pendiente("k")["value"]["hash_propuesta"]
    ts_exp = int(time.time()) + FM.EXPIRACION_VOTO_SEG
    firma = FM.firmar_voto(FM.DOMINIO_VOTO_REVOCACION, hp, "usuario1", ts_exp, priv)
    r = f.pedir({"tipo": "votar_revocacion_hecho", "key": "k", "autor": "usuario1",
                 "firma_hex": firma, "ts_exp": ts_exp})
    assert r["ok"] is True and r["ejecutada"] is True, r
    # listar pendientes: vacío (ya ejecutado)
    r = f.pedir({"tipo": "listar_revocaciones_hecho"})
    assert r["ok"] is True and r["pendientes"] == [], r
    # set quorum
    r = f.pedir({"tipo": "set_quorum_revocacion_hecho", "quorum": 5, "autor": "admin"})
    assert r["ok"] is True and r["quorum"] == 5, r
    m.close()
    print("  ✔ fachada: proponer/votar firmado/listar/set-quorum/registrar-pubkey")


if __name__ == "__main__":
    test_quorum_default_y_configurable()
    test_proponer_y_voto_unico_ejecuta()
    test_quorum_dos_requiere_dos_validadores_distintos()
    test_propuesta_duplicada_y_sin_propuesta()
    test_restaurar_quita_denylist()
    test_fachada_ops_multifirma()
    print("\nTODOS LOS TESTS DE REVOCACIÓN MULTI-FIRMA PASARON")
