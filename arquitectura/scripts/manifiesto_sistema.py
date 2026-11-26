#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""manifiesto_sistema.py — Fase 2 (cierre 3): manifiesto de CONGELAMIENTO del sistema desplegado.

Dictamen de Fable (fable_cierre_fases_fable.md): cada corrida que cuenta para el paper necesita
un MANIFIESTO con:
  1. hash del CÓDIGO desplegado (los módulos del monitor en /var/lib/hcma-monitor/lib/);
  2. digests de las IMÁGENES (rootfs de gVisor de cada bundle);
  3. versión de runsc;
  4. la POLÍTICA (config.json del bundle, umbrales del verificador de forma, etc.).

Esto materializa "sistema evaluado = sistema descrito": un revisor puede reproducir exactamente
qué código y qué binarios produjeron los resultados. Sin esto, los experimentos miden un sistema
que nadie puede auditar ni reconstruir.

El manifiesto es un dict estable (JSON canónico) + su hash. La firma la hace el HUMANO con su
clave (no el agente); acá se calcula el contenido y el hash, y se deja listo para firmar.
"""
import hashlib
import json
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# rutas de los artefactos a congelar
MONITOR_LIB = "/var/lib/hcma-monitor/lib"
GVISOR_DIR = "/home/charasky/Documentos/HMC/arquitectura/gvisor"
RESULTADOS_DIR = os.path.join(SCRIPTS, "..", "resultados")


def _sha256_file(path):
    """sha256 de un archivo; None si no existe/ilegible."""
    if not os.path.exists(path):
        return None
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for bloque in iter(lambda: f.read(1 << 20), b""):
                h.update(bloque)
        return h.hexdigest()
    except OSError:
        return None


def _sha256_archivos_de_dir(dirpath, exts=None):
    """sha256 canónico de TODOS los archivos de un directorio (ordenado por nombre).

    Devuelve un solo hash que combina (rel_path + content_hash) de cada archivo, para congelar
    el directorio completo de forma determinista.
    """
    if not os.path.isdir(dirpath):
        return None
    partes = []
    for raiz, _, archivos in os.walk(dirpath):
        for a in sorted(archivos):
            full = os.path.join(raiz, a)
            rel = os.path.relpath(full, dirpath)
            h = _sha256_file(full)
            if h is not None:
                partes.append(f"{rel}:{h}")
    if not partes:
        return None
    canon = "\n".join(partes).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()


def _version_runsc():
    """Versión de runsc (o None si no está instalado)."""
    import subprocess
    try:
        r = subprocess.run(["/usr/bin/runsc", "--version"], capture_output=True, text=True,
                           timeout=10)
        return r.stdout.strip().splitlines()[0] if r.stdout else None
    except Exception:
        return None


def construir_manifiesto_sistema():
    """Calcula el manifiesto de congelamiento del sistema desplegado. Devuelve dict.

    NOTA: NO incluye timestamp en el hash de identidad (Fable: 'congelable'). El campo 'fecha'
    se agrega solo como metadata de auditoría en el archivo guardado, no en `canonical()`.
    """
    manifiesto = {
        "tipo": "manifiesto_sistema",
        # 1. código desplegado del monitor
        "monitor_lib_sha256": _sha256_archivos_de_dir(MONITOR_LIB),
        # 2. imágenes (rootfs de cada bundle gVisor)
        "imagenes": {},
        # 3. versión runsc
        "runsc_version": _version_runsc(),
        # 4. política (config.json del bundle principal)
        "politica": {},
    }

    # digests de las imágenes: hash del config.json + el SCRIPT que genera el rootfs (fuente
    # versionable) + metadata ligera (nº archivos + tamaño). NO se hashea el rootfs completo
    # (varios GB): es caro y el rootfs se reconstruye del build script determinista.
    for bundle in ("bundle", "bundle-python", "bundle-kicad", "bundle-agente"):
        rootfs = os.path.join(GVISOR_DIR, bundle, "rootfs")
        cfg = os.path.join(GVISOR_DIR, bundle, "config.json")
        entry = {}
        cfg_h = _sha256_file(cfg)
        if cfg_h:
            entry["config_json_sha256"] = cfg_h
        # metadata ligera del rootfs (no el contenido completo)
        if os.path.isdir(rootfs):
            n = 0
            tam = 0
            for raiz, _, archivos in os.walk(rootfs):
                n += len(archivos)
                for a in archivos:
                    try:
                        tam += os.path.getsize(os.path.join(raiz, a))
                    except OSError:
                        pass
            entry["rootfs_n_archivos"] = n
            entry["rootfs_bytes"] = tam
        # hash del build script que regenera el rootfs (fuente versionable)
        build_script = os.path.join(GVISOR_DIR, f"build_rootfs_{bundle}.sh")
        bs_h = _sha256_file(build_script)
        if bs_h:
            entry["build_script_sha256"] = bs_h
        if entry:
            manifiesto["imagenes"][bundle] = entry

    # política: config.json del bundle principal (monta noexec, network=none, etc.)
    cfg_principal = os.path.join(GVISOR_DIR, "bundle", "config.json")
    manifiesto["politica"]["config_bundle_sha256"] = _sha256_file(cfg_principal)

    return manifiesto


def canonical(manifiesto):
    """Serialización canónica estable (para firmar/hashear)."""
    return json.dumps(manifiesto, sort_keys=True, ensure_ascii=False)


def hash_manifiesto(manifiesto):
    """sha256 canónico del manifiesto (identificador de la configuración congelada)."""
    return hashlib.sha256(canonical(manifiesto).encode("utf-8")).hexdigest()


def guardar_manifiesto(manifiesto, salida=None):
    """Guarda el manifiesto (JSON canónico + hash) a un archivo versionable en arquitectura/.

    Devuelve el path guardado. El archivo se versiona (es la descripción del sistema evaluado,
    no un artefacto regenerable). Acompaña a los resultados para que un revisor lo reproduzca.
    """
    salida = salida or os.path.join(SCRIPTS, "..", "manifiesto_sistema.json")
    datos = {
        "manifiesto": manifiesto,
        "hash_manifiesto": hash_manifiesto(manifiesto),
        "fecha": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
    }
    with open(salida, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False, sort_keys=True)
    return salida


if __name__ == "__main__":
    m = construir_manifiesto_sistema()
    print(json.dumps(m, indent=2, ensure_ascii=False, default=str))
    print("\nHASH DEL MANIFIESTO:", hash_manifiesto(m))
    guardado = guardar_manifiesto(m)
    print("MANIFIESTO GUARDADO:", guardado)
