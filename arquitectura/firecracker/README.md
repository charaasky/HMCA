# gVisor -> Firecracker migration (hardware boundary)

`firecracker` (KVM) replaces gVisor's user-space Sentry with a minimal VMM that runs the guest in
real guest mode (ring -1). Escaping requires a VMM/KVM bug, not a user-space "kernel" bug. It is
the largest security jump available without dedicated hardware.

## Why this gets closer to 100%

| | gVisor (previous) | Firecracker (target) |
|---|---|---|
| Boundary | user-space (Sentry) | hardware (KVM/ring -1) |
| Surface | Sentry + gofer + syscall emulation | minimal VMM (~50k LOC) |
| Escape requires | a Sentry bug | a KVM/Firecracker bug |

## What is in this folder

| File | What it does |
|---|---|
| `config.json` | Static VMM config: vsock (unique `guest_cid`), no network, read-only rootfs, 1 vCPU + 512 MiB. |
| `lanzar.py` | Entrypoint: checks binary+config and runs firecracker in the foreground (systemd manages it). |
| `hcma-firecracker.service` | Fail-closed systemd unit (`Restart=no`, `MemoryMax`, `TasksMax`, `BindsTo` monitor). |
| `auditd.rules` | Out-of-band signal: auditd watches sandbox network/secrets/privs -> `observador_audit.py`. |
| `deploy.sh` | Idempotent root deployment (firecracker + kernel + rootfs + group + permissions + auditd + unit). |

## Deployment (root, once)

```bash
# minimal guest kernel (vmlinux) + minimal ext4 rootfs with the agent + python.
# Passed by argument/env; if they are already in /var/lib/hcma-firecracker, deploy.sh keeps them.
sudo HCMA_VMLINUX=/path/vmlinux HCMA_ROOTFS=/path/rootfs.ext4 \
  sh arquitectura/firecracker/deploy.sh
```

`deploy.sh` installs firecracker (apt), places kernel+rootfs in `/var/lib/hcma-firecracker/`, creates
the `hcma-audit` group, sets the read-only store permissions, installs the auditd rules and the
systemd unit, and reloads the daemon. It is **idempotent** (safe to run again).

## Agent <-> monitor communication

The agent currently talks over an **inherited socket** (runsc FD pass). Firecracker has no FD-pass:
it uses **virtio-vsock**. The shim is already implemented in the monitor:

```
agent (guest) -- vsock --> firecracker -- uds (/run/hcma/vsock.sock) --> monitor
```

- **Host (monitor)**: `hcma_monitor_daemon._crear_socket_agente` does `bind` + `listen` + `0600`
  on `/run/hcma/vsock.sock` when `HCMA_VSOCK_UDS` is set. The monitor OWNS the UDS (its lifecycle
  dies with the monitor process: fail-closed).
- **VMM (firecracker)**: **connects** to that UDS when the guest starts a vsock connection
  (CID 2). `lanzar.py` does NOT create or unlink the UDS (the monitor does).
- **Guest**: connects to `vsock` CID=2 (host), port 5000, and speaks the SAME `monitor_ipc` framing.

The framing (`monitor_ipc`), `session_id`, reclaim, greeting and governance **do not change**: only
the transport changes (vsock instead of inherited FD). The blacklist (`vigilancia_gvisor`) and
`observador_audit.py` (out-of-band signal) stay the same.

## Static config (`config.json`)

`vsock` with a unique `guest_cid` per agent; empty `network-interfaces` (no network, as today);
`drives` with read-only rootfs; `machine-config` with 1 vCPU + 512 MiB (matching the unit's
`MemoryMax`).

## Out-of-band signal (auditd)

`auditd.rules` makes the kernel emit events for the sandbox syscalls (`runsc`/gofer or
`firecracker`) that indicate escape, independent of the Sentry/VMM:

1. **Network** - the sandbox has no network; any `connect`/`socket`/`sendto`/`recvfrom` from the
   sandbox binary is escape/exfiltration.
2. **Secrets** - path watches over the signer's key and the monitor/proxy state.
3. **Privileges** - `setuid`/`setgid`/`capset` (`mount`/`unshare`/`ptrace` are excluded because
   runsc uses them legitimately when building the sandbox).

`observador_audit.py` parses the result (`avc: denied {...}` and `syscall ... success=no`) and
`observador_kernel.py --clasificador audit` reports it to the monitor via the `procesar_log_audit`
op. The watcher runs as systemd (`hcma-audit-watcher.service`, installed by `deploy.sh`) and tails
`/var/log/audit/audit.log`, feeding `incidentes.registrar` + blocking.

The monitor, in vsock mode, listens on **two** agent sockets: the regular one
(`/run/hcma/monitor.sock`, for the host watchers) and the vsock UDS (`/run/hcma/vsock.sock`, for
Firecracker). This keeps the auditd watcher's channel even when the agent migrates from gVisor to
Firecracker.

## Cutover (human decision)

`deploy.sh` does NOT stop gVisor nor start Firecracker automatically. The cutover is manual:

```bash
# 1. monitor in vsock mode (drop-in; the original .service is not edited)
sudo mkdir -p /etc/systemd/system/hcma-monitor.service.d
printf '[Service]\nEnvironment=HCMA_VSOCK_UDS=/run/hcma/vsock.sock\n' | \
  sudo tee /etc/systemd/system/hcma-monitor.service.d/vsock.conf

# 2. stop gVisor and reload the monitor with vsock
sudo systemctl stop hcma-agent-sandbox.service
sudo systemctl restart hcma-monitor.service

# 3. start the microVM
sudo systemctl start hcma-firecracker.service
sudo systemctl enable hcma-firecracker.service   # optional, to keep it persistent
```

