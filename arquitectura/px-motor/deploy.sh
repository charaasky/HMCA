#!/bin/sh
# deploy.sh — despliegue de px-motor (detector eBPF de escape) con política firmada.
# SPDX-License-Identifier: Apache-2.0
#
# Idempotente. Hace:
#   1) build de px-motor + policygen desde la dependencia motor-go (github.com/charaasky/motor-go)
#   2) clave de política (separada de vendor.key; root-only) — copia la existente o genera una nueva
#   3) política runtime firmada (serial monótono, cgroup/UID/exe del sujeto, anti-rollback)
#   4) instalación del binario + política + unidad systemd
#
# El SUJETO se define por variables de entorno (idénticas a generar_policy.py):
#   PXMOTOR_CGROUP (default system.slice/hcma-firecracker.service)
#   PXMOTOR_UID   (default 995)   PXMOTOR_EXE (default firecracker)   PXMOTOR_ALLOW (default python3)
#   PXMOTOR_MODE  (default alert) PXMOTOR_OUTPUT (default /var/lib/px-motor/alerts.jsonl)
#
# Uso:  sudo sh deploy.sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Re-ejecutando con sudo (necesito root)..."
  exec sudo sh "$0" "$@"
fi

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
MOTOR_REPO="${MOTOR_REPO:-https://github.com/charaasky/motor-go.git}"
PXMOTOR_SRC="${PXMOTOR_SRC:-/var/lib/px-motor/src}"
INSTALL_BIN="/usr/local/bin/px-motor"
POLICYGEN_BIN="/usr/local/bin/policygen"
STATE_DIR="/var/lib/px-motor"
POLICY_KEY="${PXMOTOR_POLICY_KEY:-${STATE_DIR}/policy.key}"
LEGACY_KEY="/home/charasky/.keys/px-motor/policy.key"

paso() { echo ""; echo "=== $1 ==="; }

# Resuelve el placeholder __HCMA_REPO__ con la ruta real del repo al instalar una unidad
# systemd (portabilidad: los .service no hardcodean /home/charasky).
instalar_servicio() {
  sed -e "s|__HCMA_REPO__|${REPO}|g" -e "s|__HCMA_DASH_USER__|${DASH_USER:-}|g" "$1" > "$2"
  chmod 0644 "$2"
  echo "  ok: $2"
}


# ---- 1. build ----
paso "[1/5] Build px-motor + policygen (dependencia: motor-go)"
echo "  dependencia: ${MOTOR_REPO}"

# Herramientas de build requeridas (motor-go compila sondas eBPF + binario Go).
for tool in git go clang bpftool; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "  ❌ falta '$tool' (requerido para compilar motor-go)." >&2
    echo "     instala: git, Go, clang, bpftool y los kernel headers de tu distro (${DISTRO_ID:-linux})" >&2
    exit 1
  fi
done

# Clonar la dependencia motor-go si no está (o apuntar PXMOTOR_SRC a un checkout local).
if [ ! -d "${PXMOTOR_SRC}/.git" ]; then
  echo "  clonando ${MOTOR_REPO} → ${PXMOTOR_SRC} ..."
  git clone --depth 1 "${MOTOR_REPO}" "${PXMOTOR_SRC}"
fi

# Compilar desde fuente: vmlinux.h + sondas .bpf.c → .bpf.o, luego el binario Go (embebe los .o).
if [ ! -f "${PXMOTOR_SRC}/vmlinux.h" ]; then
  echo "  generando vmlinux.h (bpftool btf dump) ..."
  bpftool btf dump file /sys/kernel/btf/vmlinux format c > "${PXMOTOR_SRC}/vmlinux.h"
fi
(cd "${PXMOTOR_SRC}" && \
  clang -O2 -g -target bpf -D__TARGET_ARCH_x86 -c probe.bpf.c -o probe.bpf.o && \
  clang -O2 -g -target bpf -D__TARGET_ARCH_x86 -c net.bpf.c   -o net.bpf.o   && \
  clang -O2 -g -target bpf -D__TARGET_ARCH_x86 -c lsm.bpf.c   -o lsm.bpf.o   && \
  CGO_ENABLED=0 go build -o /tmp/px-motor . && \
  CGO_ENABLED=0 go build -o /tmp/policygen ./policygen)
