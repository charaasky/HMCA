#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""test_auth_ed25519.py — integración de firmas por usuario del DASHBOARD con la memoria (P0 A5).

Cierra el lazo del "multi-firma real" a nivel de módulos (sin server/socket):
  1. auth.ensure_ed25519 genera/almacena un par Ed25519 por usuario.
  2. auth.sign_voto firma con la privada del usuario autenticado.
  3. La pública se registra en la MEMORIA (registrar_pubkey) y el voto firmado verifica contra
     ella (votar_revocacion), ejecutando el borrado al quorum.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard")
if DASH not in sys.path:
    sys.path.insert(0, DASH)

# auth.py lee HCMA_DASH_AUTH_DB al import: hay que setearlo ANTES de importar.
os.environ["HCMA_DASH_AUTH_DB"] = os.path.join(tempfile.mkdtemp(prefix="hcma_auth_"), "auth.db")

import auth  # noqa: E402
from memoria_sqlite import HCMAMemoriaSQLite  # noqa: E402


def test_ed25519_usuario_dashboard_firma_y_vota():
    auth.init()
    auth.create_user("usuario1", "password123", role="usuario")

    pub = auth.ensure_ed25519("usuario1")
    assert len(pub) == 64

    m = HCMAMemoriaSQLite(":memory:")
    m.write("k", 1, fuente="usuario", verified=True)
    m.proponer_revocacion("k", autor="admin", motivo="m")

    # el dashboard registra la pública del validador y firma el voto
    m.registrar_pubkey("usuario1", pub, autor="admin")
    hp = m._revocacion_pendiente("k")["value"]["hash_propuesta"]
    ts_exp = int(time.time()) + 300
    firma = auth.sign_voto("usuario1", "voto_revocacion", hp, ts_exp)

    r = m.votar_revocacion("k", autor="usuario1", firma_hex=firma, ts_exp=ts_exp)
    assert r["ejecutada"] is True, r
    assert m.esta_revocada("k")
    m.close()
    print("  ✔ clave por usuario del dashboard -> registro -> voto firmado -> borrado")


def test_firma_de_otro_usuario_no_valida():
    auth.init()
    auth.create_user("a", "password123", role="usuario")
    auth.create_user("b", "password123", role="usuario")
    pub_a = auth.ensure_ed25519("a")
    pub_b = auth.ensure_ed25519("b")

    m = HCMAMemoriaSQLite(":memory:")
    m.write("k", 1, fuente="usuario", verified=True)
    m.proponer_revocacion("k", autor="admin", motivo="m")
    # la pública registrada es la de "a"; "b" intenta votar firmando con SU clave
    m.registrar_pubkey("a", pub_a, autor="admin")
    hp = m._revocacion_pendiente("k")["value"]["hash_propuesta"]
    ts_exp = int(time.time()) + 300
    firma_b = auth.sign_voto("b", "voto_revocacion", hp, ts_exp)
    try:
        m.votar_revocacion("k", autor="a", firma_hex=firma_b, ts_exp=ts_exp)
        assert False, "firma del usuario equivocado no debería validar"
    except ValueError as e:
        assert "firma inválida" in str(e), str(e)
    assert pub_b != pub_a
    m.close()
    print("  ✔ firma de otro usuario (pubkey distinta) rechazada")


if __name__ == "__main__":
    test_ed25519_usuario_dashboard_firma_y_vota()
    test_firma_de_otro_usuario_no_valida()
    print("\nTODOS LOS TESTS DE AUTH ED25519 (A5) PASARON")
