#!/bin/sh
# instalar.sh — instala la arquitectura HCMA completa de punta a punta (idempotente).
# SPDX-License-Identifier: Apache-2.0
#
# Orquesta los tres frentes en el orden correcto y deja TODO corriendo:
#   1) HCMA core      (montar.sh):     UIDs, claves, monitor, proxy, dashboard, firewall.
#   2) Firecracker    (deploy.sh):     microVM de hardware (boundary) + auditd + unidad systemd.
#   3) px-motor       (deploy.sh):     detector eBPF de escape con política firmada + anti-rollback.
#   4) cutover gVisor → Firecracker + arranque de la microVM y del detector.
#   5) verificación de salud.
#
# Uso:  sudo sh instalar.sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Re-ejecutando con sudo (necesito root)..."
  exec sudo sh "$0" "$@"
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DASH_USER="${HCMA_DASH_USER:-${SUDO_USER:-$(logname 2>/dev/null || true)}}"
[ -n "${DASH_USER}" ] || DASH_USER="root"

# Detectar distro (apt/dnf/pacman/zypper/apk) y mapear paquetes.
. "${REPO}/arquitectura/scripts/distro.sh"
MONITOR_DROPIN="/etc/systemd/system/hcma-monitor.service.d/vsock.conf"

paso() { echo ""; echo "================================================================"; echo " $1"; echo "================================================================"; }

paso "[1/6] Preflight: dependencias del sistema"

# ── Herramientas de runtime (obligatorias para los servicios) ──
FALTANTES=""
check_dep() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "  ok: $1"
  else
    echo "  falta: $1  (paquete: $2)"
    FALTANTES="${FALTANTES} $2"
  fi
}
check_dep python3   "${PYTHON_PKG}"
check_dep git       git
check_dep go        "${GO_PKG}"
check_dep clang     clang
check_dep bpftool   "${BPFTOOL_PKG}"
check_dep ss        "${SS_PKG}"
check_dep useradd   "${SHADOW_PKG}"
check_dep groupadd  "${SHADOW_PKG}"
check_dep usermod   "${SHADOW_PKG}"
check_dep nft       "${NFT_PKG}"
check_dep curl      curl
check_dep systemctl systemd

# ── BTF del kernel: px-motor genera vmlinux.h desde /sys/kernel/btf/vmlinux ──
if [ -f /sys/kernel/btf/vmlinux ]; then
  echo "  ok: BTF del kernel (/sys/kernel/btf/vmlinux)"
else
  echo "  aviso: sin BTF (/sys/kernel/btf/vmlinux) — px-motor no podrá generar vmlinux.h"
  echo "         (kernel con CONFIG_DEBUG_INFO_BTF=n; ver ERRORES.md)"
fi

# ── Build del kernel huésped (vmlinux) ──
if [ -f /usr/include/libelf.h ] || [ -f /usr/include/libelf/libelf.h ]; then
  echo "  ok: libelf (headers)"
else
  echo "  falta: libelf headers (paquete: ${LIBELF_PKG:-libelf-dev})"
  FALTANTES="${FALTANTES} ${LIBELF_PKG:-libelf-dev}"
fi
BUILD_FALTANTE=0
for t in gcc make bc bison flex tar xz; do
  if command -v "$t" >/dev/null 2>&1; then
    echo "  ok: $t (build vmlinux)"
  else
    echo "  falta: $t (build vmlinux; paquete en: ${BUILD_PKGS:-gcc make bc bison flex libelf-dev xz-utils})"
    BUILD_FALTANTE=1
  fi
done
if [ "${BUILD_FALTANTE}" -eq 1 ]; then
  FALTANTES="${FALTANTES} ${BUILD_PKGS:-gcc make bc bison flex libelf-dev xz-utils}"
fi

# ── Build del rootfs ext4 (mmdebstrap: solo distros basadas en apt) ──
case "${PKG_INSTALL:-}" in
  apt-get*)
    ROOTFS_FALTANTE=0
    for t in mmdebstrap mkfs.ext4; do
      if command -v "$t" >/dev/null 2>&1; then
        echo "  ok: $t (build rootfs)"
      else
        echo "  falta: $t (build rootfs; paquete en: ${ROOTFS_PKGS:-mmdebstrap e2fsprogs})"
        ROOTFS_FALTANTE=1
      fi
    done
    if [ "${ROOTFS_FALTANTE}" -eq 1 ]; then
      FALTANTES="${FALTANTES} ${ROOTFS_PKGS:-mmdebstrap e2fsprogs}"
    fi
    ;;
  *)
    echo "  aviso: build de rootfs necesita una distro apt (mmdebstrap). En ${DISTRO_ID} usá un rootfs prebuilt."
    ;;
