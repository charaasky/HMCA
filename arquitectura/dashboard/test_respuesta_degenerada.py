#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Test del guard anti-salida-degenerada (_respuesta_degenerada).

Cubre:
  1) timestamps de transcripcion ([0.0:4.0] / [4.0:]) -> degenerada.
  2) poema con timestamps -> degenerada.
  3) eco literal de la pregunta -> degenerada.
  4) respuesta normal -> NO degenerada.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

import llm_gateway as LG  # noqa: E402


def main():
    ok = True

    def check(nombre, cond, detalle=''):
        nonlocal ok
        ok &= cond
        print(f"  [{'OK' if cond else 'FALLA'}] {nombre}" + (f"  ({detalle})" if detalle else ''))

    degen = LG._respuesta_degenerada

    # 1) transcripcion con timestamps
    r = degen('que modelo sos?', '''[4.0:8.0] que modelo sos?
[10.0:14.0] que modelo sos?
[16.0:20.0] que modelo sos?''')
    check('timestamps -> degenerada', r == (True, 'timestamp_segments'), f'r={r}')

    # 2) poema con timestamps
    r = degen('que modelo sos?', '''[0.0:] Soy Nemotron.
[4.0:] Soy un modelo de lenguaje.
[8.0:] Fui entrenado con datos.''')
    check('poema con timestamps -> degenerada', r == (True, 'timestamp_segments'), f'r={r}')

    # 3) eco literal sin timestamps
    r = degen('que modelo sos?', 'que modelo sos? que modelo sos? que modelo sos?')
    check('eco literal -> degenerada', r == (True, 'echo'), f'r={r}')

    # 4) respuesta normal
    r = degen('que modelo sos?', 'Soy Nemotron 3 Ultra, un modelo de lenguaje creado por NVIDIA.')
    check('respuesta normal -> no degenerada', r == (False, ''), f'r={r}')

    # 5) mencion normal del tema (no es eco)
    r = degen('que es hcma?', 'HCMA es un sistema de memoria gobernada para agentes LLM.')
    check('mencion normal -> no degenerada', r == (False, ''), f'r={r}')

    print('\nRESULTADO:', 'guard anti-degenerada OK' if ok else 'hay fallas')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
