#!/bin/sh
# desinstalar.sh — desinstala la arquitectura HCMA de forma idempotente y fail-safe.
# SPDX-License-Identifier: Apache-2.0
#
# Revierte lo que instala `instalar.sh`: servicios systemd, usuarios/grupos,
# directorios, binarios, reglas auditd y el firewall nftables.
#
# NO desinstala los paquetes del gestor (firecracker/clang/golang/...) — se listan
# al final para removerlos a mano, porque pueden ser compartidos con otros usos.
#
# Uso:  sudo sh desinstalar.sh
set -u   # sin set -e: limpiamos todo aunque algún paso falle

if [ "$(id -u)" -ne 0 ]; then
  echo "Re-ejecutando con sudo (necesito root)..."
  exec sudo sh "$0" "$@"
fi

paso() { echo ""; echo "=== $1 ==="; }

paso "[1/6] Detener y deshabilitar servicios"
for s in hcma-firecracker hcma-px-motor hcma-audit-watcher hcma-dashboard \
         hcma-proxy hcma-monitor hcma-agent-sandbox hcma-lean-sandbox; do
  systemctl stop "$s.service" 2>/dev/null || true
  systemctl disable "$s.service" 2>/dev/null || true
  echo "  detenido/deshabilitado: $s"
done
# drop-in del monitor (modo vsock)
rm -f /etc/systemd/system/hcma-monitor.service.d/vsock.conf
rmdir /etc/systemd/system/hcma-monitor.service.d 2>/dev/null || true

paso "[2/6] Quitar unidades systemd"
rm -f /etc/systemd/system/hcma-monitor.service \
      /etc/systemd/system/hcma-proxy.service \
      /etc/systemd/system/hcma-dashboard.service \
      /etc/systemd/system/hcma-firecracker.service \
      /etc/systemd/system/hcma-px-motor.service \
      /etc/systemd/system/hcma-audit-watcher.service \
      /etc/systemd/system/hcma-agent-sandbox.service \
      /etc/systemd/system/hcma-lean-sandbox.service
systemctl daemon-reload
echo "  unidades eliminadas y daemon recargado"

paso "[3/6] Quitar firewall nftables (tabla hcma_egreso)"
if command -v nft >/dev/null 2>&1; then
  nft delete table inet hcma_egreso 2>/dev/null || true
  echo "  tabla hcma_egreso eliminada (si existía)"
fi

paso "[4/6] Quitar usuarios y grupos"
for u in hcma-firecracker hcma-agent hcma-signer hcma-proxy hcma-monitor; do
  if id "$u" >/dev/null 2>&1; then
    userdel "$u" 2>/dev/null && echo "  usuario borrado: $u" || echo "  aviso: no se pudo borrar $u"
  fi
done
for g in hcma-red hcma-audit hcma-vsock; do
  if getent group "$g" >/dev/null 2>&1; then
    groupdel "$g" 2>/dev/null && echo "  grupo borrado: $g" || echo "  aviso: no se pudo borrar grupo $g"
  fi
done

paso "[5/6] Quitar directorios, binarios y reglas auditd"
rm -rf /var/lib/hcma-agent /var/lib/hcma-signer /var/lib/hcma-monitor \
       /var/lib/hcma-proxy /var/lib/hcma-firecracker /var/lib/px-motor \
       /var/lib/hcma-lean /etc/hcma
# firecracker: si lo instaló apt lo quitamos; si fue binario de GitHub, lo borramos.
if command -v apt-get >/dev/null 2>&1; then
  apt-get remove -y firecracker 2>/dev/null || true
fi
rm -f /usr/bin/firecracker /usr/local/bin/px-motor /usr/local/bin/policygen
rm -f /etc/audit/rules.d/hcma.rules
echo "  directorios, binarios y reglas auditd eliminados"

paso "[6/6] Recargar auditd y verificar"
if command -v augenrules >/dev/null 2>&1; then
  augenrules --load 2>/dev/null || true
elif command -v auditctl >/dev/null 2>&1; then
  auditctl -D 2>/dev/null || true
fi
echo "  estado de servicios hcma:"
for s in hcma-monitor hcma-proxy hcma-dashboard hcma-firecracker hcma-px-motor hcma-audit-watcher; do
  st="$(systemctl is-active "$s.service" 2>/dev/null || echo inactivo)"
  printf '    %-22s %s\n' "$s" "$st"
done

echo ""
echo "================================================================================"
echo " Desinstalación completa."
echo ""
echo " Paquetes del gestor que podés remover a mano si no los usás para otra cosa:"
echo "   firecracker, golang-go (o golang), clang, bpftool, nftables, auditd,"
echo "   linux-headers-* (o kernel-devel), qemu-* (solo si usaste el test de VM en este host)"
echo "================================================================================"