install -m 0755 /tmp/px-motor     "${INSTALL_BIN}"
install -m 0755 /tmp/policygen    "${POLICYGEN_BIN}"
echo "  ok: ${INSTALL_BIN} y ${POLICYGEN_BIN} instalados"

# ---- 2. clave de política ----
paso "[2/5] Clave de política (separada de vendor.key)"
install -d -m 0700 "${STATE_DIR}"
if [ -f "${POLICY_KEY}" ]; then
  echo "  ok: clave de política ya existe en ${POLICY_KEY}"
elif [ -f "${LEGACY_KEY}" ]; then
  install -m 0600 "${LEGACY_KEY}" "${POLICY_KEY}"
  echo "  ok: clave copiada desde ${LEGACY_KEY} → ${POLICY_KEY}"
else
  echo "  no hay clave de política: genero un par nuevo y reconstruyo el binario con la pubkey."
  PUB="$("${POLICYGEN_BIN}" -keygen -priv "${POLICY_KEY}" 2>/dev/null | sed -n 's/.*policyPubKey): \([0-9a-f]\{64\}\).*/\1/p')"
  if [ -z "${PUB}" ]; then
    echo "  ❌ no pude generar/parsear la clave de política" >&2
    exit 1
  fi
  # La pubkey es el ancla de confianza hardcodeada; la rotamos en el fuente y recompilamos.
  sed -i "s/^const policyPubKey = \"[0-9a-f]\{64\}\"/const policyPubKey = \"${PUB}\"/" \
    "${PXMOTOR_SRC}/policy.go"
  (cd "${PXMOTOR_SRC}" && CGO_ENABLED=0 go build -o /tmp/px-motor .)
  install -m 0755 /tmp/px-motor "${INSTALL_BIN}"
  echo "  ok: par nuevo generado (pubkey ${PUB}), binario reconstruido"
fi
chmod 600 "${POLICY_KEY}"

# ---- 3. política firmada ----
paso "[3/5] Política runtime firmada (serial monótono + anti-rollback)"
python3 "${REPO}/arquitectura/px-motor/generar_policy.py" > "${STATE_DIR}/policy.unsigned.json"
"${POLICYGEN_BIN}" -sign -priv "${POLICY_KEY}" \
  -in  "${STATE_DIR}/policy.unsigned.json" \
  -out "${STATE_DIR}/policy.json"
chmod 0644 "${STATE_DIR}/policy.json"
echo "  ok: política firmada en ${STATE_DIR}/policy.json"
python3 -c "import json; p=json.load(open('${STATE_DIR}/policy.json')); print('     serial=%s cgroup=%s uid=%s exe=%s allow=%s modo=%s' % (p['serial'], p['subject']['cgroup'], p['subject']['uid'], p['subject']['exe'], ','.join(p['subject'].get('allow', [])), p['rules']['mode']))"

# ---- 4. unidad systemd ----
paso "[4/5] Unidad systemd hcma-px-motor.service"
instalar_servicio "${REPO}/arquitectura/px-motor/hcma-px-motor.service" \
  /etc/systemd/system/hcma-px-motor.service
systemctl daemon-reload
systemctl enable hcma-px-motor.service >/dev/null 2>&1 || true
echo "  ok: unidad instalada y habilitada (arranca junto a hcma-firecracker)"

# ---- 5. smoke check de la política (dry-run: valida firma/selectores/anti-rollback sin eBPF) ----
paso "[5/5] Smoke check de la política (dry-run, sin arrancar eBPF)"
if "${INSTALL_BIN}" -policy-check -policy "${STATE_DIR}/policy.json" -policy-state "${STATE_DIR}/policy.serial" >/dev/null 2>&1; then
  echo "  ok: la política verifica (firma + serial + selectores)."
else
  echo "  ❌ la política NO verificó (fail-closed). Log:" >&2
  "${INSTALL_BIN}" -policy-check -policy "${STATE_DIR}/policy.json" -policy-state "${STATE_DIR}/policy.serial" 2>&1 | tail -3 >&2
  exit 1
fi

echo ""
echo "================================================================================"
echo " px-motor desplegado. Para arrancar ahora (requiere hcma-firecracker corriendo):"
echo "   sudo systemctl start hcma-firecracker.service   # si no está corriendo"
echo "   sudo systemctl start hcma-px-motor.service"
echo " Alertas: tail -f /var/lib/px-motor/alerts.jsonl"
echo "================================================================================"
