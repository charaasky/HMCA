# Detecting LLM evasion / stalling / sandbagging

Goal: detect when the LLM "gives the runaround" instead of solving, and burns tokens doing it. The
right method depends on **where the model runs**:

- **External LLM (black-box)**: no weights/hidden-state access -> surface heuristics + sampling.
- **Local LLM inside Firecracker (white-box, open-weights)**: weights are available -> noise-injection
  sandbagging probe + internal-recognition probe (high accuracy).

## The fundamental limit

The *intent* to stall is a hidden internal state. Black-box methods only measure *symptoms*;
white-box methods get closer because they read *inside* the model. No method is 100% for open-ended
tasks; the only near-certain detection is **verifying the output against a spec** for verifiable
tasks (which HCMA already does in `verificacion_trabajo.py`).

## Black-box (external) - `detectar_externo(...)`

Signals, all deterministic (no model access):

- filler/hedge density (apologies, "as an AI", "it depends", "instead you should"...)
- deflection (response ends by asking a question back)
- missing resolutive signal (no number/code block)
- token bloat (`completion_tokens >> baseline`)
- low verbalized confidence ("I am 30% confident...")
- low self-consistency across N samples

Returns `{label: resolutivo|borderline|evasivo, score, razones}`. Gameable, but catches obvious
stalling.

## White-box (local) - `probe_ruido(...)` + `detectar_sandbagging(...)`

Reference: Tice et al. 2024 ("Noise Injection Reveals Hidden Capabilities of Sandbagging Language
Models"). Inject Gaussian noise of increasing magnitude into the weights and measure a capability
score at each level:

- honest model -> monotonic degradation,
- sandbagging model -> anomalous *improvement* under noise (the underperformance mechanism is
  disrupted while the core capability remains).

`probe_ruido(eval_fn, sigmas)` is generic; `eval_fn(sigma)` must run inference with perturbed
weights. For a real local LLM this requires hooking the inference runtime (llama.cpp, or a Python
lib that exposes weights/activations). PIR (Dingeto 2026) is the activation-probe complement: read,
from hidden states, which candidate answer the model recognizes as correct.

## Dispatch

`detectar(..., modo="auto")` reads `llm_config.local_llm_enabled()` and picks local vs external.
The gateway attaches `hcma_evasion` to every response.

## Usage / tests

```bash
python3 test_detector_evasion.py    # 13 PASS
python3 detector_evasion.py         # demo
```

## Honest limits

- Black-box: moderate accuracy, false positives possible, gameable by a sophisticated staller.
- White-box: high accuracy for *sandbagging* specifically, but needs runtime integration (not wired
  by default) and an open-weights local model.
- Neither detects *intent* in general; for verifiable tasks, verification
  (`verificacion_trabajo.py`) is the strongest and only near-certain signal.
