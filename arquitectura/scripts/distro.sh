#!/bin/sh
# distro.sh — detección de distro Linux + mapeo de paquetes.
# Debe SOURCearse (`.`) desde los scripts de instalación; no se ejecuta solo.
#
# Expone:
#   DISTRO_ID     -> ubuntu / debian / fedora / arch / opensuse / alpine / ...
#   distro_ok     -> 0 si la distro es soportada, 1 si no
#   PKG_UPDATE    -> refresca índices
#   PKG_INSTALL   -> instala paquetes (sin confirmación)
#   HEADERS_PKG   -> paquete de kernel headers
#   GO_PKG, BPFTOOL_PKG, NFT_PKG, AUDIT_PKG, SS_PKG, SHADOW_PKG, PYTHON_PKG
#   QEMU_PKGS     -> paquetes qemu + cloud-image-utils (para el host del test de VM)

if [ -f /etc/os-release ]; then
  . /etc/os-release
else
  ID=unknown
  ID_LIKE=""
fi

DISTRO_ID="${ID:-unknown}"
distro_ok=0

case "$DISTRO_ID" in
  # ── Debian / Ubuntu (apt) ──────────────────────────────────────────────
  debian|ubuntu|linuxmint|pop|elementary|kali|raspbian|devuan|zorin|mx|deepin|uos|tuxedo|neon)
    PKG_UPDATE="apt-get update"
    PKG_INSTALL="apt-get install -y"
    case "$DISTRO_ID" in
      debian) HEADERS_PKG="linux-headers-amd64" ;;
      *)      HEADERS_PKG="linux-headers-generic" ;;
    esac
    GO_PKG="golang-go"; BPFTOOL_PKG="bpftool"; NFT_PKG="nftables"; AUDIT_PKG="auditd"
    SS_PKG="iproute2"; SHADOW_PKG="passwd"; PYTHON_PKG="python3"
    QEMU_PKGS="qemu-system-x86 qemu-utils cloud-image-utils"
    ;;
  # ── RHEL / Fedora (dnf) ────────────────────────────────────────────────
  fedora|rhel|centos|rocky|almalinux|ol|amzn|oracle|virtuozzo|cloudlinux|sangoma)
    PKG_UPDATE="dnf check-update"
    PKG_INSTALL="dnf install -y"
    HEADERS_PKG="kernel-devel"; GO_PKG="golang"; BPFTOOL_PKG="bpftool"; NFT_PKG="nftables"
    AUDIT_PKG="audit"; SS_PKG="iproute"; SHADOW_PKG="shadow-utils"; PYTHON_PKG="python3"
    QEMU_PKGS="qemu-system-x86 qemu-img cloud-utils"
    ;;
  # ── Arch (pacman) ──────────────────────────────────────────────────────
  arch|manjaro|endeavouros|garuda|artix|cachyos)
    PKG_UPDATE="pacman -Sy"
    PKG_INSTALL="pacman -S --noconfirm"
    HEADERS_PKG="linux-headers"; GO_PKG="go"; BPFTOOL_PKG="bpftool"; NFT_PKG="nftables"
    AUDIT_PKG="audit"; SS_PKG="iproute2"; SHADOW_PKG="shadow"; PYTHON_PKG="python"
    QEMU_PKGS="qemu-system-x86 qemu-img"
    ;;
  # ── openSUSE (zypper) ──────────────────────────────────────────────────
  opensuse*|sles|suse)
    PKG_UPDATE="zypper refresh"
    PKG_INSTALL="zypper --non-interactive install"
    HEADERS_PKG="kernel-devel"; GO_PKG="go"; BPFTOOL_PKG="bpftool"; NFT_PKG="nftables"
    AUDIT_PKG="audit"; SS_PKG="iproute2"; SHADOW_PKG="shadow"; PYTHON_PKG="python3"
    QEMU_PKGS="qemu-x86 qemu-tools"
    ;;
  # ── Alpine (apk) ───────────────────────────────────────────────────────
  alpine)
    PKG_UPDATE="apk update"
    PKG_INSTALL="apk add"
    HEADERS_PKG="linux-headers"; GO_PKG="go"; BPFTOOL_PKG="bpftool"; NFT_PKG="nftables"
    AUDIT_PKG="audit"; SS_PKG="iproute2"; SHADOW_PKG="shadow"; PYTHON_PKG="python3"
    QEMU_PKGS="qemu-system-x86_64 qemu-img"
    ;;
  # ── fallback por ID_LIKE ───────────────────────────────────────────────
  *)
    case "${ID_LIKE:-}" in
      *debian*|*ubuntu*)
        PKG_UPDATE="apt-get update"; PKG_INSTALL="apt-get install -y"
        HEADERS_PKG="linux-headers-generic"; GO_PKG="golang-go"; BPFTOOL_PKG="bpftool"
        NFT_PKG="nftables"; AUDIT_PKG="auditd"; SS_PKG="iproute2"; SHADOW_PKG="passwd"; PYTHON_PKG="python3"
        QEMU_PKGS="qemu-system-x86 qemu-utils cloud-image-utils" ;;
      *rhel*|*fedora*)
        PKG_UPDATE="dnf check-update"; PKG_INSTALL="dnf install -y"
        HEADERS_PKG="kernel-devel"; GO_PKG="golang"; BPFTOOL_PKG="bpftool"
        NFT_PKG="nftables"; AUDIT_PKG="audit"; SS_PKG="iproute"; SHADOW_PKG="shadow-utils"; PYTHON_PKG="python3"
        QEMU_PKGS="qemu-system-x86 qemu-img cloud-utils" ;;
      *arch*)
        PKG_UPDATE="pacman -Sy"; PKG_INSTALL="pacman -S --noconfirm"
        HEADERS_PKG="linux-headers"; GO_PKG="go"; BPFTOOL_PKG="bpftool"
        NFT_PKG="nftables"; AUDIT_PKG="audit"; SS_PKG="iproute2"; SHADOW_PKG="shadow"; PYTHON_PKG="python"
        QEMU_PKGS="qemu-system-x86 qemu-img" ;;
      *)
        distro_ok=1
        PKG_UPDATE=""; PKG_INSTALL=""
        HEADERS_PKG=""; GO_PKG=""; BPFTOOL_PKG=""; NFT_PKG=""; AUDIT_PKG=""
        SS_PKG=""; SHADOW_PKG=""; PYTHON_PKG=""; QEMU_PKGS="" ;;
    esac
    ;;
esac
