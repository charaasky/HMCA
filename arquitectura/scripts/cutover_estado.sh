#!/bin/sh
# cutover_estado.sh — Fase 2 (paso 3): corta el estado al monitor (inodo nuevo + chown).
#
# Dictamen de Fable (fable_fase2_revocacion_fable.md, pasos 6-7):
#   "Copiar el estado a un inodo nuevo, en un directorio creado para 103 con 0700. No hagas
#    chown in situ: la copia mata de un golpe descriptores abiertos, ACLs y hard links."
#   "Verificar el digest de la copia contra el checkpoint y retirar el original."
#
# Este script NO se auto-ejecuta: imprime los comandos que el humano corre con sudo. Usa
# --aplicar para ejecutarlos (requiere sudo con password del humano).
#
# Requisitos previos (ya cumplidos o a verificar):
#   - usuario hcma-monitor creado, home /var/lib/hcma-monitor en 0700.
#   - store SQLite con WAL consolidado (sin -wal/-shm sueltos).
#   - checkpoint calculado (checkpoint_cutover.py) y firmado (firmar_checkpoint.py).
set -u

MONITOR_USER="hcma-monitor"
MONITOR_HOME="/var/lib/${MONITOR_USER}"
AUDIT_GROUP="hcma-audit"
STORE_SRC="/home/charasky/Documentos/HMC/arquitectura/data/memoria.db"
STORE_DST="${MONITOR_HOME}/store/memoria.db"
NONCE_DST="${MONITOR_HOME}/nonces.log"

emitir() {
  echo "================================================================================"
  echo " CUTOVER del estado al monitor (inodo nuevo + chown)"
  echo "================================================================================"
  echo ""
  echo "# 1) Crear dir del store del monitor (0750, dueño monitor, grupo hcma-audit):"
  echo "sudo install -d -m 0750 -o ${MONITOR_USER} -g ${AUDIT_GROUP} ${MONITOR_HOME}/store"
  echo ""
  echo "# 2) Eliminar WAL/SHM VIEJOS del destino (un -wal ajeno pisa el .db nuevo al abrir)."
  echo "#    NUNCA correr wal_checkpoint acá: escribiría las páginas viejas dentro del .db bueno."
  echo "sudo rm -f ${STORE_DST}-wal ${STORE_DST}-shm"
  echo ""
  echo "# 3) Copiar el store a inodo NUEVO (NO chown in situ; 0640 para lectura del dashboard):"
  echo "sudo install -m 0640 -o ${MONITOR_USER} -g ${AUDIT_GROUP} ${STORE_SRC} ${STORE_DST}"
  echo ""
  echo "# 3) Verificar digest de la copia == digest del checkpoint:"
  echo "sha256sum ${STORE_DST} ${STORE_SRC}"
  echo ""
  echo "# 4) Configurar log de nonces vacío (append-only, dueño monitor):"
  echo "sudo install -m 0600 -o ${MONITOR_USER} -g ${MONITOR_USER} /dev/null ${NONCE_DST}"
  echo "sudo install -m 0600 -o ${MONITOR_USER} -g ${MONITOR_USER} /dev/null ${NONCE_DST}.head"
  echo ""
  echo "# 5) Retirar el original (tras verificar digest):"
  echo "sudo rm ${STORE_SRC}"
  echo ""
  echo "# 6) Verificar que el AGENTE no puede escribir el nuevo store:"
  echo "sudo -u hcma-agent sh -c 'echo x > ${STORE_DST}' 2>&1 | head -1"
  echo "   # debe dar 'Permission denied'"
  echo "================================================================================"
}

aplicar() {
  echo "Aplicando cutover (requiere sudo con password)..."
  set -e
  sudo install -d -m 0750 -o "${MONITOR_USER}" -g "${AUDIT_GROUP}" "${MONITOR_HOME}/store"
  # Eliminar WAL/SHM viejos del destino ANTES de copiar. Un -wal ajeno al .db nuevo lo pisa
  # al abrir (SQLite aplica el WAL si magic/salts/checksums son válidos, aunque sea de otra época).
  # NUNCA correr wal_checkpoint: escribiría las páginas viejas dentro del .db bueno.
  sudo rm -f "${STORE_DST}-wal" "${STORE_DST}-shm"
  sudo install -m 0640 -o "${MONITOR_USER}" -g "${AUDIT_GROUP}" "${STORE_SRC}" "${STORE_DST}"
  echo "copiado a ${STORE_DST}"
  # verificar digest (como root, porque el store es 0600 del monitor)
  SRC_SUM="$(sha256sum "${STORE_SRC}" | cut -d' ' -f1)"
  DST_SUM="$(sudo sha256sum "${STORE_DST}" | cut -d' ' -f1)"
  if [ "${SRC_SUM}" != "${DST_SUM}" ]; then
    echo "ERROR: digest de la copia no coincide con el original" >&2
    exit 1
  fi
  echo "digest verificado: ${DST_SUM}"
  # log de nonces vacío
  sudo install -m 0600 -o "${MONITOR_USER}" -g "${MONITOR_USER}" /dev/null "${NONCE_DST}"
  sudo install -m 0600 -o "${MONITOR_USER}" -g "${MONITOR_USER}" /dev/null "${NONCE_DST}.head"
  echo "log de nonces inicializado en ${NONCE_DST}"
  # NO retiramos el original automáticamente: lo dejamos para que el humano verifique y
  # lo retire manualmente (paso 5), para no borrar datos de forma irreversible sin inspección.
  echo "NOTA: el original ${STORE_SRC} NO fue eliminado. Retiralo manualmente tras verificar:"
  echo "  sudo rm ${STORE_SRC}"
  echo "done."
}

case "${1:-}" in
  --aplicar)
    aplicar
    ;;
  *)
    emitir
    ;;
esac
