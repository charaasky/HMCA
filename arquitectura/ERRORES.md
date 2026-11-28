# ERRORES.md — HCMA requirements and troubleshooting

Reference guide for the end-to-end installer (`sudo sh arquitectura/instalar.sh`). If something
fails, look up the symptom here. Each entry has **symptom -> cause -> solution**.

Quick diagnosis:

```bash
sudo sh arquitectura/montaje/verificar.sh               # full health check
journalctl -u hcma-monitor     -n 50 --no-pager          # monitor + vsock socket
journalctl -u hcma-firecracker -n 50 --no-pager          # microVM (boot + vsock)
journalctl -u hcma-px-motor    -n 50 --no-pager          # eBPF detector
journalctl -u hcma-dashboard   -n 50 --no-pager          # dashboard / TLS
```

---

## 1. Requirements (what `instalar.sh` validates)

The `instalar.sh` preflight checks and auto-installs (on supported distros) these categories:

| Category | What it checks | Package(s) |
|---|---|---|
| Runtime (services) | `python3 git go clang bpftool ss useradd groupadd usermod nft curl systemctl` | per distro (`distro.sh`) |
| Kernel BTF | `/sys/kernel/btf/vmlinux` | kernel with `CONFIG_DEBUG_INFO_BTF=y` (not a package) |
| Guest kernel build (`vmlinux`) | `gcc make bc bison flex tar xz` + `libelf` headers | `BUILD_PKGS` / `LIBELF_PKG` |
| Rootfs build (`rootfs.ext4`) | `mmdebstrap mkfs.ext4` (apt distros only) | `ROOTFS_PKGS` |
| KVM / vsock | `/dev/kvm`, `/dev/vhost-vsock`, `vmx`/`svm` flag | host/BIOS (not a package) |
| microVM artifacts | `vmlinux` + `rootfs.ext4` (repo or `/var/lib/hcma-firecracker`) | `build_vmlinux.sh` / `build_rootfs_firecracker.sh` |

If a distro is not in `distro.sh`, the installer does **not** auto-install anything and says so:
install the packages by hand (names are in `distro.sh`).

---

## 2. Environment / packages

### `E: The repository «https://…» is not signed` / `Missing key … needed to verify signature`
- **Cause**: a third-party apt repo (Fortinet, etc.) without a signature is in
  `/etc/apt/sources.list.d/` and blocks `apt-get update`.
- **Solution**: disable or remove that repo and update again.
  ```bash
  ls /etc/apt/sources.list.d/
  sudo rm /etc/apt/sources.list.d/forticlient*.list      # or the offending repo
  sudo apt-get update
  ```

### `unsupported distro` (the installer does not auto-install packages)
- **Cause**: the distro is not mapped in `distro.sh`.
- **Solution**: install the packages from `distro.sh` for your family by hand and re-run.

### Firecracker: `curl: (23) … ERROR on write` / `install: cannot stat …`
- **Cause**: partial download of the GitHub binary (or a stale `.tgz` in `/tmp`).
- **Solution**: `deploy.sh` already downloads atomically with retries. Manually:
  ```bash
  sudo rm -f /tmp/firecracker-*.tgz /tmp/fc-extract-* -r
  sudo sh arquitectura/firecracker/deploy.sh
  ```

---

## 3. KVM / Firecracker (hardware boundary)

### `/dev/kvm missing` or `no vmx/svm in /proc/cpuinfo`
- **Cause**: hardware virtualization disabled in BIOS/UEFI, or (on a VM host) no *nested virt*.
- **Solution**: enable VT-x/AMD-V in BIOS. To run Firecracker *inside* a VM, enable nested virt:
  ```bash
  # QEMU host: -cpu host  (run_vm_test.sh already uses it)
  # verify inside the VM: grep -m1 -oE 'vmx|svm' /proc/cpuinfo
  ```

### `/dev/vhost-vsock missing`
- **Cause**: the `vhost_vsock` module is not loaded / not available.
- **Solution**:
  ```bash
  sudo modprobe vhost_vsock
  ls -l /dev/vhost-vsock
  ```

