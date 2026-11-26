#!/bin/sh
# deploy.sh — despliegue de la microVM Firecracker (boundary de hardware) + señal fuera de banda.
# SPDX-License-Identifier: Apache-2.0
#
# Consolida los pasos ROOT de migración gVisor→Firecracker (una sola vez, idempotente):
#   1) instalar firecracker (apt o verificar binario existente)
#   2) ubicar kernel huésped (vmlinux) y rootfs ext4 en /var/lib/hcma-firecracker/
#   3) grupo de auditoría hcma-audit (lectura de ledgers/store para el dashboard)
#   4) permisos del store del monitor (read-only para hcma-audit)
#   5) reglas auditd (señal fuera de banda) → /etc/audit/rules.d/hcma.rules
#   6) unidad systemd hcma-firecracker.service + daemon-reload
#
# NO hace el CUTOVER automático (apagar gVisor + arrancar Firecracker): es una decisión humana
# de una sola vez. Al final imprime los comandos de cutover.
#
# El kernel y el rootfs se pasan por variable de entorno (o argumento):
#   sudo HCMA_VMLINUX=/ruta/vmlinux HCMA_ROOTFS=/ruta/rootfs.ext4 sh deploy.sh
# o:
#   sudo sh deploy.sh /ruta/vmlinux /ruta/rootfs.ext4
#
# Uso:  sudo sh deploy.sh [vmlinux] [rootfs.ext4]
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Re-ejecutando con sudo (necesito root)..."
  exec sudo sh "$0" "$@"
fi

REPO="/home/charasky/Documentos/HMC"
FIRECRACKER_DIR="/var/lib/hcma-firecracker"
FIRECRACKER_BIN="/usr/bin/firecracker"

VMLINUX="${HCMA_VMLINUX:-${1:-}}"
ROOTFS="${HCMA_ROOTFS:-${2:-}}"

paso() { echo ""; echo "=== $1 ==="; }

# ---- 1. firecracker ----
paso "[1/6] Instalar firecracker"
if [ -x "${FIRECRACKER_BIN}" ]; then
  echo "  ok: firecracker ya instalado en ${FIRECRACKER_BIN}"
else
  echo "  instalando firecracker vía apt..."
  apt-get update
  apt-get install -y firecracker
  if [ ! -x "${FIRECRACKER_BIN}" ]; then
    echo "  ❌ firecracker no quedó en ${FIRECRACKER_BIN}; verificalo e instalalo a mano." >&2
    exit 1
  fi
fi
"${FIRECRACKER_BIN}" --version 2>/dev/null | head -n 1 || true

# ---- 2. kernel + rootfs ----
paso "[2/6] Kernel huésped (vmlinux) + rootfs ext4"
install -d -m 0755 "${FIRECRACKER_DIR}"

if [ -n "${VMLINUX}" ] && [ -f "${VMLINUX}" ]; then
  install -m 0644 "${VMLINUX}" "${FIRECRACKER_DIR}/vmlinux"
  echo "  vmlinux: ${VMLINUX} → ${FIRECRACKER_DIR}/vmlinux"
elif [ -f "${FIRECRACKER_DIR}/vmlinux" ]; then
  echo "  ok: vmlinux ya presente en ${FIRECRACKER_DIR}/vmlinux"
else
  echo "  ⚠️  no se encontró vmlinux. Pasalo como: sudo HCMA_VMLINUX=/ruta/vmlinux sh deploy.sh" >&2
fi

if [ -n "${ROOTFS}" ] && [ -f "${ROOTFS}" ]; then
  install -m 0644 "${ROOTFS}" "${FIRECRACKER_DIR}/rootfs.ext4"
  echo "  rootfs: ${ROOTFS} → ${FIRECRACKER_DIR}/rootfs.ext4"
elif [ -f "${FIRECRACKER_DIR}/rootfs.ext4" ]; then
  echo "  ok: rootfs ya presente en ${FIRECRACKER_DIR}/rootfs.ext4"
else
  echo "  ⚠️  no se encontró rootfs.ext4. Pasalo como: sudo HCMA_ROOTFS=/ruta/rootfs.ext4 sh deploy.sh" >&2
fi

# ---- 2b. config.json + lanzar.py (leídos desde /var/lib, NO desde /home por ProtectHome) ----
paso "[2b/6] config.json + lanzar.py → /var/lib/hcma-firecracker"
install -m 0644 "${REPO}/arquitectura/firecracker/config.json" "${FIRECRACKER_DIR}/config.json"
install -m 0755 "${REPO}/arquitectura/firecracker/lanzar.py" "${FIRECRACKER_DIR}/lanzar.py"
echo "  ok: config.json + lanzar.py instalados en ${FIRECRACKER_DIR}"

# ---- 3. grupo de auditoría ----
paso "[3/6] Grupo de auditoría hcma-audit"
if getent group hcma-audit >/dev/null 2>&1; then
  echo "  ok: grupo hcma-audit ya existe (gid $(getent group hcma-audit | cut -d: -f3))"
