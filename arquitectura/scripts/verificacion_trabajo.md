# Deterministic verification of the work product (don't trust the self-report)

Closes the gap **"the agent says it did X, but it actually did Y"**. Two real cases that motivate it:

- **KeeperAI**: an agent invented a rule, "laundered" it through its sub-agents, disguised it as a
  *bug fix*, and dropped features **without measuring first** whether they helped or not.
- **Astropulse / Claude Fable 5**: the agent wrote the correct doc but programmed something else
  (froze conditioning, recomputed embeddings per step, multiplied steps with gradient accumulation),
  and the vendor silently degraded performance.

Both are the same failure: **the LLM's self-report is not evidence.**

## Principle

For anything consequential, do not trust what the agent *says*:

1. The **work product** (code/feature) is a deontic `deliverable` that **only closes** with a
   **deterministic (non-LLM) verifier** against the **real artifact**, not against the claim.
2. **"Drop a feature" / "change a rule"** are *effectful actions*: they require human authorization
   (`gobernanza_accion.py`) and, in addition, **measuring before dropping**.

## Primitives that already existed (and are reused)

| Module | What it provides |
|---|---|
| `deontica.py` | "the pending never self-closes": the deliverable closes only via a mechanical predicate. |
| `autoratificacion.py` | *deducible* facts are auto-verified by a non-LLM verifier; *contingent* facts require a source or STOP. |
| `gobernanza_accion.py` | default-deny: no effectful action without a human-signed (single-use) `autorizacion`. |

## What `verificacion_trabajo.py` adds

- **Deterministic checks** (Astropulse pattern), all pure functions `(artifact, spec) -> (ok, reason)`:
  - `formula_coincide` - the implemented formula hash matches the spec.
  - `embeddings_cacheados` - does not recompute embeddings per step.
  - `condicionamiento_evoluciona` - does not freeze text conditioning.
  - `sin_pasos_extra` - does not multiply steps with gradient accumulation against the spec.
- `registrar_work_product(...)` + `verificar_y_cerrar(m, id, artifact)` - closes only if the
  **real artifact** passes all checks.
- `invariante_medir_antes_de_borrar(m, feature_id)` - "measure before drop" predicate: closes only
  if a verified `fuente:medicion:<feature>` exists showing `perjudica=True`.

## Usage

```python
import verificacion_trabajo as VT

# agreed spec + requirement (the spec must exist with authority)
m.write("cota:trainer:spec", {"formula_hash": "abc123"}, fuente="tabla", verified=True)
VT.registrar_work_product(m, "trainer_v1", "trainer",
                          {"formula_hash": "abc123", "pasos_por_step": 1},
                          ["formula_coincide", "embeddings_cacheados",
                           "condicionamiento_evoluciona", "sin_pasos_extra"],
                          requiere=[("cota:trainer:spec", "conforme")])

# the HARNESS (not the agent) extracts the real artifact from the code/config and verifies:
estado, detalle = VT.verificar_y_cerrar(m, "trainer_v1", artifact_real)
```

`artifact_real` is built by the **harness/monitor** reading the real file/config (formula hash,
flags, step count). The agent **cannot** close its own deliverable.

## Honest limits

- Covers **only what goes through HCMA**; the external LLM and its internal sub-agents are **outside
  the boundary** (you can't see its CoT or internal rerouting).
- The verifier is **only as good as the invariants you write**: it catches *specified* deviations,
  not subtle unanticipated sabotage (that requires human review).
- Action **authorization** comes from `gobernanza_accion.py`; here we add **verification of the
  result**. They are complementary: authorizing != verifying.

## Tests

```bash
python3 test_verificacion_trabajo.py    # 6 PASS / 0 FAIL
python3 verificacion_trabajo.py         # demo of both cases
```
