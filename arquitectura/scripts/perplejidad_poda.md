# Perplexity on a pruned system (verification tree / constrained decoder)

When the harness **prunes** paths (a decision/verification tree or a constrained decoder) to remove
hallucinations, the space of valid tokens shrinks at each step. Depending on **where** the logits
are taken, you are measuring a different distribution.

## 1. The two perplexities

### A. Intrinsic base-model perplexity (no mask)
Take the raw logits **before** applying pruning/verification.

- **What it measures**: how well the base model predicts the target sequence on its own.
- **Limitation**: it does not reflect the pruning architecture. If the base model hesitates between
  a real fact and a hallucination, its PPL will be high even if the tree later discards the false
  branch.

### B. Post-pruning perplexity (renormalized)
If the system reduces the allowed tokens/paths at step `i` from `V` to `V_valid(i)`, the valid
perplexity requires **renormalizing** over the survivors:

```
Z_i = sum_{v in V_valid(i)} P_base(v)
P_pruned(t_i) = P_base(t_i) / Z_i
PPL_pruned = exp( - (1/N) * sum_i log P_pruned(t_i) )
```

- **What it measures**: the real uncertainty of the full system after discarding invalid branches.
- **Critical condition (ground truth)**: the real token must survive pruning. If the mechanism
  wrongly removes the true path, `P_pruned(t_i)=0` -> `-log(0)=inf` -> `PPL=inf`. This turns the
  metric into a **fire test**: it immediately detects over-aggressive pruning.

## 2. Pruning at the branch/sequence level (not token)

If the harness verifies **whole nodes** (states or block hashes) instead of masking token by token:

- **Branch/node perplexity:** sum the log-probabilities of the tokens of each valid node and
  normalize only over the branches that passed verification.
- **The `PPL = 1` effect:** if at a deterministic point pruning leaves a single valid branch
  (`|V_valid| = 1`), the renormalized probability of that path is `1.0` and the perplexity at that
  step is exactly `1.0` (zero uncertainty).

## 3. Complementary metrics (PPL alone is misleading)

A system that blocks 99% of the vocabulary will always have PPL ~ 1 even if it loses flexibility.
Pair it with:

| Metric | What it measures |
|---|---|
| Ground-truth survival rate | % of real sequences not wrongly pruned (avoids `PPL=inf`). |
| Effective branching factor | = `PPL_pruned`: how many valid branches remain open per level on average. |
| Perplexity delta (dPPL) | `PPL_base / PPL_pruned`: how much uncertainty the verification removes. |

## 4. Numerical stability

Renormalize in **log-space** (log-sum-exp), not by exponentiating and re-dividing: with large
vocabularies or aggressive pruning, exponentiating first underflows.

```python
logsumexp(xs) = m + log( sum(exp(x_i - m)) ),   m = max(xs)
```

## 5. Implementation

`perplejidad_poda.py` (same directory) implements `PPL_base`, `PPL_pruned`, `branching`, `dPPL` and
`survival_rate` in a single pass. Usage:

```bash
python3 perplejidad_poda.py --demo    # self-test with synthetic data
```
