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

Producción creada el 2026-08-08:

```text
Postgres service: 0276da42-ffdd-48d2-bc7f-bd6ae7fd37e7
telegram-miniapp: dbd1077b-a34e-4eaf-9385-50ec633aefa7
telegram-alert-worker: 6d6d036b-109c-46d8-98a5-0e8b0f8bdbcb
Mini App URL: https://telegram-miniapp-production-cbab.up.railway.app
```

El acceso Mini App está activo en modo privado. El worker está desplegado con
alertas desactivadas hasta completar el smoke interactivo y de deduplicación.

## Alta y baja de usuarios

El acceso lo decide la fila del usuario en `miniapp_users`, no la
configuración del servicio. **Dar de alta a alguien ya no requiere
redesplegar**: era el bloqueo real para admitir usuarios nuevos.

Cualquiera que abra la Mini App queda registrado en estado `pending` y ve un
mensaje pidiéndole que solicite la aprobación. Para activarlo:

```sql
UPDATE miniapp_users
SET status = 'active', approved_at = now(), approved_by = <tu_telegram_user_id>
WHERE telegram_user_id = <id_del_usuario>;
```

Para revocar el acceso, `status = 'blocked'`. Los tres estados válidos son
`pending`, `active` y `blocked`.

Cola de solicitudes pendientes:

```sql
SELECT telegram_user_id, username, first_name, first_seen_at
FROM miniapp_users WHERE status = 'pending' ORDER BY first_seen_at;
```

Consideraciones:

- El cambio surte efecto en el **siguiente inicio de sesión** del usuario, no
  al instante: el rol y el plan viajan dentro de la cookie firmada para no
  consultar PostgreSQL en cada petición. Bloquear a alguien con sesión abierta
  le corta el acceso cuando su cookie caduque o se reemita. Para un corte
  inmediato hay que rotar `MINIAPP_SESSION_SECRET`, lo que invalida **todas**
  las sesiones.
- `TELEGRAM_ALLOWED_USER_IDS` sigue existiendo, pero **sólo como semilla de
  administradores**: quien esté en esa lista entra siempre y se marca como
  `admin` en la tabla. Es la salvaguarda para que un error de datos no deje el
  sistema sin nadie capaz de aprobar a nadie. No añadir usuarios normales ahí.
- Con `TELEGRAM_ACCESS_MODE=public` toda alta nueva nace `active` y no hay
  nada que aprobar.

## Variables de la Mini App

```text
TELEGRAM_BOT_TOKEN=<secreto compartido con el bot>
TELEGRAM_ACCESS_MODE=private
# Sólo administradores: el acceso normal se concede en la tabla miniapp_users.
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
   botón. Desde la Fase 125 el bot no tiene menús nativos propios -catálogo,
   predicciones, en vivo y demás sólo viven en la Mini App-, así que sin este
   botón el bot queda reducido a `/whoami`, `/start`, `/help`, `/premium` y
   `/mi_plan`.
3. Cambiar `MINIAPP_ENABLED=false` o detener los dos servicios nuevos.
4. No revertir API, modelos, snapshots ni router oficial: Fase 115 sólo añade
   presentación, autenticación, preferencias y notificaciones.
5. Conservar PostgreSQL para auditoría. Su eliminación es una operación
   destructiva separada y no forma parte del rollback normal.

## Análisis interno del historial de aciertos

Lo que la Mini App muestra en "Aciertos" -incluidas las gráficas de la Fase
126- no es un cálculo hecho al vuelo: se lee directamente de
`prediction_settlements`, la tabla **append-only** que `TelegramChannelPublisher`
llena vía `add_if_absent()` (`src/settlement_store.py`) sobre el mismo Postgres
que usa la Mini App. Nunca sobrescribe una fila ya liquidada, así que no hace
falta ninguna persistencia adicional para "no perder" esta información: ya
sobrevive a redeploys y está disponible para análisis fuera de la app en
cualquier momento.

Para un análisis que la interfaz no cubre -por ejemplo, evolución mensual, o
cruzar con los artefactos de calibración de Fase 122-, consulta la tabla
directamente:

```sql
SELECT fixture_key, league_slug, kickoff_ts, home_team_name, away_team_name,
       score_home, score_away, official_verdicts, shadow_verdicts, settled_at
  FROM prediction_settlements
 ORDER BY kickoff_ts DESC
 LIMIT 500;
```

`official_verdicts` y `shadow_verdicts` son JSONB: en Postgres,
`official_verdicts->'one_x_two'->>'hit'` extrae el acierto de un mercado sin
tener que traer la fila completa a un cliente. Los picks del menú "Mayor
probabilidad" (Fase 123) viven aparte, en la tabla que gestiona
`src/high_probability_settlement.py` (`SqlAlchemyHighProbabilityPickRepository`).
