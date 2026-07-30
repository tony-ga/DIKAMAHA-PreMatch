# Railway — bot premium Telegram

## Arquitectura

El bot premium se despliega como un servicio Railway distinto del servicio API
y avisador. No carga modelos ni snapshots: consulta la API DIKAMAHA por HTTPS,
aplica una allowlist de usuarios y usa el mismo presentador de tarjetas y
mercados del canal.

## Crear el servicio

1. En el proyecto Railway existente, crear un servicio desde el mismo
   repositorio GitHub.
2. En **Settings → Build**, seleccionar `Dockerfile.telegram-bot`.
   Alternativamente, configurar
   `RAILWAY_DOCKERFILE_PATH=Dockerfile.telegram-bot`.
3. No generar dominio público ni agregar volumen; este worker sólo realiza
   conexiones salientes.
4. Mantener exactamente una réplica. Dos réplicas con el mismo token compiten
   por `getUpdates`.

## Variables obligatorias

```text
TELEGRAM_BOT_TOKEN=<secreto del bot>
TELEGRAM_ALLOWED_USER_IDS=<id1,id2,id3>
DIKAMAHA_BOT_API_URL=https://dikamaha-prematch-production.up.railway.app
DIKAMAHA_API_KEY=<misma clave privada de la API>
```

Variables opcionales:

```text
TELEGRAM_POLL_TIMEOUT=25
TELEGRAM_REQUEST_TIMEOUT=15
TELEGRAM_RATE_LIMIT=10
TELEGRAM_RATE_WINDOW_SECONDS=60
LOG_LEVEL=INFO
```

No configurar `DIKAMAHA_BOT_API_URL` con `127.0.0.1`: la API vive en otro
servicio. El bot falla al arrancar si la URL no es HTTPS, la clave está vacía o
no hay usuarios autorizados.

## Alta y baja de usuarios

1. El usuario abre el bot y ejecuta `/whoami`.
2. El administrador añade ese número a `TELEGRAM_ALLOWED_USER_IDS`.
3. Railway reinicia el servicio al guardar la variable.
4. Para revocar acceso, retirar el ID y volver a desplegar.

Esta fase no automatiza pagos, vencimientos ni renovaciones. La allowlist es el
control operativo de membresías inicial.

## Verificación

En los logs debe aparecer:

```text
telegram_premium_started allowed_users=<cantidad>
```

Después, desde un usuario autorizado:

1. Abrir `/start`.
2. Entrar a **Próximos y predicciones**.
3. Seleccionar liga, fecha y partido.
4. Confirmar tarjeta de pronóstico y dashboard de mercados idénticos al canal.
5. Probar play-by-play, estadísticas y jugadores.

Un usuario no incluido debe ver `ACCESO PREMIUM REQUERIDO` y no debe provocar
llamadas de predicción.

## Operación segura

- No ejecutar otra instancia de long polling con el mismo token.
- El avisador puede compartir token porque sólo publica mensajes; no consume
  `getUpdates`.
- Rotar token o API key desde Railway ante cualquier exposición.
- Revisar reinicios, errores de Telegram y disponibilidad de la API en logs.
