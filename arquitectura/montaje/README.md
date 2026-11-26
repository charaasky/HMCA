# Montaje de la arquitectura HCMA

Carpeta autocontenida para **montar la arquitectura completa** de forma reproducible, con un único
orquestador y una verificación de salud.

> **Instalación completa de punta a punta (core + Firecracker + px-motor):** `sudo sh ../instalar.sh`.
> Este `montar.sh` cubre solo el core HCMA (monitor/proxy/dashboard/firma). La microVM de hardware
> se despliega con `../firecracker/deploy.sh` y el detector eBPF con `../px-motor/deploy.sh`.

## Qué hay acá

| Archivo | Qué hace |
|---|---|
| `montar.sh` | Orquestador end-to-end (idempotente): UIDs → claves+pin → despliegue → systemd → firewall → verificación. |
| `verificar.sh` | Verificación de salud post-montaje (sale 0 si todo OK). |
| `README.md` | Este runbook. |

Los scripts canónicos que orquesta viven en `../scripts/` (deploy de libs, firewall, firma, etc.) y
los `.service` en `../scripts/` y `../dashboard/`. **No se duplican**: `montar.sh` los invoca por
ruta (fuente única de verdad).

## Cómo montar

```bash
cd arquitectura/montaje   # desde la raíz del repo
sudo sh montar.sh
```

`montar.sh` se re-ejecuta con `sudo` si no es root. Es **idempotente**: se puede correr de nuevo sin
romper nada (salta lo que ya existe).

## Qué hace cada fase

1. **Frontera de UIDs** — crea `hcma-agent` (101), `hcma-signer` (102), `hcma-monitor` (999),
   `hcma-proxy` (997) y el grupo `hcma-red` (1001); agrega `hcma-proxy` y `hcma-monitor` al grupo.
   Los UIDs son **explícitos** para que coincidan con lo que hardcodean el firewall (997) y el
   servicio del proxy (999).
2. **Claves + pin** — genera el par Ed25519 (la privada queda en `/var/lib/hcma-signer/`, 600) y
   fija el ancla de confianza `/etc/hcma/publica.pin` (root-owned).
3. **Despliegue** — copia los módulos del monitor a `/var/lib/hcma-monitor/lib/` y los del proxy a
   `/var/lib/hcma-proxy/lib/`.
4. **systemd** — instala y habilita `hcma-monitor`, `hcma-proxy` y `hcma-dashboard`.
5. **Firewall** — nftables: default-deny **solo** para el UID 997 (proxy), host intacto.
   ⚠️ Nunca `policy drop` en hook output (corta internet a toda la máquina); ver
   `../scripts/firewall_hcma_proxy.sh`.
6. **Manifiesto + verificación** — congela el sistema (`manifiesto_sistema.py`) y corre
   `verificar.sh`.

## Paso manual (no lo hace `montar.sh`): ceremonia de corte de estado

El corte del estado (checkpoint firmado + copia del store al monitor) es una **ceremonia humana**
de una sola vez, no automatizable de forma segura. En una máquina **nueva** hay que hacerla a mano:

1. `python3 ../scripts/checkpoint_cutover.py` — calcula el checkpoint del store.
2. `python3 ../scripts/firmar_checkpoint.py` — lo firma el humano (fuera de línea).
3. `sudo sh ../scripts/cutover_estado.sh --aplicar` — copia el store a
   `/var/lib/hcma-monitor/store/memoria.db` (inodo nuevo, digest verificado) y crea el log de nonces.
4. Verificar el digest y retirar el original manualmente.

`montar.sh` **verifica** que el store exista (fase 6) pero **no** lo crea destructivamente. En esta
máquina el corte ya está hecho (store presente, digest `aa60f3…` alineado con el checkpoint).

## Verificación rápida

```bash
sudo sh verificar.sh          # salud completa
curl -skS https://localhost:8787/api/components   # dashboard (más legible; TLS autofirmado)

# Auditoría PUNTUAL de reconciliación (Opción A): cruza ejecuciones (proxy 997)
# contra autorizaciones consumidas (nonces del monitor 999). Ambos append-only, UIDs distintos.
sudo python3 arquitectura/scripts/auditar_reconciliacion.py   # desde la raíz del repo
```

## Prerrequisitos del host

- Linux con `systemd`, `nftables`, `python3` (≥3.11) y `useradd/groupadd`.
- `runsc` (gVisor) instalado (`runsc --version`).
- `.deps` en la raíz del repo (`.deps`, PYTHONPATH para los scripts de firma).
- Bundles gVisor construidos en `../gvisor/` (el manifiesto los congela por hash).
