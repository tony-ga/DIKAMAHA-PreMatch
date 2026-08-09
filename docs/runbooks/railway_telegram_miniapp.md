# Railway — Telegram Mini App y worker de alertas

## Servicios

La Fase 115 añade tres recursos al proyecto Railway sin sustituir el bot:

1. PostgreSQL administrado, compartido sólo por la Mini App y su worker.
2. `telegram-miniapp`, construido con `railway.miniapp.toml`.
3. `telegram-alert-worker`, construido con `railway.alert-worker.toml`.

Ambos servicios de aplicación usan `miniapp/Dockerfile`. La imagen corre como
`node`, la Mini App escucha en `$PORT` y el worker mantiene una sola réplica.
En Railway se debe asignar explícitamente el archivo de configuración de cada
servicio porque ninguno reemplaza el `railway.toml` de la API principal.

## Variables de la Mini App

```text
TELEGRAM_BOT_TOKEN=<secreto compartido con el bot>
TELEGRAM_ACCESS_MODE=private
TELEGRAM_ALLOWED_USER_IDS=<ids separados por coma>
DIKAMAHA_BOT_API_URL=https://<api-dikamaha>.up.railway.app
DIKAMAHA_API_KEY=<secreto de la API>
DATABASE_URL=${{Postgres.DATABASE_URL}}
MINIAPP_SESSION_SECRET=<32 bytes aleatorios o más>
MINIAPP_ENABLED=false
MINIAPP_ALERTS_ENABLED=false
```

Variables adicionales del worker:

```text
TELEGRAM_ALERT_POLL_SECONDS=30
```

No usar prefijos `NEXT_PUBLIC_` para tokens, claves, allowlists ni URLs
autenticadas. El navegador sólo llama rutas `/api/*` same-origin.

## Migración y despliegue gradual

1. Crear PostgreSQL y referenciar su `DATABASE_URL` en los dos servicios.
2. Desplegar `telegram-miniapp` con `MINIAPP_ENABLED=false`; el pre-deploy
   ejecuta `npm run db:migrate` de forma idempotente.
3. Confirmar `/api/health` con estado `ready` y revisar que el bundle no
   contenga `DIKAMAHA_API_KEY`, token o `initData`.
4. Desplegar el worker con `MINIAPP_ALERTS_ENABLED=false` y una réplica.
5. Cambiar `MINIAPP_ENABLED=true` y completar un smoke desde un usuario de la
   allowlist en Telegram Android, iOS o Desktop.
6. Configurar en BotFather el dominio HTTPS y el short name de la Mini App.
7. En el bot premium definir `DIKAMAHA_MINIAPP_URL`,
   `TELEGRAM_BOT_USERNAME` y `TELEGRAM_MINIAPP_SHORT_NAME`; reiniciar para que
   `setChatMenuButton` publique el acceso global.
8. Probar creación, edición, pausa y eliminación de una alerta. Sólo entonces
   cambiar `MINIAPP_ALERTS_ENABLED=true` en el worker.

## Smoke mínimo

- sesión válida crea cookie `HttpOnly`, `Secure`, `SameSite=Lax`;
- firma alterada, `auth_date` vencido, grupo o usuario fuera de allowlist
  reciben rechazo;
- `/live` refresca cada 25 segundos y separa Markov, residual Hawkes y
  combinado con rótulo `shadow`;
- fuera de allowlist Hawkes, la API reporta fallback Markov exacto;
- Cambridge United–Barnet conserva 1T, 2T y total en pre-match;
- worker consulta únicamente la API DIKAMAHA y registra una sola entrega por
  `subscription_id + event_key`;
- logs no incluyen tokens, `initData`, API keys ni payloads personales.

## Rollback

1. Cambiar `MINIAPP_ALERTS_ENABLED=false` para detener notificaciones.
2. Eliminar `DIKAMAHA_MINIAPP_URL` del bot y redesplegarlo para retirar el
   botón; los comandos y menús nativos siguen funcionando.
3. Cambiar `MINIAPP_ENABLED=false` o detener los dos servicios nuevos.
4. No revertir API, modelos, snapshots ni router oficial: Fase 115 sólo añade
   presentación, autenticación, preferencias y notificaciones.
5. Conservar PostgreSQL para auditoría. Su eliminación es una operación
   destructiva separada y no forma parte del rollback normal.
