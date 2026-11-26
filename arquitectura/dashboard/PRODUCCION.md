# Dashboard HCMA — puesta en producción

El dashboard ya quedó **read-only sobre el store** y el botón "Rehabilitar" **reenvía al monitor**
(único escritor). Falta solo la configuración de permisos del sistema (root) para que el dashboard
lea el store real y conecte al socket de gobernanza.

## Qué hace el código (ya listo)

- `memoria_sqlite.HCMAMemoriaSQLite(..., read_only=True)` abre el store con `mode=ro`; cualquier
  `write` falla con `OperationalError` (defensa en profundidad).
- `app.py`: `_memory()` abre en read-only; `POST /api/incidentes/rehabilitar` NO escribe el store:
  envía `revisar → parchear → rehabilitar` al monitor por socket (`_rehabilitar_via_monitor`).
- `hcma-dashboard.service` apunta a:
  - `HCMA_DASH_MEMORY_DB=/var/lib/hcma-monitor/store/memoria.db` (read-only)
  - `HCMA_MONITOR_SOCK=/run/hcma/monitor.sock` (acciones humanas)
  - `SupplementaryGroups=hcma-audit`

## Pasos root (una sola vez)

```bash
# 1. grupo de auditoría
sudo groupadd --system hcma-audit

# 2. el usuario del dashboard entra al grupo
sudo usermod -aG hcma-audit charasky

# 3. LECTURA del store del monitor (hoy 0700 del UID 999)
sudo chgrp -R hcma-audit /var/lib/hcma-monitor
sudo chmod 750 /var/lib/hcma-monitor /var/lib/hcma-monitor/store
sudo chmod 640 /var/lib/hcma-monitor/store/memoria.db

# 4. el socket de gobernanza nace accesible por hcma-audit (el monitor hace chown si puede);
#    para asegurarlo sin CAP_CHOWN, dar setgid al dir de runtime del monitor:
sudo chgrp hcma-audit /run/hcma 2>/dev/null || true
sudo chmod 2750 /run/hcma 2>/dev/null || true

# 5. recargar y reiniciar (monitor + dashboard)
sudo systemctl daemon-reload
sudo systemctl restart hcma-monitor hcma-dashboard
```

## Socket de gobernanza (IMPLEMENTADO)

El monitor ahora escucha DOS sockets en el MISMO event loop single-writer:

- `/run/hcma/monitor.sock` (agente): 0600, solo el lanzador; acepta cualquier op del agente.
- `/run/hcma/gobernanza.sock` (humano): grupo `hcma-audit` (0660) con **allowlist de ops**
  (`revisar_incidente`, `parchear_incidente`, `rehabilitar_incidente`, `incidentes_activos`).
  Cualquier otra op es denegada (fail-closed). El dashboard apunta a este socket.

El dashboard NO escribe el store: reenvía el flujo humano al socket de gobernanza, y el monitor
(único escritor) aplica las transiciones. Así el socket del agente queda intacto (nadie más que
el lanzador entra).

## Modo Firecracker (vsock): no afecta al dashboard

Si el monitor corre en modo vsock (`HCMA_VSOCK_UDS=/run/hcma/vsock.sock`, migración gVisor→Firecracker),
el socket del **agente** cambia de `/run/hcma/monitor.sock` a `/run/hcma/vsock.sock`, pero el socket de
**gobernanza** (`/run/hcma/gobernanza.sock`) NO cambia. El dashboard sigue apuntando a gobernanza.sock
(`HCMA_MONITOR_SOCK=/run/hcma/gobernanza.sock` en `hcma-dashboard.service`), así que la migración es
transparente para la observabilidad y el botón "Rehabilitar".

## Consolidación de pasos root

Los pasos root de esta página están consolidados en dos scripts idempotentes:
- `arquitectura/montaje/montar.sh` — montaje completo (UIDs, claves, despliegue, systemd, firewall,
  grupo hcma-audit + permisos del store).
- `arquitectura/firecracker/deploy.sh` — migración a Firecracker (firecracker, kernel, rootfs, reglas
  auditd, unidad systemd, grupo hcma-audit + permisos del store).

## Cómo verificar (sin root, en dev)

```bash
cd arquitectura/dashboard
HCMA_DASH_MEMORY_DB=../data/memoria.db \
HCMA_MONITOR_SOCK=/run/hcma/gobernanza.sock \
python3 app.py
# → http://localhost:8787
# GET  /api/incidentes            → activos + historia (read-only)
# POST /api/incidentes/rehabilitar → reenvía al monitor (o error si no hay socket)
```

## Autenticación y RBAC

El dashboard exige login para **toda** la API (la página `/` se sirve sin auth; el JS muestra el
login vía `/api/me`). Roles:

- `admin`   : todo, incluido el ABM de usuarios (consola «Usuarios»).
- `usuario` : ejecuta acciones operativas (rehabilitar, cablear, manifiesto).
- `visor`   : solo lectura. Audita; NO ejecuta acciones (403 en backend y botones ocultos).

Seguridad:
- Contraseñas PBKDF2-HMAC-SHA256 (200k iteraciones) + salt por usuario.
- 2FA TOTP (RFC 6238, HMAC-SHA1), **activado forzosamente en el primer login**.
- Sesiones en memoria (token HttpOnly, SameSite=Strict, Secure si TLS), expiración `HCMA_DASH_SESSION_TTL` (def. 12 h).
- Allowlist de IPs por usuario (`allowed_ips`, csv de IPs o CIDR; vacío = permitir todas).
- TLS con certificado autofirmado (`hcma-dashboard.crt`/`.key`, generados con openssl al arrancar).

Variables de entorno:
- `HCMA_DASH_AUTH_DB` (def. `auth.db`) — base de usuarios (no versionar).
- `HCMA_DASH_ADMIN_PASSWORD` — contraseña del admin inicial (si no se setea, se genera una y se imprime una sola vez).
- `HCMA_DASH_SESSION_TTL` — segundos de vida de la sesión (def. 43200 = 12 h).
- `HCMA_DASH_NO_TLS=1` — fuerza HTTP plano (solo desarrollo/pruebas).

Notas:
- El admin puede regenerar el acceso 2FA de un usuario vía **QR de visualización única** (consola).
- La IP se toma de `client_address` (sin confiar en `X-Forwarded-For`, que es falsable); si va detrás
  de un proxy, la allowlist verá la IP del proxy.
- El cert autofirmado produce aviso en el navegador (esperado); reemplazá `hcma-dashboard.crt`/`.key`
  por un certificado real si querés quitar el aviso.
- CSRF se mitiga con `SameSite=Strict` + cookie HttpOnly (baseline; para endurecer, agregar token CSRF).
