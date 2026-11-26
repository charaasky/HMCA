#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Tests de la ACCIÓN GOBERNADA (quorum para acciones que degradan la seguridad).

Cubre: rehabilitar/eliminar incidente y aprobar instalación/cableado de herramienta DEBEN exigir
quorum de validadores (multi-firma Ed25519), reutilizando la maquinaria del borrado multi-firma.
El MONITOR ejecuta el dominio al alcanzar el quorum y lo marca `ejecutada`. El proponente nunca
se auto-aprueba.
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


def _firma_accion(hash_propuesta, usuario, priv):
    ts_exp = int(time.time()) + FM.EXPIRACION_VOTO_SEG
    return FM.firmar_voto(FM.DOMINIO_VOTO_ACCION, hash_propuesta, usuario, ts_exp, priv), ts_exp


def test_memoria_proponer_votar_marcar():
    m = HCMAMemoriaSQLite(_tmp())
    m.set_quorum_revocacion(2, autor="admin")
    r = m.proponer_accion_gobernada("rehabilitar_incidente", "i1", autor="admin",
                                    motivo="desc", payload={})
    assert r["estado"] == "pendiente" and r["quorum"] == 2, r
    hp = r["hash_propuesta"]
    # auto-voto rechazado
    try:
        m.votar_accion_gobernada(hp, autor="admin")
        assert False, "auto-voto no debería permitirse"
    except ValueError as e:
        assert "propia propuesta" in str(e)
    priv1 = _registrar(m, "usuario1")
    priv2 = _registrar(m, "usuario2")
    f1, t1 = _firma_accion(hp, "usuario1", priv1)
    r1 = m.votar_accion_gobernada(hp, autor="usuario1", firma_hex=f1, ts_exp=t1)
    assert r1["ejecutable"] is False and r1["n_votos"] == 1, r1
    f2, t2 = _firma_accion(hp, "usuario2", priv2)
    r2 = m.votar_accion_gobernada(hp, autor="usuario2", firma_hex=f2, ts_exp=t2)
    assert r2["ejecutable"] is True and r2["n_votos"] == 2, r2
    assert m.acciones_gobernadas_pendientes()[0]["estado"] == "aprobada"
    # el monitor marca ejecutada
    ok = m.marcar_accion_gobernada_ejecutada(hp, autor="monitor", resultado={"ok": True})
    assert ok
    assert m.acciones_gobernadas_pendientes() == []
    m.close()
    print("  ✔ memoria: proponer → 2 votos firmados → ejecutable → marcar ejecutada")


def test_fachada_rehabilitar_incidente_quorum():
    from monitor_fachada import MonitorFachada
    import incidentes as INC
    m = HCMAMemoriaSQLite(_tmp())
    f = MonitorFachada(m)
    f.memoria.set_quorum_revocacion(2, autor="admin")
    INC.registrar(m, "esc1", "socket", "transporte", "s1", agent_id="ag-1")
    INC.bloquear(m, "esc1", "s1", motivo="escape")
    INC.cuarentenar(m, "esc1", "monitor", motivo="aislar")
    assert INC.estado(m, "esc1") == "cuarentena"
    # proponer gobernado (NO ejecuta todavía)
    r = f.pedir({"tipo": "proponer_accion_gobernada", "tipo_accion": "rehabilitar_incidente",
                 "objetivo": "esc1", "autor": "admin", "motivo": "parche aplicado"})
    assert r["ok"] is True and r["estado"] == "pendiente", r
    hp = r["hash_propuesta"]
    priv1 = _registrar(m, "usuario1")
    priv2 = _registrar(m, "usuario2")
    f1, t1 = _firma_accion(hp, "usuario1", priv1)
    r1 = f.pedir({"tipo": "votar_accion_gobernada", "hash_propuesta": hp,
                  "autor": "usuario1", "firma_hex": f1, "ts_exp": t1})
    assert r1["ok"] is True and r1.get("ejecutable") is False, r1
    assert INC.estado(m, "esc1") == "cuarentena"  # aún no ejecutado
    f2, t2 = _firma_accion(hp, "usuario2", priv2)
    r2 = f.pedir({"tipo": "votar_accion_gobernada", "hash_propuesta": hp,
                  "autor": "usuario2", "firma_hex": f2, "ts_exp": t2})
    assert r2["ok"] is True and r2.get("ejecutada") is True, r2
    assert INC.estado(m, "esc1") == "rehabilitado"
    assert f.pedir({"tipo": "listar_acciones_gobernadas"})["pendientes"] == []
    m.close()
    print("  ✔ fachada: rehabilitar incidente exige quorum=2 y ejecuta al alcanzarlo")


def test_fachada_aprobar_cableado_quorum():
    from monitor_fachada import MonitorFachada
    import herramientas as HR
    m = HCMAMemoriaSQLite(_tmp())
    f = MonitorFachada(m)
    f.memoria.set_quorum_revocacion(2, autor="admin")
    HR.solicitar(m, "kicad", origen="internet", session_id="ag-1")
    HR.aprobar_instalacion(m, "kicad", "admin")
    HR.marcar_instalada(m, "kicad", "internet")
    assert HR.estado(m, "kicad") == "instalada"
    r = f.pedir({"tipo": "proponer_accion_gobernada",
                 "tipo_accion": "aprobar_cableado_herramienta", "objetivo": "kicad",
                 "autor": "admin", "motivo": "cablear"})
    assert r["ok"] is True, r
    hp = r["hash_propuesta"]
    priv1 = _registrar(m, "usuario1")
    priv2 = _registrar(m, "usuario2")
    for u, priv in (("usuario1", priv1), ("usuario2", priv2)):
        fi, ti = _firma_accion(hp, u, priv)
        rr = f.pedir({"tipo": "votar_accion_gobernada", "hash_propuesta": hp,
                      "autor": u, "firma_hex": fi, "ts_exp": ti})
        assert rr["ok"] is True, rr
    assert HR.estado(m, "kicad") == "cableada"
    m.close()
    print("  ✔ fachada: cablear herramienta exige quorum=2 y ejecuta al alcanzarlo")


if __name__ == "__main__":
    test_memoria_proponer_votar_marcar()
    test_fachada_rehabilitar_incidente_quorum()
    test_fachada_aprobar_cableado_quorum()
    print("\nTODOS LOS TESTS DE ACCIÓN GOBERNADA PASARON")