While Firecracker is not deployed, the gVisor sandbox + `observador_audit.py` (out-of-band signal)
is the active defense.

## Honesty (there is no 100%)

Firecracker raises the escape barrier from "a Sentry bug" to "a VMM/KVM bug", the maximum achievable
without dedicated hardware. It **does not eliminate** the possibility of escape: there is still a
surface (the ~50k LOC VMM + KVM). The honest statement is "it reduces the probability to 'requires a
hypervisor bug'", not "it is impossible". See `consultas/modelo_amenazas.md`.

**Scope (caveat):** the microVM + px-motor contain and watch the *agent runtime* (the code that runs
here). The LLM is an **external** service we do not control: for demonstration purposes an external
LLM is used, and its behavior happens **outside** this boundary. The containment protects against a
compromised/faulty agent, not against the remote model.

## Building the guest image (vmlinux + rootfs.ext4)

The microVM needs two artifacts that are **not versioned** (regenerated by the scripts):

| Script | Produces | Requirements |
|---|---|---|
| `build_vmlinux.sh` | `vmlinux` (minimal ELF kernel: virtio-blk/vsock/ext4/serial built-in) | gcc make bc bison flex libelf-dev |
| `build_rootfs_firecracker.sh` | `rootfs.ext4` (Debian minbase + python3 + agent + /init) | mmdebstrap, e2fsprogs |

```bash
# 1) guest kernel
sudo sh build_vmlinux.sh              # -> ./vmlinux

# 2) rootfs
sudo sh build_rootfs_firecracker.sh   # -> ./rootfs.ext4

# 3) deploy passing the artifacts
sudo HCMA_VMLINUX=$PWD/vmlinux HCMA_ROOTFS=$PWD/rootfs.ext4 sh deploy.sh
```

`config.json` already includes the `vsock` device (`guest_cid=3`, `uds_path=/run/hcma/vsock.sock`)
and `init=/init` in `boot_args`. `guest_init` mounts `/proc` `/sys` `/dev` and starts
`agente_vsock.py`, which connects to `CID_HOST=2` port `5000`.

### vsock transport (guest -> host)

Firecracker maps each guest vsock port to a **per-port** UDS: `<uds_path>_<port>`. The agent connects
to `CID=2` port `5000`, so Firecracker forwards to `/run/hcma/vsock.sock_5000` (raw passthrough, no
binary header). The monitor listens on that path (`HCMA_VSOCK_UDS=/run/hcma/vsock.sock_5000`, set by
`instalar.sh`); `config.json` only provides the base `/run/hcma/vsock.sock`.

## LLM inside the microVM

To run the model **inside** Firecracker (instead of an external API), with port/circuit config from
the dashboard: see [`LLM_LOCAL.md`](LLM_LOCAL.md).

## Status: validated end-to-end (2026-11-26)

The full path was verified on a Debian 14 host (gcc 16, kernel 7.2):

```
agent (Debian guest) -- virtio-vsock (CID=2:5000) --> firecracker --> /run/hcma/vsock.sock_5000 --> monitor
```

- `hcma-firecracker` -> `active`: the microVM boots Debian, mounts the read-only ext4 rootfs (`vda`)
  and runs `/init`.
- `hcma-px-motor` -> `active`: eBPF loaded and signed policy verified.
- The agent connects over vsock and the monitor responds (`estado` + `propuesta_write` -> `ok: True`).

### Guest kernel adjustments (gcc >= 15 and Firecracker)

`build_vmlinux.sh` applies them automatically:

- `-std=gnu11` forced in `realmode` and `EFI stub` (gcc 15+ uses C23 and breaks `bool`/`false`/`true`).
- `VIRTIO_MMIO` (not `VIRTIO_PCI`): Firecracker exposes devices over MMIO.
- `VSOCKETS` + `VIRTIO_VSOCKETS` + `VIRTIO_VSOCKETS_COMMON` built-in.
- `SYSTEM_TRUSTED_KEYRING` / `SYSTEM_REVOCATION_LIST` / `SECONDARY_TRUSTED_KEYRING` off (avoids OpenSSL).
- `DEBUG_INFO` / `DEBUG_INFO_BTF` off (avoids `pahole`, compiles faster).
- `WERROR` off (new gcc 16 warnings don't become errors).
- `MODULES` off (everything built-in).

### Host adjustments (vsock socket)

- `hcma-firecracker.service`: `ReadWritePaths=/run/hcma` so `ProtectSystem=strict` doesn't leave the
  socket base read-only.
- The monitor's unit creates `/run/hcma` with `RuntimeDirectory=hcma` + `RuntimeDirectoryMode=0711`
  (traversable), and chowns the **socket** `vsock.sock_5000` to group `hcma-vsock` + `chmod 0660`
  (so Firecracker, in that group, can connect).

### Pending (non-blocking for the core)

- `agente_vsock.py` is a demo (connects -> proposes -> exits); `guest_init` relaunches it every 5s.
  The agent's real service loop is the next step.
- The CORTE ceremony (signed checkpoint + real-store cutover) is still manual, by design.