### Firecracker: `The kernel file cannot be opened: No such file or directory (os error 2)`
- **Cause**: missing guest kernel at `/var/lib/hcma-firecracker/vmlinux`.
- **Solution**: build or pass the `vmlinux`:
  ```bash
  cd arquitectura/firecracker
  sudo sh build_vmlinux.sh                                   # ~10-15 min
  sudo sh deploy.sh "$PWD/vmlinux" "$PWD/rootfs.ext4"
  # or, at install time:
  sudo HCMA_VMLINUX=/path/vmlinux HCMA_ROOTFS=/path/rootfs.ext4 sh arquitectura/instalar.sh
  ```

### Kernel panic: `VFS: Unable to mount root fs on unknown-block(0,0)`
- **Cause**: the guest kernel does not have `virtio-blk` + `ext4` **built-in** (Firecracker exposes
  devices via **virtio-MMIO**, not PCI).
- **Solution**: `build_vmlinux.sh` already enables `VIRTIO_MMIO`, `VIRTIO_BLK`, `EXT4_FS`,
  `VSOCKETS`, `SERIAL_8250`. If compiling by hand, make sure those are `=y` (not modules).

### Firecracker vsock: `Read-only file system (os error 30)`
- **Cause**: `ProtectSystem=strict` leaves `/run/hcma` read-only and Firecracker can't create the
  vsock socket.
- **Solution**: the unit already has `ReadWritePaths=/run/hcma`. Verify:
  ```bash
  grep -n 'ReadWritePaths' arquitectura/firecracker/hcma-firecracker.service
  sudo systemctl daemon-reload && sudo systemctl restart hcma-firecracker
  ```

### Firecracker vsock: `Permission denied (os error 13)`
- **Cause**: `/run/hcma` is not traversable/writable by the non-root user `hcma-firecracker`
  (group `hcma-vsock`).
- **Solution**: the monitor creates `/run/hcma` (`RuntimeDirectory=hcma`, `0711`) and chowns the
  socket `vsock.sock_5000` to `hcma-vsock` (`0660`). Check:
  ```bash
  ls -ld /run/hcma                        # drwx--x--x (0711)
  ls -l /run/hcma/vsock.sock_5000         # srw-rw---- hcma-monitor hcma-vsock
  id hcma-firecracker                     # ... groups: kvm, hcma-vsock
  ```

---

## 4. Guest kernel build (`build_vmlinux.sh`)

### `error: cannot use keyword 'false' as enumeration constant` / `'bool' cannot be defined via 'typedef'`
- **Cause**: **gcc >= 15 defaults to C23**; `bool`/`false`/`true` become keywords and break
  `realmode` and the EFI stub (which override `KBUILD_CFLAGS` without `-std=gnu11`).
- **Solution**: `build_vmlinux.sh` already forces `-std=gnu11` in `arch/x86/Makefile` (realmode) and
  `drivers/firmware/efi/libstub/Makefile`. By hand:
  ```bash
  sed -i -E 's/^(REALMODE_CFLAGS[[:space:]]*:= )/\1-std=gnu11 /' arch/x86/Makefile
  sed -i 's/-D__DISABLE_EXPORTS$/-D__DISABLE_EXPORTS -std=gnu11/' drivers/firmware/efi/libstub/Makefile
  ```

### `fatal error: openssl/bio.h: No such file or directory` (in `certs/extract-cert.c`)
- **Cause**: the `certs` subsystem (keyrings) requires OpenSSL headers.
- **Solution**: `build_vmlinux.sh` disables `SYSTEM_TRUSTED_KEYRING`, `SECONDARY_TRUSTED_KEYRING`
  and `SYSTEM_REVOCATION_LIST` (not needed for the microVM). Alternative:
  `sudo apt-get install -y libssl-dev`.

### `make: *** [arch/x86] Error 2` with no clear message
- **Cause**: missing `bc`/`bison`/`flex`/`libelf-dev` (the kernel build needs them).
- **Solution**: `sudo apt-get install -y gcc make bc bison flex libelf-dev xz-utils` (or the distro
  equivalent; see `distro.sh` -> `BUILD_PKGS`).

