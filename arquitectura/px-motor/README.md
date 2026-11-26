# px-motor — detector eBPF de escape de la microVM

`px-motor` es el detector host-side que vigila el **sujeto** (el VMM Firecracker) y convierte los
intentos de escape en **señales observables**. Es la tercera capa de la arquitectura (contención),
complementaria al monitor/proxy (gobernanza) y a la microVM (boundary de hardware).

Vive en otro repo (`github.com/charaasky/motor-go`); acá está el
**despliegue** que lo integra a HCMA.

## Qué vigila y cómo

| Mecanismo | Detalle |
|---|---|
| **Política firmada** | Ed25519 con separación de dominio (`px-motor/policy/v1`). Pin el sujeto por **cgroup** (heredado, no spoofeable) + UID + exe (+`allow` para el lanzador). **Fail-closed**: firma inválida / vencida / fingerprint ajeno / sin selector → no arranca. |
| **Invariantes de escape** | watcher cada 2s sobre el cgroup: `ESCAPE_UID` (UID distinto al firmado), `ESCAPE_EXEC` (binario fuera de `{exe}∪{allow}`), `ESCAPE_CGROUP_MIGRATION` (sujeto que sale del cgroup vivo). Siempre se emiten (bypass del allowlist de reglas). |
| **Anti-rollback** | persiste el mayor `serial` aceptado (root-only, escritura atómica) y rechaza políticas con serial menor. |

El sujeto por defecto es la microVM Firecracker: `system.slice/hcma-firecracker.service`, UID 995
(`hcma-firecracker`), binario `firecracker`, con el lanzador `python3` (`lanzar.py`) allowlisteado.

## Despliegue

```bash
sudo sh arquitectura/px-motor/deploy.sh   # desde la raíz del repo
```

O, todo junto, desde el orquestador:

```bash
sudo sh arquitectura/instalar.sh
```

`deploy.sh` es **idempotente**: compila `px-motor` + `policygen`, ubica (o genera) la clave de
política, firma una política con `serial` monótono y `not_after`, instala el binario en
`/usr/local/bin/px-motor`, la política en `/var/lib/px-motor/` y la unidad
`hcma-px-motor.service`.

## Configuración (variables de entorno)

| Variable | Default | Qué es |
|---|---|---|
| `PXMOTOR_SRC` | `/var/lib/px-motor/src` | repo fuente del motor (clonado de github.com/charaasky/motor-go) |
| `PXMOTOR_CGROUP` | `system.slice/hcma-firecracker.service` | cgroup del sujeto |
| `PXMOTOR_UID` | `995` | UID del sujeto |
| `PXMOTOR_EXE` | `firecracker` | binario del sujeto |
| `PXMOTOR_ALLOW` | `python3` | binarios auxiliares permitidos (coma-separados) |
| `PXMOTOR_MODE` | `alert` | `alert` (solo registrar) o `kill` (SIGKILL al ofensor) |
| `PXMOTOR_OUTPUT` | `/var/lib/px-motor/alerts.jsonl` | JSONL de alertas |
| `PXMOTOR_VALID_DAYS` | `365` | días de validez de `not_after` |
| `PXMOTOR_PIN_FINGERPRINT` | `1` | atar la política a ESTA máquina |

## Clave de política (importante)

La clave privada de política es **distinta** de `vendor.key` (licencia ≠ política). La **pública**
va embebida como ancla de confianza en el binario (`policyPubKey`). La privada:

- en esta máquina: `/var/lib/px-motor/policy.key` (600 root, copiada desde `~/.keys/px-motor/policy.key`).
- en producción: debe vivir **offline** (nunca en el host que corre px-motor).

Si `deploy.sh` no encuentra la clave, genera un par nuevo **y rota la `policyPubKey` del fuente +
recompila** (por eso el ancla queda consistente). Eso es un evento de rotación de confianza: hacelo
a propósito.

## Operación

```bash
systemctl status hcma-px-motor.service
journalctl -u hcma-px-motor -n 50 -f          # arranque + fail-closed + invariantes
tail -f /var/lib/px-motor/alerts.jsonl        # alertas ESCAPE_*
```

## Honestidad

px-motor hace los escapes **observables y atribuibles** (y terminables en `kill`). **No** demuestra
que la microVM sea inescapable: ninguna frontera de software lo es. La afirmación honesta es
"boundary de hardware + detección de escape host-side", no "100%".
