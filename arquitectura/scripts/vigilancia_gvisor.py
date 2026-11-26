#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""vigilancia_gvisor.py — containment observability: detects escapes INSIDE gVisor.

Closes the "silent blocks" gap: static containment (--network=none, caps 0, read-only rootfs,
rlimits) BLOCKS but does not LOG the attempt. Here we translate sandbox events into escape
INCIDENTS in HCMA (incidentes.py):

  - `clasificar_linea(linea)`  : a runsc log line (--debug-log/--strace) → escape vector
    (red / fs_host / privilegios / recursos / proc_sys), or None if benign.
  - `clasificar_exit(rc)`      : the agent process exit code → escape vector for anomalous
    death (SIGKILL/SIGXCPU = fork bomb / CPU exceeded), or None if normal.
  - `procesar_log(...)`        : classifies a log and REGISTERS + BLOCKS each incident in HCMA.
  - `procesar_eventos(...)`    : same with already-normalized events (idempotent by hash).

Detection is only the first half; the full cycle is closed by `incidentes.py`
(detected → blocked → under_review → patched → rehabilitated) with the human deciding whether to
patch the technique and re-enable it. The agent NEVER writes the incident.
"""
import os
import re
import sys

DEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".deps")
if DEP not in sys.path and os.path.isdir(DEP):
    sys.path.insert(0, DEP)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import incidentes as INC  # noqa: E402

# Syscall patterns (runsc --debug-log/--strace fixtures). Conservative: only clear vectors.
PATRONES_SYSCALL = [
    ("red",         re.compile(r"\b(connect|socket|sendto|recvfrom|bind|accept|raw_socket)\b", re.I)),
    ("privilegios", re.compile(r"\b(setuid|setgid|capset|mount|umount|ptrace|unshare|chroot)\b", re.I)),
    ("recursos",    re.compile(r"\b(ENOMEM|fork.?bomb|resource.?limit|RLIMIT|EAGAIN)\b", re.I)),
    ("proc_sys",    re.compile(r"(/proc/sys|/sys/kernel)")),
    ("tampering",   re.compile(r"\b(chmod|rename|truncate|unlink)\b", re.I)),
]

# Sensitive path BLACKLIST — inherited from the "framework secure" eBPF LSM (motor-go/lsm.bpf.c
# sensitive_write_path_{a,b}, lsm.go, rules.go): "if a process tried to touch something it should
# not". Order = priority (most severe first); match by substring (contains).
PATHS_SENSIBLES = [
    ("escape_fs", [  # namespace/chroot evasion via /proc (access the HOST root/fs)
        "/proc/self/root", "/proc/1/root", "/proc/self/fd", "/proc/self/map_files",
        "/proc/self/cwd"]),
    ("kernel_mem", [  # T1611 escape/rootkit: kernel memory
        "/dev/mem", "/dev/kmem", "/dev/port", "/proc/kcore"]),
    ("data_wipe", [  # T1485: disk write
        "/dev/sd", "/dev/nvme", "/dev/vd", "/dev/xvd", "/dev/hd", "/dev/mmcblk"]),
    ("fs_host", [  # persistence/backdoor/tampering (T1505/T1546/T1556) + HCMA secrets
        "/var/lib/hcma", "/etc/hcma", "humano_ed25519", "/home/charasky", "/root/",
        "/etc/passwd", "/etc/sudoers", "ld.so.preload", "/etc/environment",
        "/sshd_config", "/etc/ssh/sshd_config.d/", "/etc/pam.d/",
        ".bashrc", ".zshrc", ".profile", ".bash_profile", "authorized_keys",
        "/etc/profile", "rc.local", "/etc/sudoers.d/", "/etc/cron",
        "/var/spool/cron", "/etc/profile.d/", "/etc/init.d/", "/etc/ld.so.conf.d/",
        "/etc/ssl/certs/", "/usr/local/share/ca-certificates/", "ca-certificates.conf",
        "/.user.ini", "/.htaccess"]),
    ("recon", [  # T1082/T1087 discovery: reading sensitive files
        "/etc/group", "/etc/shadow", "/.dockerenv", "/proc/1/cgroup", "/proc/self/cgroup",
        "/etc/os-release", "/etc/alpine-release", "/etc/debian_version",
        "/proc/cpuinfo", "/etc/hosts", "/etc/resolv.conf", "/etc/krb5.conf"]),
]

SIGNAL_KILL = 9
SIGNAL_XCPU = 24  # SIGXCPU (triggered by RLIMIT_CPU)

# Human description per vector (so the incident is explanatory, not just a token).
DESCRIPCIONES = {
    "escape_fs": "intento de evadir el namespace/chroot vía /proc (acceder al fs del host)",
    "red": "intento de conexión de red (exfiltración) desde el sandbox",
    "fs_host": "intento de acceder/escribir un archivo sensible del host",
    "kernel_mem": "intento de acceder a memoria del kernel (escape/rootkit)",
    "data_wipe": "intento de escritura a disco (destrucción de datos)",
    "recon": "descubrimiento de archivos sensibles del sistema (reconocimiento)",
    "privilegios": "intento de escalar privilegios (setuid/capset/mount)",
    "recursos": "abuso de recursos (fork bomb / agotamiento / CPU)",
    "proc_sys": "intento de escribir en /proc/sys o /sys",
    "tampering": "intento de manipular/borrar archivos (persistencia o limpieza de evidencia)",
    "transporte": "intento de romper el protocolo IPC (frame inválido)",
}


def clasificar_linea(linea):
    """Returns (escape_mode, vector) for an escape log line, or None if benign.

    Order: first the sensitive paths (by severity: kernel_mem > data_wipe > fs_host > recon),
    then the syscalls (red/privilegios/recursos/proc_sys/tampering)."""
    if not isinstance(linea, str):
        return None
    for vector, paths in PATHS_SENSIBLES:
        for p in paths:
            if p in linea:
                return vector, vector
    for vector, pat in PATRONES_SYSCALL:
        if pat.search(linea):
            return vector, vector
    return None


def clasificar_exit(returncode):
    """Returns (escape_mode, vector) for an anomalous exit code, or None if normal.

    returncode < 0 = killed by signal (runsc propagates the sign in negative); returncode >= 128 =
    shell convention (128 + signal)."""
    if returncode is None:
        return None
    sig = None
    if returncode < 0:
        sig = -returncode
    elif returncode >= 128:
        sig = returncode - 128
    if sig == SIGNAL_XCPU:
        return "cpu_excedida", "recursos"
    if sig == SIGNAL_KILL:
        return "sigkill", "recursos"
    if sig is not None and sig > 0:
        return f"signal_{sig}", "recursos"
    return None


def _id_evento(session_id, modo_escape, detalle):
    import hashlib
    return hashlib.sha256(f"{session_id}|{modo_escape}|{detalle}".encode("utf-8")).hexdigest()[:16]


def procesar_eventos(m, session_id, agent_id, eventos):
    """Registers + BLOCKS one incident per event (idempotent by event hash).

    Enriches the incident with problema (human description), detalle (observable) and comando
    (concrete exploit), so it is auditable and explanatory."""
    incidentes = []
    for ev in eventos or []:
        modo = ev.get("modo_escape")
        vector = ev.get("vector", "desconocido")
        detalle = ev.get("detalle", "")
        comando = ev.get("comando", detalle)          # by default, the exploit is the detail
        problema = ev.get("problema", DESCRIPCIONES.get(vector, vector))
        if not modo:
            continue
        iid = _id_evento(session_id, modo, detalle)
        try:
            INC.registrar(m, iid, modo, vector, session_id, evidencia={"detalle": detalle},
                          agent_id=agent_id, problema=problema, detalle=detalle,
                          comando=comando)
        except ValueError:
            continue  # already registered (idempotent)
        INC.bloquear(m, iid, session_id, motivo=f"escape detectado (gVisor/{vector})")
        INC.cuarentenar(m, iid, session_id, motivo="aislamiento total tras bloqueo")
        incidentes.append(iid)
    return incidentes


def procesar_log(m, session_id, agent_id, lineas):
    """Classifies a log (list of lines) and registers the escape incidents it finds."""
    eventos = []
    for ln in lineas or []:
        clas = clasificar_linea(ln)
        if clas:
            eventos.append({"modo_escape": clas[0], "vector": clas[1],
                            "detalle": ln.strip(), "comando": ln.strip()})
    return procesar_eventos(m, session_id, agent_id, eventos)


def procesar_exit(m, session_id, agent_id, returncode):
    """Registers an incident for an anomalous exit code (death by signal = DoS/resources)."""
    clas = clasificar_exit(returncode)
    if clas is None:
        return None
    return procesar_eventos(m, session_id, agent_id,
                            [{"modo_escape": clas[0], "vector": clas[1],
                              "detalle": f"returncode={returncode}"}])


if __name__ == "__main__":
    from memoria_sqlite import HCMAMemoriaSQLite
    m = HCMAMemoriaSQLite(":memory:")
    log = [
        "connect(...) => EPERM (network=none)",
        "open(/var/lib/hcma-signer/humano_ed25519.key) => ENOENT",
        "setuid(0) => EPERM",
        "clone(...) => ENOMEM",
        "write(/proc/sys/kernel/hostname) => EPERM",
        "read(fd) => ok (benigno)",
    ]
    print("incidentes:", procesar_log(m, "s1", "agente-a", log))
    print("exit SIGKILL:", procesar_exit(m, "s1", "agente-a", -9))
    print("activos:", INC.incidentes_activos(m))
