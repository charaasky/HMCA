#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""ratificacion_topo.py — human ratification sheet for the block topology.

Next rung (P1 from Fable, fable_fabricable): go from `derivado_bajo_supuesto` (grade 1,
ASUMIDA topology) to `conectado` (grade 2, DECLARADA topology).

The critical piece is that the human does NOT sign blindly: they sign a deterministic SHEET that lists the
complete graph (blocks, nets, bindings) and the explicit assumptions. The signature is HMAC-SHA256
(external key, reuses provenance) over the canonical hash of the graph. Any subsequent edit
of the graph changes the hash and leaves the signature invalid.

Flow:
  1. `hoja_de_ratificacion(grafo, supuestos)` -> deterministic text that the human reads.
  2. the human signs: `firmar_topologia(grafo, supuestos)` -> hex signature (uses external key).
  3. `verificar_ratificacion(grafo, supuestos, firma)` -> bool (edit-proof).
  4. `topologia_firmada(grafo, supuestos, firma)` -> DECLARADA state if it verifies, else ASUMIDA.

Does NOT touch the core. Reuses `procedencia._clave_humana()` (the same external key) to avoid duplication.
"""
import os
import sys

DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import topologia_bloques as TB  # noqa: E402
import topologia as T  # noqa: E402
import procedencia as P  # noqa: E402


def contenido_firmado(grafo, supuestos):
    """The exact object that is signed: graph + assumptions (stable canonical hash)."""
    return {"grafo": grafo, "supuestos": list(supuestos or [])}


def firmar_topologia(grafo, supuestos=None):
    """Signs the topology with the external human key. Returns the signature (hex 64)."""
    import hmac as _hmac
    import hashlib
    mensaje = P.normalizar(contenido_firmado(grafo, supuestos)).encode("utf-8")
    return _hmac.new(P._clave_humana(), mensaje, hashlib.sha256).hexdigest()


def verificar_ratificacion(grafo, supuestos, firma):
    """Verifies the signature over (graph, assumptions). False on any edit."""
    import hmac as _hmac
    import hashlib
    if not isinstance(firma, str) or len(firma) != 64:
        return False
    mensaje = P.normalizar(contenido_firmado(grafo, supuestos)).encode("utf-8")
    try:
        dado = bytes.fromhex(firma)
        esperado = _hmac.new(P._clave_humana(), mensaje, hashlib.sha256).digest()
        return _hmac.compare_digest(esperado, dado)
    except (ValueError, P.ProcedenciaInvalida):
        return False


def topologia_ratificada(grafo, supuestos, firma):
    """Resulting state: DECLARADA if the graph validates AND the signature is correct; otherwise ASUMIDA
    (or AUSENTE if the graph does not even validate structurally)."""
    ok_estructural, defectos = TB.validar_grafo(grafo)
    if not ok_estructural:
        return T.AUSENTE, defectos
    if verificar_ratificacion(grafo, supuestos, firma):
        return T.DECLARADA, []
    return T.ASUMIDA, ["firma humana ausente o inválida"]


def grado_ratificado(grafo, supuestos, firma):
    """Deliverable grade: 'conectado' only with a valid signature; otherwise it degrades."""
    estado, falt = topologia_ratificada(grafo, supuestos, firma)
    grado, faltantes = T.grado_entregable([estado])
    return grado, falt + faltantes


def hoja_de_ratificacion(grafo, supuestos):
    """Deterministic text (for the human to read BEFORE signing). Does not change state."""
    lineas = ["HOJA DE RATIFICACIÓN DE TOPOLOGÍA", "=" * 40]
    lineas.append(f"Hash del grafo: {TB.hash_canonico(grafo)[:16]}…")
    lineas.append("")
    lineas.append("BLOQUES:")
    for b in grafo.get("bloques", []):
        puertos = ", ".join(f"{p['id']}({p['dominio']})" for p in b.get("puertos", []))
        lineas.append(f"  - {b['id']} [{b.get('tipo','?')}]: {puertos}")
    lineas.append("")
    lineas.append("NETS:")
    for n in grafo.get("nets", []):
        conns = ", ".join(f"{c['bloque']}.{c['puerto']}" for c in n.get("conexiones", []))
        lineas.append(f"  - {n['id']}: {conns}")
    lineas.append("")
    lineas.append("BINDINGS (fórmula → bloque/puerto):")
    for bi in grafo.get("bindings", []):
        lineas.append(f"  - {bi['variable']} → {bi['bloque']}.{bi.get('puerto','—')}")
    lineas.append("")
    lineas.append("SUPUESTOS QUE SE FIRMAN:")
    for s in (supuestos or []):
        lineas.append(f"  * {s}")
    lineas.append("")
    lineas.append("El humano firma el hash de lo anterior. Editar el grafo o los supuestos invalida la firma.")
    return "\n".join(lineas)


if __name__ == "__main__":
    # demo with test key
    os.environ["HCMA_HUMANO_HMAC_KEY"] = "clave-demo"
    grafo = {
        "bloques": [
            {"id": "bateria", "tipo": "fuente", "puertos": [{"id": "V+", "dominio": "potencia"}, {"id": "V-", "dominio": "tierra"}]},
            {"id": "adc", "tipo": "adc", "puertos": [{"id": "V+", "dominio": "potencia"}, {"id": "V-", "dominio": "tierra"}]},
        ],
        "nets": [
            {"id": "vbus", "conexiones": [{"bloque": "bateria", "puerto": "V+"}, {"bloque": "adc", "puerto": "V+"}]},
            {"id": "gnd", "conexiones": [{"bloque": "bateria", "puerto": "V-"}, {"bloque": "adc", "puerto": "V-"}]},
        ],
        "bindings": [],
    }
    supuestos = ["la batería alimenta solo el ADC", "sin carga en la red de señal"]
    firma = firmar_topologia(grafo, supuestos)
    print("firma:", firma[:16], "…")
    print("verifica:", verificar_ratificacion(grafo, supuestos, firma))
    # editing the graph invalidates
    grafo_editado = {k: (v.copy() if isinstance(v, list) else v) for k, v in grafo.items()}
    grafo_editado["bloques"].append({"id": "extra", "tipo": "x", "puertos": []})
    print("tras editar, verifica:", verificar_ratificacion(grafo_editado, supuestos, firma))
    print("grado firmado:", grado_ratificado(grafo, supuestos, firma))