esac

# ── KVM / virtio-vsock (Firecracker) ──
if [ -e /dev/kvm ]; then
  echo "  ok: /dev/kvm (KVM)"
else
  echo "  ⚠️  /dev/kvm ausente — Firecracker no podrá arrancar la microVM (habilitá VT-x/AMD-V o nested virt)"
fi
if [ -e /dev/vhost-vsock ]; then
  echo "  ok: /dev/vhost-vsock (virtio-vsock)"
else
  echo "  ⚠️  /dev/vhost-vsock ausente — el transporte agente↔monitor por vsock no va a funcionar"
fi
if grep -qE '(^|[[:space:]])(vmx|svm)([[:space:]]|$)' /proc/cpuinfo; then
  echo "  ok: virtualización por hardware (vmx/svm) presente"
else
  echo "  ⚠️  sin vmx/svm en /proc/cpuinfo — KVM no va a funcionar"
fi

# ── Instalar dependencias faltantes ──
if [ -n "${FALTANTES}" ]; then
  if [ "${distro_ok}" -ne 0 ]; then
    echo "  No se auto-instalan dependencias: distro no soportada (${DISTRO_ID}). Instalalas a mano."
  else
    echo "  Instalando dependencias faltantes:${FALTANTES} ${HEADERS_PKG}"
    ${PKG_UPDATE} || true
    # shellcheck disable=SC2086
    ${PKG_INSTALL} ${FALTANTES} ${HEADERS_PKG}
  fi
fi

# ── Artefactos de la microVM (vmlinux + rootfs.ext4) ──
FC_DIR="${REPO}/arquitectura/firecracker"
if [ -f "${FC_DIR}/vmlinux" ] && [ -f "${FC_DIR}/rootfs.ext4" ]; then
  echo "  ok: vmlinux + rootfs.ext4 presentes en el repo (se desplegarán)"
elif [ -f /var/lib/hcma-firecracker/vmlinux ] && [ -f /var/lib/hcma-firecracker/rootfs.ext4 ]; then
  echo "  ok: vmlinux + rootfs.ext4 ya desplegados en /var/lib/hcma-firecracker"
else
  echo "  ⚠️  faltan vmlinux y/o rootfs.ext4 — Firecracker no podrá bootear sin ellos."
  echo "     Generálos (el kernel tarda ~10-15 min):"
  echo "       cd ${FC_DIR} && sudo sh build_vmlinux.sh && sudo sh build_rootfs_firecracker.sh"
  echo "     O pasá artefactos ya compilados:"
  echo "       sudo HCMA_VMLINUX=/ruta/vmlinux HCMA_ROOTFS=/ruta/rootfs.ext4 sh ${REPO}/arquitectura/instalar.sh"
fi

paso "[2/6] HCMA core (monitor / proxy / dashboard / firma)"
sh "${REPO}/arquitectura/montaje/montar.sh"

paso "[3/6] Firecracker (boundary de hardware)"
# Si el repo trae los artefactos ya compilados, pasalos al deploy para que
# "clone → instalar → andar" no exija compilar a mano. deploy.sh los conserva
# si ya están en /var/lib/hcma-firecracker.
if [ -f "${FC_DIR}/vmlinux" ]; then export HCMA_VMLINUX="${FC_DIR}/vmlinux"; fi
if [ -f "${FC_DIR}/rootfs.ext4" ]; then export HCMA_ROOTFS="${FC_DIR}/rootfs.ext4"; fi
sh "${REPO}/arquitectura/firecracker/deploy.sh" || echo "  ⚠️  Firecracker no se pudo desplegar (no bloquea el core)."

paso "[4/6] px-motor (detección eBPF de escape, política firmada)"
sh "${REPO}/arquitectura/px-motor/deploy.sh" || echo "  ⚠️  px-motor no se pudo desplegar (no bloquea el core)."

paso "[5/6] Cutover gVisor → Firecracker + arranque"
# 4a. monitor en modo vsock. Firecracker reenvía guest->host a <uds_path>_<puerto>; el agente
#     conecta a CID=2 puerto 5000, así que el monitor escucha en la ruta POR PUERTO (no la base).
install -d -m 0755 /etc/systemd/system/hcma-monitor.service.d
printf '[Service]\nEnvironment=HCMA_VSOCK_UDS=/run/hcma/vsock.sock_5000\n' > "${MONITOR_DROPIN}"
echo "  ok: monitor en modo vsock (${MONITOR_DROPIN})"

systemctl daemon-reload

