# Railway — piloto de usuarios

## Unidad desplegable

El `Dockerfile` ejecuta un único supervisor. La API escucha en
`0.0.0.0:$PORT` y el worker Telegram se conecta internamente a esa API. Railway
comprueba `/v1/readiness`.

## Variables obligatorias

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_ID`
- `TELEGRAM_CHANNEL_MODE=lite|full`
- `DIKAMAHA_API_KEY`: secreto aleatorio de al menos 32 bytes
- `DIKAMAHA_AUTH_ENABLED=true`

Variables recomendadas:

- `TELEGRAM_CHANNEL_POLL_SECONDS=300`
- `TELEGRAM_CHANNEL_LEDGER_PATH=/data/telegram_channel.sqlite`
- `DIKAMAHA_MAX_CONCURRENT_REQUESTS=16`
- `DIKAMAHA_RATE_LIMIT_REQUESTS=600`
- `DIKAMAHA_RATE_LIMIT_WINDOW_SECONDS=60`
- `DIKAMAHA_INFERENCE_TIMEOUT_SECONDS=30`
- `LOG_LEVEL=INFO`

No definir `DIKAMAHA_BOT_API_URL`: el supervisor usa automáticamente el
puerto asignado por Railway.

## Persistencia

Crear un volumen Railway y montarlo en `/data`. Sin volumen, el ledger se
perderá al recrear el contenedor y podrían repetirse publicaciones ya enviadas.

## Health y operación

- liveness: `GET /v1/health`;
- readiness: `GET /v1/readiness`;
- métricas: `GET /v1/metrics` con `X-Dikamaha-Key`;
- logs: stdout JSON, consultables desde Railway.

Los logs no contienen cuerpos, tokens, API keys ni cadenas de conexión.

## Despliegue

1. Conectar el repositorio a Railway.
2. Crear el volumen `/data`.
3. cargar las variables obligatorias.
4. desplegar mediante el `Dockerfile`.
5. comprobar readiness y un ciclo `--dry-run` antes de permitir mensajes.
6. iniciar en `TELEGRAM_CHANNEL_MODE=lite`.

## Rollback

Reactivar la última imagen saludable desde Railway. El volumen `/data` debe
conservarse: no eliminar ni reemplazar el ledger durante el rollback.

## Límites iniciales

La primera versión utiliza un solo proceso y un ledger SQLite persistente.
No se debe escalar horizontalmente a más de una réplica mientras el ledger no
migre a una base compartida con locks distribuidos.
