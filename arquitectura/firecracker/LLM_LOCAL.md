# LLM dentro de la microVM (boundary local)

Correr el **modelo adentro** de la microVM Firecracker, en vez de llamar a un API externo. El LLM
queda **contenido** en la frontera de hardware: su runtime vive en el guest (ring −1), igual que el
agente, y `px-motor` sigue vigilando al sujeto en el host.

> Esto **no** reemplaza la verificación del work product (`verificacion_trabajo.py`): el modelo
> corre adentro (bien), pero su *salida* sigue necesitando gobernanza y verificación determinista.

## Lo que ya está cableado

El gateway (`dashboard/llm_gateway.py`) ya distingue proveedor **local** vs **externo**:

- `_provider_is_local(base)` → `localhost`/`127.0.0.1`/`.local`/`.internal` es local.
- `_scheme_allowed(url)` → permite `http` **solo** para loopback/local.
- `_egress_facts(...)` → con proveedor local, **todos** los facts (incluido `secret`) se quedan
  locales; con externo se filtran.

Se agregó un bloque de config de primera clase en `dashboard/llm_config.py`:

```json
"local_llm": { "enabled": false, "port": 8080, "model": "local" }
```

Con `enabled=true`, `_conn()` resuelve al endpoint `http://127.0.0.1:<port>/v1` (proveedor `local`),
sin API key, y fuerza el `model` local. El **circuito** del gateway queda: `cliente → gateway → 127.0.0.1:<port> → LLM en el guest`.

## Requisitos (honestos)

- **CPU-only.** Firecracker no hace passthrough de GPU; la inferencia es en CPU. Un modelo 7B Q4
  necesita ~4–5 GB de RAM residente; sumá margen para KV-cache y el sistema.
- La microVM por defecto es **512 MiB / 1 vCPU** (config.json) — para un LLM hay que subir
  `mem_size_mib` y `vcpu_count` (p. ej. 8192 MiB / 4 vCPU para un 7B).
- **rootfs grande** para los pesos (varios GB) o, mejor, montar los pesos como un **segundo block
  device read-only** (`drives` en `config.json`) y no dentro del rootfs.

## Cómo construirlo

1. Extendé `build_rootfs_firecracker.sh` (o hacé un rootfs aparte) para instalar un runtime
   OpenAI-compatible: `llama.cpp` (`server`) u `ollama`.
2. Los pesos: agregá un segundo `drives` en `config.json` (read-only) apuntando a un `.ext4` con el
   modelo, y montá-lo en el `guest_init`.
3. `guest_init` levanta el server en un puerto TCP (p. ej. `8080` para llama.cpp, `11434` para
   ollama) y lo deja corriendo.

## Transporte (cómo exponer el puerto al host)

### Opción A — tap device (estándar, cualquier server HTTP)

Agregá una `network-interfaces` a `config.json` con un `tap` (p. ej. `tap0`), levantá un bridge/NAT
en el host, y el guest obtiene IP. El host llega al LLM por `http://<ip-guésped>:<port>` (o un
port-forward a loopback).

- **Tradeoff honesto:** reintroduce una ruta de red a la microVM (antes era `network-interfaces: []`).
  Esa superficie es **solo** el puerto del LLM, y sigue cubierta por `px-motor` + el gateway local.
  El claim "sin red" de la microVM se relaja a propósito.

### Opción B — vsock (mantiene la microVM sin red)

Firecracker ya usa vsock para el agente. Si el runtime LLM habla vsock nativamente (raro) o ponés un
**shim vsock→TCP** en el guest (escucha en vsock y reenvía al server TCP local), el host lo alcanza
por un forwarder vsock→loopback. Más trabajo, pero conserva "sin red".

## Configurar desde el dashboard

Vía `llm_config.save()` (que ya persiste el bloque):

```python
llm_config.save({"local_llm": {"enabled": True, "port": 8080, "model": "mi-modelo-7b"}})
```

O equivalentemente con `provider`/`base_url`/`model` en el panel (el gateway lo trata igual si el
host es loopback). Con `enabled`, el gateway **no pide API key** y fuerza el `model` local.

## Límites honestos

- **CPU-only** → latencia y throughput acotados por la máquina; no es un reemplazo de GPU.
- El modelo sigue siendo **un binario que podés verificar** con `verificacion_trabajo.py` (la salida
  no se auto-certifica).
- "LLM adentro" **reduce** la dependencia del vendor externo, pero **no** elimina el problema de
  gobernanza de la salida (el modelo puede alucinar o desviarse igual).

Ver también: `README.md` (boundary), `../scripts/verificacion_trabajo.md` (verificación del work product).
