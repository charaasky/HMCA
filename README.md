# HCMA — Governed, Versioned Memory for LLM Agents

**HCMA** (Hipocampo / _Hipocampo_) is a **governed, versioned memory system** for LLM agents:
every fact carries **provenance**, a **monotonic version**, and an **integrity hash-chain**, so an
agent can *remember* without *inventing*. The memory is **write-governed** (a single-writer
monitor), **auditable** (provenance + lineage + hash chain), and supports **governable deletion**
(retract specific facts by lineage, without touching unrelated knowledge).

The pipeline is: `user → orchestrator → { memory | alias | calculator | skill | LLM } → user`,
with **memory-first deterministic recall** (the LLM never decides whether a fact exists).

## Architecture (three layers)

| Layer | Component | Role |
|---|---|---|
| **Governance** | `hcma-monitor`, `hcma-proxy`, `hcma-dashboard` | Memory store, write-governance, signed provenance, web UI + OpenAI-compatible LLM gateway |
| **Hardware boundary** | Firecracker microVM | Untrusted workload runs inside a KVM microVM (vsock to the monitor) |
| **Containment** | `px-motor` (host-side eBPF) | Detects escape attempts of the microVM subject and raises observable, attributable signals |

## Quick start

```bash
sudo sh arquitectura/instalar.sh
```

The installer is **idempotent** and leaves everything running. At the end it prints:

- Dashboard (self-signed HTTPS): `https://localhost:8787/` (and the LAN IP).
- Initial admin credentials (force-changed on first login).
- OpenAI-compatible LLM gateway: `https://localhost:8787/v1` (gateway key is copyable from the dashboard → LLM tab).

Health check: `sudo sh arquitectura/montaje/verificar.sh`.

## Why is `sudo` required?

The installer needs root **only once, at install time**, to set up the security boundary. Once
installed, the services run as unprivileged users (privilege separation):

| Step | Why it needs root |
|---|---|
| `useradd`/`groupadd` (fixed UIDs 101, 102, 997, 999; groups 1001, 1002) | System users are the basis of the UID-separation boundary. |
| Write to `/etc` (systemd units, `/etc/hcma/publica.pin`) | `/etc` is root-owned; the pin is the trust anchor. |
| Write to `/var/lib/hcma-*` + `chown`/`chmod` | Service homes with 0600/0700 modes. |
| `systemctl daemon-reload / enable / start` | Managing system services. |
| `nftables` (default-deny for the proxy UID) | Kernel firewall rules. |
| Firecracker (KVM microVM) | Needs `/dev/kvm`. |
| px-motor (eBPF) | Loading eBPF programs needs CAP_BPF / CAP_SYS_ADMIN. |

**Install = root (one-time setup) · Run = unprivileged.** The monitor runs as UID 999, the proxy
as 997, the signer as 102, the agent as 101, and the dashboard as your user. Root is never used by
the running services — that separation is the point of the architecture.

You can run a partial "dev mode" (dashboard + monitor as your user, without systemd, firewall,
KVM or eBPF), but you lose the security boundary. The firewall, KVM and eBPF cannot be done
without root by design.

## Repository layout

```
arquitectura/
  dashboard/      # web UI + LLM gateway + config persistence (encrypted API keys)
  scripts/        # monitor, proxy, memory store, skills, calculator, incidentes, ...
  montaje/        # installer + health check
  firecracker/    # microVM deployment
  px-motor/       # eBPF escape-detector deployment (source lives in a separate repo)
  gvisor/         # legacy sandbox bundles (pre-cutover)
```

## License

This repository is **dual-licensed**:

- **Code** (`*.py`, `*.sh`, dashboard, scripts): **Apache-2.0** — see [`LICENSE`](LICENSE) and the
  `SPDX-License-Identifier: Apache-2.0` headers in every source file.
- **Documentation, papers, and derived data**: **CC BY 4.0** — see [`LICENSE-CC-BY-4.0.txt`](LICENSE-CC-BY-4.0.txt).

## Third-party data & provenance (probes are *named*, not redistributed)

The benchmark/eval probes are **referenced by name** but **not uploaded** in this repository; the
corpus and loaders are derived and attributed, not redistributed raw:

- **TempLAMA / TemporalWiki** (EMNLP 2022): upstream license **undeclared**; underlying data
  **Wikidata (CC0)** and **Wikipedia / T-REx / Google-RE (CC BY-SA 4.0)**. Attribution:
  *"derived from TempLAMA (TemporalWiki, EMNLP 2022); underlying data Wikidata (CC0) / Wikipedia
  (CC BY-SA 4.0)."*
- **InjecAgent** (benchmark D3): third-party, **MIT**.

> Governable deletion is a first-class feature: if a fact/document/link must be withdrawn for
> legal reasons, its full ingestion tree can be removed by lineage without touching unrelated
> knowledge.

## Security binary (`px-motor`)

The `px-motor` detector is built **from source** by `arquitectura/px-motor/deploy.sh` (its source
lives in a separate repository). **No compiled binary is committed here** — see the install step
above for how it is built and deployed.
