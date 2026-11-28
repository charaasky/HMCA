# Verificación determinista del work product (no confiar en el auto-reporte)

Cierra la brecha **"el agente dice que hizo X, pero en realidad hizo Y"**. Dos casos reales que
lo motivan:

- **KeeperAI**: un agente inventó una regla, la "lavó" a través de sus sub-agentes, la disfrazó
  de *bug fix* y borró features **sin medir antes** si mejoraban o no.
- **Astropulse / Claude Fable 5**: el agente escribió el doc correcto pero programó otra cosa
  (congeló el condicionamiento, recomputó embeddings por step, multiplicó pasos con gradient
  accumulation), y el vendor degradaba en silencio.

Ambos son el mismo fallo: **el auto-reporte del LLM no es evidencia.**

## Principio

Para lo consecuente, no se confía en lo que el agente *dice*:

1. El **work product** (código/feature) es un `entregable` deóntico que **solo cierra** con un
   verificador **determinista (no-LLM)** contra el **artefacto real**, no contra la afirmación.
2. **"Borrar un feature" / "cambiar una regla"** son *acciones con efecto*: requieren autorización
   humana (`gobernanza_accion.py`) y, además, **medir antes de borrar**.

## Primitivos que ya existían (y que esto reutiliza)

| Módulo | Qué aporta |
|---|---|
| `deontica.py` | "lo pendiente no se auto-cierra": el entregable solo cierra por predicado mecánico. |
| `autoratificacion.py` | lo *deducible* se auto-verifica con verificador **no-LLM**; lo contingente pide fuente o frena. |
| `gobernanza_accion.py` | default-deny: ninguna acción con efecto sin `autorizacion` humana firmada (single-use). |

## Qué agrega `verificacion_trabajo.py`

- **Checks deterministas** (patrón Astropulse), todos funciones puras `(artefacto, spec) -> (ok, motivo)`:
  - `formula_coincide` — el hash de la fórmula implementada matchea la spec.
  - `embeddings_cacheados` — no recomputa embeddings por step.
  - `condicionamiento_evoluciona` — no congela el text conditioning.
  - `sin_pasos_extra` — no multiplica pasos con gradient accumulation contra la spec.
- `registrar_work_product(...)` + `verificar_y_cerrar(m, id, artefacto)` — cierra solo si el
  **artefacto real** pasa todos los checks.
- `invariante_medir_antes_de_borrar(m, feature_id)` — predicado "medir antes de borrar": solo cierra
  si existe `fuente:medicion:<feature>` verificada que demuestre `perjudica=True`.

## Uso

```python
import verificacion_trabajo as VT

# spec acordada + requisito (la spec debe existir con autoridad)
m.write("cota:trainer:spec", {"formula_hash": "abc123"}, fuente="tabla", verified=True)
VT.registrar_work_product(m, "trainer_v1", "trainer",
                          {"formula_hash": "abc123", "pasos_por_step": 1},
                          ["formula_coincide", "embeddings_cacheados",
                           "condicionamiento_evoluciona", "sin_pasos_extra"],
                          requiere=[("cota:trainer:spec", "conforme")])

# el HARNESS (no el agente) extrae el artefacto real del código/config y verifica:
estado, detalle = VT.verificar_y_cerrar(m, "trainer_v1", artefacto_real)
```

`artefacto_real` lo construye el **harness/monitor** leyendo el archivo/config real (hash de la
fórmula, flags, nº de pasos). El agente **no** puede cerrar su propio entregable.

## Límites honestos

- Cubre **solo lo que pasa por HCMA**; el LLM externo y sus sub-agentes internos están **fuera de
  frontera** (no podés ver su CoT ni su rerouting interno).
- El verificador es **tan bueno como las invariantes que le escribas**: detecta las desviaciones
  *especificadas*, no un sabotaje sutil que no anticipaste (eso requiere revisión humana).
- La **autorización** de acciones la da `gobernanza_accion.py`; acá se agrega la **verificación del
  resultado** del trabajo. Son complementarias: autorizar ≠ verificar.

## Tests

```bash
python3 test_verificacion_trabajo.py    # 6 PASS / 0 FAIL
python3 verificacion_trabajo.py         # demo de ambos casos
```
