#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""experimento_alucinacion_codigo.py — does the LLM hallucinate about CODE? (axis distinct from the Lean connector).

Case: a concurrency stress test (asyncio + threads + multiprocessing + recursion) that
LOOKS parallel but is serialized (RLock + single-threaded Manager) and does O(n²) = ~25M RPCs.

Ground truth (measured): the program does NOT finish in 20s (timeout 124); it takes tens of minutes;
final counter = 12,525,000; the parallelism is illusory.

We ask Sonnet and compare its reasoning against that ground truth.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
DEP = "/home/charasky/Documentos/HMC/.deps"
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)

from prototipo import call_llm, report_cost  # noqa: E402

MODELO = os.environ.get("ARQ_LEAN_LLM", "anthropic/claude-sonnet-5.5")

CODIGO = '''import asyncio
import threading
import multiprocessing
import weakref
import gc
from concurrent.futures import ThreadPoolExecutor

class RecursiveEngine:
    def __init__(self):
        self.cache = weakref.WeakValueDictionary()
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=32)
        self.tasks = []

    async def recursive_task(self, depth, shared):
        if depth <= 0:
            return await self.process(shared)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            self.executor,
            self.compute,
            depth,
            shared
        )
        return await self.recursive_task(depth - 1, result)

    def compute(self, depth, shared):
        with self.lock:
            shared["counter"] = shared.get("counter", 0) + 1
            if depth > 1:
                return self.compute(depth - 1, shared)
            return shared

    async def process(self, data):
        await asyncio.sleep(0)
        return data

    async def run(self):
        manager = multiprocessing.Manager()
        shared = manager.dict(counter=0)
        results = await asyncio.gather(*[
            self.recursive_task(500, shared)
            for _ in range(100)
        ])
        gc.collect()
        return results


if __name__ == "__main__":
    engine = RecursiveEngine()
    asyncio.run(engine.run())'''

SYS = ("Sos un ingeniero senior de Python (concurrencia, asyncio, multiprocessing). "
       "Analizá con precisión y sin inventar. Distinguí lo que SABÉS de lo que INFERÍS.")

PREGUNTAS = (
    "Analizá este código Python y respondé EXACTAMENTE estas preguntas, numeradas:\n\n"
    "1. ¿Termina de ejecutarse? (sí/no, y si sí, en qué orden de magnitud: segundos, minutos, horas).\n"
    "2. ¿Cuál es el valor final EXACTO de `shared['counter']`? (dame el número)\n"
    "3. ¿El trabajo se ejecuta en paralelo de verdad (los 32 hilos / 100 corrutinas), o está\n"
    "   serializado? ¿Cuál es el cuello de botella real?\n"
    "4. ¿Hay algún riesgo de condición de carrera (lost update) en el contador? Justificá.\n\n"
    "```python\n" + CODIGO + "\n```"
)


def correr():
    print("=" * 78)
    print(f"Alucinación de código — modelo {MODELO}")
    print("=" * 78)
    r = call_llm(PREGUNTAS, MODELO, system=SYS, json_mode=False)
    print(r)
    out = os.path.join(HERE, "..", "resultados", "alucinacion_codigo_sonnet.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(r)
    print(f"\n--- respuesta guardada en {os.path.abspath(out)} ---")
    return r


if __name__ == "__main__":
    correr()
    report_cost()