---

## 5. px-motor (eBPF detector)

### `Unknown key 'StartLimitIntervalSec' in section [Service]`
- **Cause**: on modern systemd, `StartLimitIntervalSec`/`StartLimitBurst` go in `[Unit]`, not
  `[Service]`.
- **Solution**: already fixed in `arquitectura/px-motor/hcma-px-motor.service` (they are in `[Unit]`).
  Reinstall the unit and reload:
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl restart hcma-px-motor
  ```

### `bpftool btf dump … No such file or directory` (no BTF)
- **Cause**: the running kernel lacks `CONFIG_DEBUG_INFO_BTF=y`, so there is no
  `/sys/kernel/btf/vmlinux` to generate `vmlinux.h`.
- **Solution**: use a kernel with BTF (stock Ubuntu/Debian kernels have it) or enable BTF and reboot.
  ```bash
  ls -l /sys/kernel/btf/vmlinux
  ```

### Failure compiling the `.bpf.c` probes
- **Cause**: `clang` without the `bpf` target, or a missing/corrupt `vmlinux.h`.
- **Solution**: `sudo apt-get install -y clang bpftool linux-headers-amd64` (or equivalent) and make
  sure you have BTF; then re-run `px-motor/deploy.sh`.

---

## 6. Rootfs (`build_rootfs_firecracker.sh`)

### `mmdebstrap missing`
- **Cause**: `mmdebstrap` only exists on apt-based distros (Debian/Ubuntu and derivatives).
- **Solution**: on an apt distro: `sudo apt-get install -y mmdebstrap e2fsprogs`. On other distros
  the rootfs build is not supported; use a prebuilt `rootfs.ext4` and pass it with
  `HCMA_ROOTFS=/path/rootfs.ext4`.

---

## 7. Dashboard / network

### `curl: (7) Failed to connect … port 8787` / dashboard not responding
- **Cause**: the port is **dynamic** (if 8787 is taken, the next one is used) and stored in
  `/etc/hcma/dash.env`.
- **Solution**:
  ```bash
  . /etc/hcma/dash.env; echo "port: $HCMA_DASH_PORT"
  sudo systemctl status hcma-dashboard --no-pager
  curl -skS https://localhost:$HCMA_DASH_PORT/api/components
  ```

### The health check reports `gVisor legacy not installed`
- **Cause**: after the Firecracker cutover, `runsc` is no longer the boundary (it is legacy).
- **Solution**: **expected**; the gVisor check is informational. The active boundary is Firecracker
  (`firecracker installed` + `vmlinux` + `rootfs`).

---

## 8. Monitor / vsock socket

### `MONITOR MUERE: no se pudo crear los sockets`
- **Cause**: `/run/hcma` cannot be created/written, or the `hcma-vsock`/`hcma-audit` group is missing.
- **Solution**: reinstall the core (`sudo sh arquitectura/montaje/montar.sh`) to recreate the groups,
  and check the monitor unit:
  ```bash
  grep -E 'RuntimeDirectory|SupplementaryGroups' arquitectura/scripts/hcma-monitor.service
  sudo systemctl restart hcma-monitor
  ```

---

## 9. CORTE ceremony (checkpoint + store) — manual, by design

The real store (`/var/lib/hcma-monitor/store/memoria.db`) is installed via a **one-time human
ceremony**, not safely automatable. If the health check reports `store del monitor presente (falta
ceremonia de cutover?)`, follow `arquitectura/montaje/README.md` -> "Manual step: state-cutover
ceremony".

## 10. The agent does not do what it says (governance drift)

If the LLM **reports one thing and does another** (invents a rule, drops a feature without measuring,
or writes code different from what was agreed), that is NOT an output hallucination - it is a
**governance drift**. The answer is not the sandbox nor perplexity, but: do not trust the
self-report, verify the *work product* with a deterministic verifier, and require human
authorization for effectful decisions. See `arquitectura/scripts/verificacion_trabajo.md`.
