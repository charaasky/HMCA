# VM test - end-to-end HCMA install

Harness to test that `sudo sh arquitectura/instalar.sh` works on a **virgin** machine, without
touching your real host: boots an Ubuntu 24.04 VM with QEMU/KVM, clones the repo, runs the installer
(dependency preflight + steps 1..6) and verifies service health.

## Requirements

- **KVM** on the host (`/dev/kvm`) and **nested virt enabled** (so Firecracker can run *inside* the
  VM).
- ~40 GB free disk and ~8 GB free RAM.
- `wget`, `ssh`, `scp` (standard).

The script installs only the missing QEMU pieces (`qemu-system-x86 qemu-utils cloud-image-utils`).

## Usage

```bash
cd arquitectura/vm
sh run_vm_test.sh
```

What it does:

1. Installs qemu + cloud-image-utils (if missing).
2. Downloads the Ubuntu 24.04 cloud image (only the first time).
3. Generates a cloud-init seed with the `ubuntu` user + your SSH key.
4. Boots the VM with `-cpu host` (nested virt) and SSH forwarding to port `2222`.
5. Waits for the VM to come up, copies `provision.sh` and runs it.
6. Prints the result.

## Manual check

If you prefer to control the process:

```bash
# enter the VM
ssh -p 2222 ubuntu@localhost

# run the install
bash /tmp/provision.sh        # (or copy provision.sh again)

# follow the log
tail -f /var/log/hcma-install.log
```

## Shut down the VM

```bash
sh teardown.sh
```

Stops the test qemu and removes `.vm/` (image + seed), freeing disk. HCMA uninstall (inside the VM or
on any host) is: `sudo sh arquitectura/desinstalar.sh`.

## Notes

- The installer creates systemd users/groups, installs packages, compiles the eBPF probes and boots
  Firecracker/KVM. That is why the VM needs **nested virt**; without it, the Firecracker step fails
  (expected).
- The install can take several minutes (apt + Go/eBPF build).
