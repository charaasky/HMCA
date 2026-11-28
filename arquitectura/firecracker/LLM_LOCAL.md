# LLM inside the microVM (local boundary)

Run the **model inside** the Firecracker microVM, instead of calling an external API. The LLM stays
**contained** in the hardware boundary: its runtime lives in the guest (ring -1), like the agent,
and `px-motor` keeps watching the subject on the host.

> This does **not** replace work-product verification (`verificacion_trabajo.py`): the model runs
> inside (good), but its *output* still needs governance and deterministic verification.

## Already wired

The gateway (`dashboard/llm_gateway.py`) already distinguishes a **local** vs **external** provider:

- `_provider_is_local(base)` -> `localhost`/`127.0.0.1`/`.local`/`.internal` is local.
- `_scheme_allowed(url)` -> allows `http` **only** for loopback/local.
- `_egress_facts(...)` -> with a local provider, **all** facts (including `secret`) stay local; with
  an external provider they are filtered.

A first-class config block was added to `dashboard/llm_config.py`:

```json
"local_llm": { "enabled": false, "port": 8080, "model": "local" }
```

With `enabled=true`, `_conn()` resolves to `http://127.0.0.1:<port>/v1` (provider `local`), without
an API key, and forces the local `model`. The gateway **circuit** becomes:
`client -> gateway -> 127.0.0.1:<port> -> LLM in the guest`.

## Requirements (honest)

- **CPU-only.** Firecracker does no GPU passthrough; inference runs on CPU. A 7B Q4 model needs
  ~4-5 GB resident RAM; add headroom for KV-cache and the OS.
- The default microVM is **512 MiB / 1 vCPU** (`config.json`) - for an LLM you must raise
  `mem_size_mib` and `vcpu_count` (e.g. 8192 MiB / 4 vCPU for a 7B).
- **Large rootfs** for the weights (several GB) or, better, mount the weights as a **second
  read-only block device** (`drives` in `config.json`) rather than inside the rootfs.

## How to build it

1. Extend `build_rootfs_firecracker.sh` (or build a separate rootfs) to install an OpenAI-compatible
   runtime: `llama.cpp` (`server`) or `ollama`.
2. Weights: add a second `drives` entry in `config.json` (read-only) pointing at an `.ext4` with the
   model, and mount it in `guest_init`.
3. `guest_init` starts the server on a TCP port (e.g. `8080` for llama.cpp, `11434` for ollama).

## Transport (how to expose the port to the host)

### Option A - tap device (standard, any HTTP server)

Add a `network-interfaces` to `config.json` with a `tap` (e.g. `tap0`), bring up a bridge/NAT on the
host, and the guest gets an IP. The host reaches the LLM at `http://<guest-ip>:<port>` (or a
port-forward to loopback).

- **Honest tradeoff:** this reintroduces a network path to the microVM (previously
  `network-interfaces: []`). That surface is **only** the LLM port, still covered by `px-motor` +
  the local gateway. The "no network" claim is relaxed on purpose.

### Option B - vsock (keeps the microVM network-free)

Firecracker already uses vsock for the agent. If the LLM runtime speaks vsock natively (rare), or you
add a **vsock->TCP shim** in the guest (listens on vsock and forwards to the local TCP server), the
host reaches it via a vsock->loopback forwarder. More work, but keeps "no network".

## Configure from the dashboard

Via `llm_config.save()` (which already persists the block):

```python
llm_config.save({"local_llm": {"enabled": True, "port": 8080, "model": "my-model-7b"}})
```

Or equivalently via `provider`/`base_url`/`model` in the panel (the gateway treats it the same if the
host is loopback). With `enabled`, the gateway **requires no API key** and forces the local `model`.

The dashboard LLM tab has a "**Local LLM (inside the microVM)**" block (enable checkbox + port +
model).

## Honest limits

- **CPU-only** -> latency/throughput bounded by the machine; not a GPU replacement.
- The model is still **a binary you can verify** with `verificacion_trabajo.py` (output does not
  self-certify).
- "LLM inside" **reduces** dependence on the external vendor, but does **not** eliminate the output
  governance problem (the model can still hallucinate or drift).

See also: `README.md` (boundary), `../scripts/verificacion_trabajo.md` (work-product verification).
