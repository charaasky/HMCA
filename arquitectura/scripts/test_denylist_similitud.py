#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Tests de la denylist por consenso: hash del revocado + rechazo de contenido igual/similar.

Invariantes:
  1. Tras un borrado por consenso, un write futuro con el MISMO valor (aunque sea bajo OTRA key)
     es rechazado, y la negativa cita el hash del hecho revocado + el motivo + la categoría.
  2. Un valor SIMILAR (tilde, puntuación, edición menor) también es rechazado (match 'similar').
  3. Un valor DISTINTO se permite.
  4. `restaurar` quita la denylist y vuelve a permitir el valor.
"""
import os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memoria_sqlite import HCMAMemoriaSQLite
import firmas_multifirma as FM


def _tmp():
    return os.path.join(tempfile.mkdtemp(), "hcma.db")


def _revocar(m, key, autor="admin", votante="usuario1", motivo="peligroso", categoria="peligroso"):
    m.proponer_revocacion(key, autor=autor, motivo=motivo, categoria=categoria)
    priv, pub = FM.generar_par()
    m.registrar_pubkey(votante, pub, autor="admin")
    hp = m._revocacion_pendiente(key)["value"]["hash_propuesta"]
    ts_exp = int(time.time()) + FM.EXPIRACION_VOTO_SEG
    firma = FM.firmar_voto(FM.DOMINIO_VOTO_REVOCACION, hp, votante, ts_exp, priv)
    m.votar_revocacion(key, autor=votante, firma_hex=firma, ts_exp=ts_exp)


def _bloqueado(fn):
    try:
        fn()
        return False
    except ValueError as e:
        return str(e)


def test_valor_igual_bajo_otra_key():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("secreto", "la formula del veneno es X", fuente="usuario", verified=True)
    _revocar(m, "secreto", motivo="peligroso para humanos", categoria="peligroso")
    msg = _bloqueado(lambda: m.write("otra_key", "la formula del veneno es X",
                                     fuente="usuario", verified=True))
    assert msg is not False, "mismo valor bajo otra key debería estar bloqueado"
    assert "hash_revocado=" in msg, msg
    assert "[peligroso]" in msg, msg
    assert "peligroso para humanos" in msg, msg
    assert "match valor" in msg, msg
    m.close()
    print("  ✔ valor exacto bajo otra key bloqueado (cita hash + motivo + categoría)")


def test_valor_similar_bloqueado():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("secreto", "la formula del veneno es X", fuente="usuario", verified=True)
    _revocar(m, "secreto", motivo="etico", categoria="etica")
    # tilde + signo de exclamación: es 'similar', no 'igual'
    msg = _bloqueado(lambda: m.write("k2", "la fórmula del veneno es X !",
                                     fuente="usuario", verified=True))
    assert msg is not False, "valor similar debería estar bloqueado"
    assert "match similar" in msg, msg
    assert "[etica]" in msg, msg
    m.close()
    print("  ✔ valor similar (tilde/puntuación) bloqueado como 'similar'")


def test_valor_distinto_permitido():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("secreto", "la formula del veneno es X", fuente="usuario", verified=True)
    _revocar(m, "secreto")
    v = m.write("inocuo", "el cielo es azul", fuente="usuario", verified=True)
    assert v is not None
    m.close()
    print("  ✔ valor distinto permitido")


def test_restaurar_libera_la_denylist():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("secreto", "la formula del veneno es X", fuente="usuario", verified=True)
    _revocar(m, "secreto")
    assert _bloqueado(lambda: m.write("k2", "la formula del veneno es X",
                                      fuente="usuario", verified=True)) is not False
    m.restaurar("secreto", autor="admin", motivo="falsa alarma")
    # tras restaurar, el mismo valor vuelve a ser aceptado
    v = m.write("k2", "la formula del veneno es X", fuente="usuario", verified=True)
    assert v is not None
    m.close()
    print("  ✔ restaurar libera la denylist y permite re-aprender el valor")


def test_misma_key_sigue_bloqueada():
    m = HCMAMemoriaSQLite(_tmp())
    m.write("secreto", "valor", fuente="usuario", verified=True)
    _revocar(m, "secreto", categoria="moral", motivo="contrario a la moral")
    msg = _bloqueado(lambda: m.write("secreto", "valor", fuente="usuario", verified=True))
    assert msg is not False and "match key" in msg, msg
    assert "[moral]" in msg, msg
    m.close()
    print("  ✔ misma key bloqueada (match key, categoría moral)")


if __name__ == "__main__":
    test_valor_igual_bajo_otra_key()
    test_valor_similar_bloqueado()
    test_valor_distinto_permitido()
    test_restaurar_libera_la_denylist()
    test_misma_key_sigue_bloqueada()
    print("\nTODOS LOS TESTS DE DENYLIST POR CONSENSO PASARON")
