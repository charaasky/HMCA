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
if [ -e /dev/kvm ]; then
  echo "  ok: /dev/kvm (KVM)"
else
  echo "  aviso: /dev/kvm ausente — Firecracker no podrá arrancar la microVM"
fi
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

paso "[2/6] HCMA core (monitor / proxy / dashboard / firma)"
sh "${REPO}/arquitectura/montaje/montar.sh"

paso "[3/6] Firecracker (boundary de hardware)"
sh "${REPO}/arquitectura/firecracker/deploy.sh"

paso "[4/6] px-motor (detección eBPF de escape, política firmada)"
sh "${REPO}/arquitectura/px-motor/deploy.sh"

paso "[5/6] Cutover gVisor → Firecracker + arranque"
# 4a. monitor en modo vsock (drop-in; el UDS lo crea el monitor, firecracker se conecta).
install -d -m 0755 /etc/systemd/system/hcma-monitor.service.d
printf '[Service]\nEnvironment=HCMA_VSOCK_UDS=/run/hcma/vsock.sock\n' > "${MONITOR_DROPIN}"
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
systemctl start  hcma-firecracker.service
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
echo "================================================================================"
