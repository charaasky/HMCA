#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test de política de egreso (P0-D): qué memoria puede salir a qué proveedor.

Reglas:
  - proveedor LOCAL (loopback/socket) => todo pasa.
  - proveedor EXTERNO => public siempre, personal por defecto (opt-out), secret nunca.
  - sensibilidad derivada de fuente (usuario/chat => personal) o meta.sens.
  - 'secret' (meta.sens='secret') NUNCA sale a externo; 'personal' se inyecta por defecto
    (HCMA_EGRESS_ALLOW_PERSONAL_EXTERNAL=0 lo desactiva para privacidad estricta).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

import llm_gateway as LG  # noqa: E402


def main():
    ok = True

    def check(nombre, cond, detalle=""):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ""))

    personal = {"fuente": "usuario", "meta": {}}
    chat = {"fuente": "chat:usuario", "meta": {}}
    public = {"fuente": "ingeniero", "meta": {}}
    secret = {"fuente": "ingeniero", "meta": {"sens": "secret"}}

    # clasificación de proveedor por base_url
    check("localhost es local", LG._provider_is_local("http://localhost:11434/v1"))
    check("127.0.0.1 es local", LG._provider_is_local("http://127.0.0.1:8080/v1"))
    check("openrouter es externo", not LG._provider_is_local("https://openrouter.ai/api/v1"))
    check("gemini es externo", not LG._provider_is_local("https://generativelanguage.googleapis.com/v1beta"))

    # sensibilidad
    check("usuario -> personal", LG._fact_sens(personal) == "personal")
    check("chat -> personal", LG._fact_sens(chat) == "personal")
    check("ingeniero -> public", LG._fact_sens(public) == "public")
    check("meta.sens secret", LG._fact_sens(secret) == "secret")

    ext = "https://openrouter.ai/api/v1"
    local = "http://localhost:11434/v1"

    # externo por defecto (sin env): public + personal; secret nunca
    LG.os.environ.pop("HCMA_EGRESS_ALLOW_PERSONAL_EXTERNAL", None)
    out = LG._egress_facts([personal, public, secret], ext, "openrouter")
    keys = {f["fuente"] for f in out}
    check("externo por defecto: public+personal, secret no",
          len(out) == 2 and "usuario" in keys and "ingeniero" in keys,
          f"n={len(out)} keys={keys}")

    # externo con opt-out (env=0): privacidad estricta -> solo public
    LG.os.environ["HCMA_EGRESS_ALLOW_PERSONAL_EXTERNAL"] = "0"
    out = LG._egress_facts([personal, public, secret], ext, "openrouter")
    check("externo con opt-out (env=0): solo public", len(out) == 1 and out[0] is public,
          f"n={len(out)}")

    # local: todo pasa (incluso secret)
    LG.os.environ.pop("HCMA_EGRESS_ALLOW_PERSONAL_EXTERNAL", None)
    out = LG._egress_facts([personal, public, secret], local, "ollama")
    check("local: todo pasa", len(out) == 3, f"n={len(out)}")

    print("\nRESULTADO:", "✔ P0-D (egreso) OK" if ok else "✘ hay fallas")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