else
  groupadd --system --gid 1002 hcma-audit
  echo "  creado: grupo hcma-audit (gid 1002)"
fi
usermod -a -G hcma-audit charasky 2>/dev/null || echo "  AVISO: no se pudo agregar charasky a hcma-audit"

# ---- 4. permisos del store (read-only para el dashboard) ----
paso "[4/6] Permisos del store del monitor (read-only hcma-audit)"
if [ -d /var/lib/hcma-monitor ]; then
  chgrp -R hcma-audit /var/lib/hcma-monitor 2>/dev/null || true
  chmod 750 /var/lib/hcma-monitor /var/lib/hcma-monitor/store 2>/dev/null || true
  [ -f /var/lib/hcma-monitor/store/memoria.db ] && chmod 640 /var/lib/hcma-monitor/store/memoria.db || true
  [ -f /var/lib/hcma-monitor/nonces.log ] && chmod 640 /var/lib/hcma-monitor/nonces.log || true
  echo "  ok: /var/lib/hcma-monitor legible por hcma-audit (escritura sigue del UID 999)"
else
  echo "  AVISO: /var/lib/hcma-monitor no existe aún (¿montaste la arquitectura?)"
fi

# ---- 4b. usuario NO-root para Firecracker + grupo hcma-vsock ----
paso "[4b/6] Usuario hcma-firecracker (NO-root) + grupo hcma-vsock"
if getent group hcma-vsock >/dev/null 2>&1; then
  echo "  ok: grupo hcma-vsock ya existe (gid $(getent group hcma-vsock | cut -d: -f3))"
else
  groupadd --system hcma-vsock
  echo "  creado: grupo hcma-vsock"
fi
if getent passwd hcma-firecracker >/dev/null 2>&1; then
  echo "  ok: usuario hcma-firecracker ya existe"
else
  useradd --system --no-create-home --shell /usr/sbin/nologin hcma-firecracker
  echo "  creado: usuario hcma-firecracker"
fi
usermod -a -G kvm,hcma-vsock hcma-firecracker
echo "  hcma-firecracker en grupos: kvm + hcma-vsock (sin capabilities, sin home)"

# ---- 5. reglas auditd ----
paso "[5/6] Reglas auditd (señal fuera de banda)"
if [ -d /etc/audit/rules.d ]; then
  install -m 0640 "${REPO}/arquitectura/firecracker/auditd.rules" /etc/audit/rules.d/hcma.rules
  if command -v augenrules >/dev/null 2>&1; then
    augenrules --load
    echo "  reglas cargadas con augenrules --load"
  elif command -v auditctl >/dev/null 2>&1; then
    auditctl -R /etc/audit/rules.d/hcma.rules
    echo "  reglas cargadas con auditctl -R"
  else
    echo "  ⚠️  auditd instalado pero augenrules/auditctl no disponible" >&2
  fi
else
  echo "  ⚠️  auditd no instalado (sin /etc/audit/rules.d). Se omite la señal fuera de banda."
  echo "     Para activarla: sudo apt-get install -y auditd y re-correr este script." >&2
fi

# ---- 6. unidades systemd ----
paso "[6/6] Unidades systemd (firecracker + audit-watcher + monitor actualizado)"
install -m 0644 "${REPO}/arquitectura/firecracker/hcma-firecracker.service" \
  /etc/systemd/system/hcma-firecracker.service
install -m 0644 "${REPO}/arquitectura/scripts/hcma-audit-watcher.service" \
  /etc/systemd/system/hcma-audit-watcher.service
# El monitor se reinstala para que tome SupplementaryGroups=hcma-vsock (necesario para el chown
# del socket vsock). El restart efectivo ocurre en el CUTOVER.
install -m 0644 "${REPO}/arquitectura/scripts/hcma-monitor.service" \
  /etc/systemd/system/hcma-monitor.service
systemctl daemon-reload
echo "  unidades instaladas y daemon recargado"

echo ""
echo "================================================================================"
echo " Despliegue Firecracker completo."
echo ""
echo " CUTOVER (decisión humana): reemplaza gVisor por Firecracker:"
echo "   1. Configurar el monitor en modo vsock (drop-in):"
echo "        sudo mkdir -p /etc/systemd/system/hcma-monitor.service.d"
echo "        printf '[Service]\\nEnvironment=HCMA_VSOCK_UDS=/run/hcma/vsock.sock\\n' |"
echo "          sudo tee /etc/systemd/system/hcma-monitor.service.d/vsock.conf"
echo "   2. sudo systemctl stop hcma-agent-sandbox.service    # apagar gVisor"
echo "   3. sudo systemctl restart hcma-monitor.service      # recargar con vsock"
echo "   4. sudo systemctl start hcma-firecracker.service    # arrancar la microVM"
echo "      (para que quede fijo: sudo systemctl enable hcma-firecracker.service)"
echo ""
echo " Mientras no hagas el cutover, el sandbox gVisor + auditd sigue siendo la defensa vigente."
echo "================================================================================"
