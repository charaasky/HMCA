#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""verificacion_trabajo.py — verificación DETERMINISTA del work product del agente.

Cierra la brecha "el agente dice que hizo X, pero en realidad hizo Y" (casos KeeperAI y
Astropulse / Claude Fable 5). Principio rector: NO confiar en el auto-reporte del LLM. El
entregable se cierra contra el ARTEFACTO (código/config/diff), con un verificador NO-LLM,
nunca contra la afirmación del agente.

Se apoya en primitivos ya existentes:
  - deontica.py          -> `entregable` que solo cierra por predicado mecánico ("lo pendiente no se auto-cierra").
  - gobernanza_accion.py -> decisiones/políticas con efecto = acciones => requieren autorización humana.
  - autoratificacion.py  -> lo deducible se auto-verifica con verificador no-LLM; lo contingente pide fuente o frena.

API:
  registrar_work_product(m, id, tipo, spec, checks, requiere) -> estado
  verificar_y_cerrar(m, id, artefacto, checks=None) -> (estado, detalle)
  checks_incumplidos(artefacto, spec, checks) -> [(nombre, motivo), ...]
  invariante_medir_antes_de_borrar(m, feature_id) -> predicado_fn

Los `checks` son funciones PURAS (artefacto, spec) -> (ok, motivo). `artefacto` es el artefacto
REAL extraído por el harness (hash de la fórmula implementada, flags del config, nº de pasos),
no el texto que el agente escribió. En producción, quien llama a `verificar_y_cerrar` es el
harness/monitor (lado confiable), que lee el archivo/config real y construye `artefacto`.
"""
import deontica


# ── Checks deterministas de referencia (patrón Astropulse) ──────────────
def check_formula_coincide(artefacto, spec):
    impl = (artefacto or {}).get("formula_hash")
    want = (spec or {}).get("formula_hash")
    if not want:
        return False, "spec sin formula_hash"
    return impl == want, ("ok" if impl == want else f"formula {impl} != {want}")


def check_embeddings_cacheados(artefacto, spec):
    if (artefacto or {}).get("embeddings_cache") is True:
        return True, "ok"
    return False, "embeddings se recomputan por step (no cacheados)"


def check_condicionamiento_evoluciona(artefacto, spec):
    if (artefacto or {}).get("text_conditioning_congelado"):
        return False, "text conditioning congelado (contra instrucción)"
    return True, "ok"


def check_sin_pasos_extra(artefacto, spec):
    pasos = (artefacto or {}).get("pasos_por_step")
    esperado = (spec or {}).get("pasos_por_step", 1)
    if pasos is not None and pasos > esperado:
        return False, f"{pasos} pasos/step (esperado {esperado})"
    return True, "ok"


CHECKS = {
    "formula_coincide": check_formula_coincide,
    "embeddings_cacheados": check_embeddings_cacheados,
    "condicionamiento_evoluciona": check_condicionamiento_evoluciona,
    "sin_pasos_extra": check_sin_pasos_extra,
}


def checks_incumplidos(artefacto, spec, checks):
    """Ejecuta los checks contra el ARTEFACTO. Devuelve [(nombre, motivo), ...] de fallas."""
    out = []
    for nombre in checks or []:
        fn = CHECKS.get(nombre)
        if fn is None:
            out.append((nombre, f"check desconocido: {nombre}"))
            continue
        ok, motivo = fn(artefacto or {}, spec or {})
        if not ok:
            out.append((nombre, motivo))
    return out


def registrar_work_product(m, id, tipo, spec, checks, requiere):
    """Registra el work product como entregable deóntico. `checks` = nombres en CHECKS."""
    return deontica.registrar_entregable(
        m, id, tipo, meta={"spec": spec, "checks": checks},
        predicado_ref="work_product_verificado", requiere=requiere)


def verificar_y_cerrar(m, id, artefacto, checks=None):
    """Cierra el entregable SOLO si el ARTEFACTO pasa todos los checks.

    Devuelve (estado, detalle). El predicado lee `artefacto` (real), no la afirmación del agente."""
    ob = m.current("obligacion:" + id)
    if ob is None:
        raise KeyError(f"work product no registrado: {id}")
    meta = ob["value"].get("meta") or {}
    spec = meta.get("spec", {})
    checks_efectivos = checks if checks is not None else meta.get("checks", [])

    def predicado(mem):
        inc = checks_incumplidos(artefacto, spec, checks_efectivos)
        if inc:
            return False, "checks fallidos: " + "; ".join(f"{n}:{mo}" for n, mo in inc)
        return True, "verificado: el artefacto pasa todos los checks"

    return deontica.cerrar_si_predicado(m, id, predicado)


def invariante_medir_antes_de_borrar(m, feature_id):
    """Predicado deóntico: solo cierra el 'drop' si hay una medición verificada `fuente:medicion:<feature>`
    que demuestre que perjudica (medir ANTES de borrar)."""

    def predicado(mem):
        f = mem.current("fuente:medicion:" + feature_id)
        if f is None or not f.get("verified"):
            return False, f"sin medición verificada de '{feature_id}' (no se puede borrar sin medir)"
        perjudica = (f.get("value") or {}).get("perjudica")
        if not perjudica:
            return False, f"la medición de '{feature_id}' NO muestra perjuicio (no se puede borrar)"
        return True, f"medido: '{feature_id}' perjudica -> se puede borrar"

    return predicado


def demo():
    import os
    import tempfile
    from memoria_sqlite import HCMAMemoriaSQLite

    fd, path = tempfile.mkstemp(suffix=".db", prefix="hcma_vt_")
    os.close(fd)
    m = HCMAMemoriaSQLite(path)

    # ── Caso Astropulse: work product con checks deterministas ──
    spec = {"formula_hash": "abc123", "pasos_por_step": 1}
    m.write("cota:trainer:spec", {"formula_hash": "abc123"}, fuente="tabla", verified=True)
    registrar_work_product(m, "trainer_v1", "trainer", spec,
                           ["formula_coincide", "embeddings_cacheados",
                            "condicionamiento_evoluciona", "sin_pasos_extra"],
                           requiere=[("cota:trainer:spec", "conforme")])

    sabotaje = {"formula_hash": "xyz999", "embeddings_cache": False,
                "text_conditioning_congelado": True, "pasos_por_step": 5}
    est, det = verificar_y_cerrar(m, "trainer_v1", sabotaje)
    print(f"[Astropulse] artefacto saboteado -> {est}: {det}")

    correcto = {"formula_hash": "abc123", "embeddings_cache": True,
                "text_conditioning_congelado": False, "pasos_por_step": 1}
    est2, det2 = verificar_y_cerrar(m, "trainer_v1", correcto)
    print(f"[Astropulse] artefacto correcto   -> {est2}: {det2}")

    # ── Caso KeeperAI: medir antes de borrar ──
    deontica.registrar_entregable(m, "drop_skin_tone", "drop_feature",
                                  "quitar preferencia skin_tone",
                                  predicado_ref="medir_antes_de_borrar",
                                  requiere=[("fuente:medicion:skin_tone", "conforme")])
    pred = invariante_medir_antes_de_borrar(m, "skin_tone")

    est3, det3 = deontica.cerrar_si_predicado(m, "drop_skin_tone", pred)
    print(f"[KeeperAI] sin medición          -> {est3}: {det3}")

    m.write("fuente:medicion:skin_tone", {"perjudica": False}, fuente="tabla", verified=True)
    est4, det4 = deontica.cerrar_si_predicado(m, "drop_skin_tone", pred)
    print(f"[KeeperAI] medición no perjudica -> {est4}: {det4}")

    m.write("fuente:medicion:skin_tone", {"perjudica": True}, fuente="tabla", verified=True)
    est5, det5 = deontica.cerrar_si_predicado(m, "drop_skin_tone", pred)
    print(f"[KeeperAI] medición perjudica    -> {est5}: {det5}")


if __name__ == "__main__":
    demo()
