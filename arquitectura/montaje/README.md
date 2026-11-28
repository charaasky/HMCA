# HCMA architecture mount

Self-contained folder to **mount the full architecture** reproducibly, with a single orchestrator
and a health check.

> **Full end-to-end install (core + Firecracker + px-motor):** `sudo sh ../instalar.sh`.
> This `montar.sh` covers only the HCMA core (monitor/proxy/dashboard/signing). The hardware microVM
> is deployed with `../firecracker/deploy.sh` and the eBPF detector with `../px-motor/deploy.sh`.

## What is here

| File | What it does |
|---|---|
| `montar.sh` | End-to-end orchestrator (idempotent): UIDs -> keys+pin -> deploy -> systemd -> firewall -> verification. |
| `verificar.sh` | Post-mount health check (exits 0 if all OK). |
| `README.md` | This runbook. |

The canonical scripts it orchestrates live in `../scripts/` (lib deploy, firewall, signing, etc.) and
the `.service` files in `../scripts/` and `../dashboard/`. **They are not duplicated**: `montar.sh`
invokes them by path (single source of truth).

## How to mount

```bash
cd arquitectura/montaje   # from the repo root
sudo sh montar.sh
```

`montar.sh` re-runs itself with `sudo` if not root. It is **idempotent**: safe to run again (it skips
what already exists).

## What each phase does

1. **UID boundary** - creates `hcma-agent` (101), `hcma-signer` (102), `hcma-monitor` (999),
   `hcma-proxy` (997) and the `hcma-red` group (1001); adds `hcma-proxy` and `hcma-monitor` to the
   group. The UIDs are **explicit** so they match what the firewall (997) and the proxy service
   (999) hardcode.
2. **Keys + pin** - generates the Ed25519 pair (the private key stays in `/var/lib/hcma-signer/`,
   600) and fixes the trust anchor `/etc/hcma/publica.pin` (root-owned).
3. **Deploy** - copies the monitor modules to `/var/lib/hcma-monitor/lib/` and the proxy modules to
   `/var/lib/hcma-proxy/lib/`.
4. **systemd** - installs and enables `hcma-monitor`, `hcma-proxy` and `hcma-dashboard`.
5. **Firewall** - nftables: default-deny **only** for UID 997 (proxy), host untouched.
   Never `policy drop` on the output hook (cuts internet for the whole machine); see
   `../scripts/firewall_hcma_proxy.sh`.
6. **Manifest + verification** - freezes the system (`manifiesto_sistema.py`) and runs
   `verificar.sh`.

## Manual step (not done by `montar.sh`): state-cutover ceremony

The state cutover (signed checkpoint + store copy to the monitor) is a **one-time human ceremony**,
not safely automatable. On a **new** machine it must be done by hand:

1. `python3 ../scripts/checkpoint_cutover.py` - computes the store checkpoint.
2. `python3 ../scripts/firmar_checkpoint.py` - the human signs it (offline).
3. `sudo sh ../scripts/cutover_estado.sh --aplicar` - copies the store to
   `/var/lib/hcma-monitor/store/memoria.db` (new inode, verified digest) and creates the nonce log.
4. Verify the digest and remove the original manually.

`montar.sh` **verifies** that the store exists (phase 6) but does **not** create it destructively. On
this machine the cutover is already done (store present, digest `aa60f3...` aligned with the
checkpoint).

## Quick verification

```bash
sudo sh verificar.sh          # full health
curl -skS https://localhost:8787/api/components   # dashboard (more readable; self-signed TLS)

# PUNCTUAL reconciliation audit (Option A): cross executions (proxy 997)
# against consumed authorizations (monitor 999 nonces). Both append-only, distinct UIDs.
sudo python3 arquitectura/scripts/auditar_reconciliacion.py   # from the repo root
```

## Host prerequisites

- Linux with `systemd`, `nftables`, `python3` (>=3.11) and `useradd`/`groupadd`.
- `runsc` (gVisor) installed (`runsc --version`).
- `.deps` at the repo root (`.deps`, PYTHONPATH for the signing scripts).
- gVisor bundles built in `../gvisor/` (the manifest freezes them by hash).
