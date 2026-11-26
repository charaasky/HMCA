#!/bin/sh
# teardown.sh — apaga la VM del test de instalación y borra la imagen/seed (.vm/).
# Se corre en el HOST (donde ejecutaste run_vm_test.sh).
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> apagando la VM del test"
# El proceso qemu se lanzó con la imagen $HERE/.vm/ubuntu-24.04.img.
pkill -f "qemu-system-x86_64.*${HERE}/.vm/ubuntu-24.04.img" 2>/dev/null || true
# Fallback: matar cualquier qemu que use una imagen .vm de este árbol.
pkill -f "qemu-system-x86_64.*/\.vm/ubuntu-24.04.img" 2>/dev/null || true

sleep 1

echo "==> borrando imagen y seed (.vm/)"
rm -rf "$HERE/.vm"

echo "==> listo. VM apagada y ~40 GB liberados."
