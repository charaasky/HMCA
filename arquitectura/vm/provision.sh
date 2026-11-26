#!/usr/bin/env bash
# provision.sh — corre DENTRO de la VM virgen (Ubuntu 24.04). Clona HCMA y ejecuta
# la instalación de punta a punta, dejando un log y un marcador de fin.
#
# Se puede correr de dos formas:
#   1) Automático (cloud-init / run_vm_test.sh): se copia vía SSH y se ejecuta como `ubuntu`.
#   2) Manual:  ssh -p 2222 ubuntu@localhost 'bash -s' < provision.sh
#
# Uso directo:  bash provision.sh
set -euo pipefail

LOG="/var/log/hcma-install.log"
DONE="/home/ubuntu/hcma-test.done"

echo "=================================================================="
echo " HCMA VM TEST — instalación desde cero"
echo " fecha: $(date -Is)  ·  host: $(hostname)"
echo "=================================================================="

# Re-ejecutar con sudo si no somos root (camino realista: ubuntu + sudo => SUDO_USER=ubuntu).
if [ "$(id -u)" -ne 0 ]; then
  echo "==> re-ejecutando con sudo"
  exec sudo bash "$0"
fi

echo "==> actualizando índices apt"
apt-get update -y

echo "==> instalando git (para clonar HCMA)"
apt-get install -y git

echo "==> clonando HCMA (rama main) en /opt/hcma"
rm -rf /opt/hcma
git clone --depth 1 https://github.com/charaasky/HMCA.git /opt/hcma

echo "==> ejecutando instalador (preflight + pasos 1..6)"
cd /opt/hcma
bash arquitectura/instalar.sh

echo ""
echo "==> verificación de salud"
bash arquitectura/montaje/verificar.sh || true

echo ""
echo "==> estado de servicios"
for s in hcma-monitor hcma-proxy hcma-dashboard hcma-firecracker hcma-px-motor; do
  printf '  %-22s %s\n' "$s" "$(systemctl is-active "$s.service" 2>/dev/null || echo inactivo)"
done

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
echo "=================================================================="
echo " INSTALACIÓN COMPLETA"
echo "   Dashboard: https://${IP}:8787/"
echo "   Log:       $LOG"
echo "=================================================================="

touch "$DONE"
echo "marcador de fin: $DONE"
