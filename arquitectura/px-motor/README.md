# px-motor - eBPF escape detector for the microVM

`px-motor` is the host-side detector that watches the **subject** (the Firecracker VMM) and turns
escape attempts into **observable signals**. It is the third layer of the architecture
(containment), complementary to the monitor/proxy (governance) and the microVM (hardware boundary).

It lives in another repo (`github.com/charaasky/motor-go`); here is the **deployment** that
integrates it into HCMA.

## What it watches and how

| Mechanism | Detail |
|---|---|
| **Signed policy** | Ed25519 with domain separation (`px-motor/policy/v1`). Pins the subject by **cgroup** (inherited, non-spoofable) + UID + exe (+`allow` for the launcher). **Fail-closed**: invalid/expired signature / foreign fingerprint / no selector -> won't start. |
| **Escape invariants** | 2s watcher over the cgroup: `ESCAPE_UID` (UID different from the signed one), `ESCAPE_EXEC` (binary outside `{exe} U {allow}`), `ESCAPE_CGROUP_MIGRATION` (subject leaving the live cgroup). Always emitted (bypass of the rules allowlist). |
| **Anti-rollback** | persists the highest accepted `serial` (root-only, atomic write) and rejects policies with a lower serial. |

The default subject is the Firecracker microVM: `system.slice/hcma-firecracker.service`, UID 995
(`hcma-firecracker`), binary `firecracker`, with the `python3` launcher (`lanzar.py`) allowlisted.

## Deployment

```bash
sudo sh arquitectura/px-motor/deploy.sh   # from the repo root
```

Or, all together, from the orchestrator:

```bash
sudo sh arquitectura/instalar.sh
```

`deploy.sh` is **idempotent**: compiles `px-motor` + `policygen`, locates (or generates) the policy
key, signs a policy with a monotonic `serial` and `not_after`, installs the binary in
`/usr/local/bin/px-motor`, the policy in `/var/lib/px-motor/` and the `hcma-px-motor.service` unit.

## Configuration (environment variables)

| Variable | Default | What it is |
|---|---|---|
| `PXMOTOR_SRC` | `/var/lib/px-motor/src` | motor source repo (cloned from github.com/charaasky/motor-go) |
| `PXMOTOR_CGROUP` | `system.slice/hcma-firecracker.service` | subject cgroup |
| `PXMOTOR_UID` | `995` | subject UID |
| `PXMOTOR_EXE` | `firecracker` | subject binary |
| `PXMOTOR_ALLOW` | `python3` | allowed auxiliary binaries (comma-separated) |
| `PXMOTOR_MODE` | `alert` | `alert` (log only) or `kill` (SIGKILL the offender) |
| `PXMOTOR_OUTPUT` | `/var/lib/px-motor/alerts.jsonl` | alerts JSONL |
| `PXMOTOR_VALID_DAYS` | `365` | `not_after` validity days |
| `PXMOTOR_PIN_FINGERPRINT` | `1` | bind the policy to THIS machine |

## Policy key (important)

The policy private key is **distinct** from `vendor.key` (license != policy). The **public** key is
embedded as the trust anchor in the binary (`policyPubKey`). The private key:

- on this machine: `/var/lib/px-motor/policy.key` (600 root, copied from `~/.keys/px-motor/policy.key`).
- in production: must live **offline** (never on the host running px-motor).

If `deploy.sh` finds no key, it generates a new pair **and rotates the source's `policyPubKey` +
recompiles** (so the anchor stays consistent). That is a trust-rotation event: do it on purpose.

## Operation

```bash
systemctl status hcma-px-motor.service
journalctl -u hcma-px-motor -n 50 -f          # startup + fail-closed + invariants
tail -f /var/lib/px-motor/alerts.jsonl        # ESCAPE_* alerts
```

## Honesty

px-motor makes escapes **observable and attributable** (and terminable in `kill`). It does **not**
prove the microVM is inescapable: no software boundary is. The honest statement is "hardware
boundary + host-side escape detection", not "100%".
