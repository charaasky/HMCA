# Perplejidad en un sistema con poda (árbol de verificación / decodificador restringido)

Cuando el harness **poda** caminos (un árbol de decisiones/verificación o un decodificador
restringido) para eliminar alucinaciones, el espacio de tokens válidos se achica en cada paso.
Según **dónde** se tomen los logits, se mide una distribución distinta.

## 1. Las dos perplejidades

### A. Perplejidad intrínseca del modelo base (sin máscara)
Se toman los logits crudos del modelo **antes** de aplicar poda/verificación.

- **Qué mide:** qué tan bien el modelo base predice la secuencia objetivo por sí solo.
- **Limitación:** no refleja la arquitectura de poda. Si el base duda entre un dato real y una
  alucinación, su PPL será alta aunque el árbol luego descarte la rama falsa.

### B. Perplejidad post-poda (renormalizada)
Si el sistema reduce los tokens/caminos permitidos en el paso `i` de `V` a `V_válido(i) ⊂ V`,
la perplejidad válida requiere **renormalizar** sobre los sobrevivientes:

```
Z_i = Σ_{v ∈ V_válido(i)} P_base(v)
P_podado(t_i) = P_base(t_i) / Z_i
PPL_podado = exp( - (1/N) Σ_i log P_podado(t_i) )
```

- **Qué mide:** la incertidumbre real del sistema completo tras descartar ramas inválidas.
- **Condición crítica (ground truth):** el token real debe sobrevivir a la poda. Si el mecanismo
  elimina por error el camino verdadero, `P_podado(t_i)=0` → `-log(0)=∞` → `PPL=∞`. Esto convierte
  la métrica en una **prueba de fuego**: detecta al instante si la poda es demasiado agresiva.

## 2. Poda a nivel de rama/secuencia (no de token)

Si el harness verifica **nodos completos** (estados o hashes de bloques) en vez de enmascarar
token a token:

- **Branch/node perplexity:** se suman las log-probabilidades de los tokens de cada nodo válido y
  se normaliza solo sobre las ramas que pasaron la verificación.
- **Efecto `PPL = 1`:** si en un punto determinista la poda deja una única rama válida
  (`|V_válido| = 1`), la probabilidad renormalizada de ese camino es `1.0` y la perplejidad en ese
  paso es exactamente `1.0` (incertidumbre cero).

## 3. Métricas complementarias (la PPL sola engaña)

Un sistema que bloquea el 99% del vocabulario tendrá siempre PPL ≈ 1 aunque pierda flexibilidad.
Acompañar con:

| Métrica | Qué mide |
|---|---|
| Tasa de supervivencia del ground truth | % de secuencias reales no podadas por error (evita `PPL=∞`). |
| Factor de ramificación efectivo | = `PPL_podado`: cuántas ramas válidas promedio quedan abiertas por nivel. |
| Delta de perplejidad (ΔPPL) | `PPL_base / PPL_podado`: cuánto reduce la verificación la incertidumbre. |

## 4. Estabilidad numérica

Renormalizar en **log-space** (log-sum-exp), no exponenciando y re-dividiendo: con vocabularios
grandes o podas agresivas, exponenciar primero produce underflow.

```python
logsumexp(xs) = m + log( Σ exp(x_i - m) ),   m = max(xs)
```

## 5. Implementación

`perplejidad_poda.py` (mismo directorio) implementa `PPL_base`, `PPL_podado`, `branching`,
`ΔPPL` y `survival_rate` en una sola pasada. Uso:

```bash
python3 perplejidad_poda.py --demo    # auto-test con datos sintéticos
```
