#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""Métricas de perplejidad para un sistema generativo que PODA caminos.

Mide perplejidad sobre un árbol de decisiones/verificación o un decodificador restringido,
donde en cada paso el espacio de tokens válidos se reduce de `V` a `V_válido(i) ⊂ V`.

Hay que distinguir DOS distribuciones (si no, la métrica no tiene sentido matemático):

- `PPL_base`   : perplejidad intrínseca del modelo base (logits crudos, sin poda).
- `PPL_podado` : perplejidad post-poda, con las probabilidades RENORMALIZADAS sobre los
                 nodos/ramas que sobrevivieron. Es el "factor de ramificación efectivo".

Además: `survival_rate` (fracción de ground-truth que NO fue podada por error) y `delta_ppl`
(cuánta incertidumbre elimina el mecanismo de verificación).

Entrada (una secuencia de N pasos ya recorrida por el harness):
    logprobs_gt[i]    -> log P_base(token_i) del token ground-truth (o seleccionado).
    log_mass_valid[i] -> log Z_i = log Σ_{v ∈ V_válido(i)} P_base(v)  (masa de sobrevivientes).
    sobrevive[i]      -> bool: True si el token ground-truth sobrevivió a la poda.

Todo en log-space (log-sum-exp) por estabilidad numérica. Sin dependencias (stdlib).
"""
import math
import sys


def _logsumexp(xs):
    """log(Σ exp(x_i)) estable. `xs`: iterable de log-probabilidades (≤ 0)."""
    xs = [float(x) for x in xs if x != -math.inf]
    if not xs:
        return -math.inf
    m = max(xs)
    return m + math.log(sum(math.exp(x - m) for x in xs))


def _exp_neg(m):
    """exp(-m) protegido contra overflow. m == -inf -> +inf; m muy negativo -> +inf."""
    if m == -math.inf:
        return math.inf
    if -m > 700.0:  # exp(700) ~ 1e304, el límite útil de float64
        return math.inf
    return math.exp(-m)


def _mean_log(xs):
    xs = list(xs)
    if not xs:
        return -math.inf
    return sum(xs) / len(xs)


def perplejidad_base(logprobs_gt):
    """PPL del modelo base: exp(-mean(logprobs_gt))."""
    return _exp_neg(_mean_log(logprobs_gt))


def perplejidad_podada(logprobs_gt, log_mass_valid, sobrevive=None):
    """PPL post-poda (renormalizada sobre la masa de ramas válidas).

    log_podado[i] = logprobs_gt[i] - log_mass_valid[i]   (log de la prob. renormalizada).
    Si `sobrevive[i]` es False, el GT fue podado -> log_podado[i] = -inf -> PPL = inf.
    """
    n = len(logprobs_gt)
    if n == 0:
        return math.inf
    if sobrevive is None:
        sobrevive = [True] * n
    renorm = []
    for i in range(n):
        if not sobrevive[i]:
            renorm.append(-math.inf)
        else:
            # clamp: por ruido de float, el cociente no debe exceder 1 (log ≤ 0)
            renorm.append(min(0.0, logprobs_gt[i] - log_mass_valid[i]))
    return _exp_neg(_mean_log(renorm))


def metricas(logprobs_gt, log_mass_valid, sobrevive=None):
    """Todas las métricas en una sola pasada. Devuelve dict."""
    n = len(logprobs_gt)
    if sobrevive is None:
        sobrevive = [True] * n
    ppl_base = perplejidad_base(logprobs_gt)
    ppl_podado = perplejidad_podada(logprobs_gt, log_mass_valid, sobrevive)
    sobrevivientes = sum(1 for s in sobrevive if s)
    return {
        "n_tokens": n,
        "ppl_base": ppl_base,
        "ppl_podado": ppl_podado,
        "branching_efectivo": ppl_podado,  # identidad: = PPL post-poda
        "delta_ppl_abs": ppl_base - ppl_podado,
        "delta_ppl_ratio": (ppl_base / ppl_podado) if ppl_podado > 0.0 else math.inf,
        "survival_rate": (sobrevivientes / n) if n else 0.0,
    }


def extraer_secuencia_logprobs(content):
    """Convierte la forma de OpenRouter `logprobs.content` en (tokens, logprobs_gt).

    Útil para `PPL_base` (sin poda). Para `PPL_podado`, el harness debe aportar
    `log_mass_valid` por paso (la masa de ramas que sobrevivieron a la poda).
    """
    tokens, logprobs = [], []
    for e in content or []:
        tokens.append(e.get("token", ""))
        logprobs.append(float(e.get("logprob", -math.inf)))
    return tokens, logprobs


def demo():
    gt = [-1.0, -2.0, -0.5]

    print("Escenario 1 — poda correcta (GT sobrevive; Z = 0.8, 0.5, 0.9):")
    for k, v in metricas(gt, [-0.2231, -0.6931, -0.1054]).items():
        print(f"  {k:20s} = {v:.4f}")

    print("\nEscenario 2 — sobre-poda (GT eliminado en el paso 1):")
    for k, v in metricas(gt, [-0.2231, -0.6931, -0.1054], [True, False, True]).items():
        print(f"  {k:20s} = {v:.4f}")

    print("\nEscenario 3 — una sola rama válida (determinista → PPL_podado = 1):")
    for k, v in metricas(gt, gt).items():
        print(f"  {k:20s} = {v:.4f}")


def main(argv):
    demo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