# 4b. apagar gVisor solo si todavía está corriendo (idempotente).
if systemctl is-active --quiet hcma-agent-sandbox.service 2>/dev/null; then
  systemctl stop hcma-agent-sandbox.service
  echo "  ok: gVisor (hcma-agent-sandbox) detenido"
else
  echo "  ok: gVisor ya no corre (o no estaba instalado)"
fi

# 4c. recargar el monitor para que tome el vsock (fail-closed: si el monitor cae, firecracker cae).
systemctl restart hcma-monitor.service
echo "  ok: monitor reiniciado (vsock activo)"

# 4d. arrancar y fijar la microVM + el detector.
systemctl enable hcma-firecracker.service >/dev/null 2>&1 || true
systemctl start  hcma-firecracker.service 2>/dev/null || echo "  ⚠️  Firecracker no arrancó (¿binario instalado?)."
echo "  ok: Firecracker arrancado"

systemctl start hcma-px-motor.service 2>/dev/null || {
  echo "  ⚠️  px-motor no arrancó; mirá: journalctl -u hcma-px-motor -n 40" >&2
}

# 4e. dashboard + proxy (el dashboard sirve el link y la consola). `restart` para que una
#     reinstalación cargue el código nuevo (start sería no-op si ya están corriendo).
systemctl restart hcma-dashboard.service hcma-proxy.service
echo "  ok: dashboard + proxy arrancados (recargados)"

paso "[6/6] Verificación de salud"
echo "  servicios:"
for svc in hcma-monitor hcma-proxy hcma-dashboard hcma-firecracker hcma-px-motor; do
  st="$(systemctl is-active "$svc.service" 2>/dev/null || echo inactivo)"
  printf '    %-22s %s\n' "$svc" "$st"
done

# Link del dashboard: HTTPS autofirmado, con hostname e IP para acceso local o remoto.
DASH_HOST="$(hostname 2>/dev/null || echo localhost)"
DASH_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -f /etc/hcma/dash.env ]; then
  . /etc/hcma/dash.env
fi
DASH_PORT="${HCMA_DASH_PORT:-8787}"

# Credencial inicial del admin (se muestra UNA sola vez; el primer login fuerza el cambio).
# Se corre como charasky (usuario del dashboard) para no romper el ownership de auth.db.
DASH_DIR="${REPO}/arquitectura/dashboard"
ADMIN_LINE="$(sudo -u "$DASH_USER" python3 "${DASH_DIR}/setup_admin.py" 2>/dev/null || true)"
ADMIN_USER="$(printf '%s' "$ADMIN_LINE" | awk '{print $1}')"
ADMIN_PW="$(printf '%s' "$ADMIN_LINE" | awk '{print $2}')"

echo ""
echo "================================================================================"
echo " Instalación completa."
echo ""
echo "   Dashboard (HTTPS autofirmado — aceptá el aviso de certificado):"
echo "     https://${DASH_HOST}:${DASH_PORT}/"
if [ -n "${DASH_IP}" ]; then
  echo "     https://${DASH_IP}:${DASH_PORT}/   (IP local)"
fi
echo ""
if [ "${ADMIN_PW}" = "__YA_CONFIGURADA__" ]; then
  echo "   Credencial admin:  ${ADMIN_USER}  (contraseña ya configurada; no se rota en reinstalación)"
else
  echo "   Credencial admin (cámbiala en el primer login — se muestra UNA sola vez):"
  echo "     usuario:     ${ADMIN_USER}"
  echo "     contraseña:  ${ADMIN_PW}"
fi
echo ""
echo "   API (sin autenticar, con -k): curl -sk https://localhost:${DASH_PORT}/api/components"
echo ""
echo "   Gateway LLM (OpenAI-compatible — Zed / VSCode / chat):"
echo "     endpoint:      https://localhost:${DASH_PORT}/v1"
if [ -n "${DASH_IP}" ]; then
  echo "     endpoint (IP): https://${DASH_IP}:${DASH_PORT}/v1"
fi
echo "     gateway key:   en el dashboard → pestaña LLM → botón 'copiar'"
echo ""
echo "   Alertas:    tail -f /var/lib/px-motor/alerts.jsonl"
echo "   Salud:      sudo sh ${REPO}/arquitectura/montaje/verificar.sh"
echo ""
echo " La ceremonia de CORTE de estado (checkpoint + store al monitor) sigue siendo manual:"
echo " ver ${REPO}/arquitectura/montaje/README.md"
echo ""
echo " Requisitos + errores conocidos y cómo resolverlos:"
echo "   ${REPO}/arquitectura/ERRORES.md"
echo "================================================================================"
