# Telegram DIKAMAHA — inicio rápido

## Seguridad previa

El token nunca debe guardarse en código, documentación o artefactos. Si fue
compartido en un chat, debe regenerarse con BotFather antes de publicar el bot.

Copiar las variables de `.env.telegram.example` al `.env` local:

```dotenv
TELEGRAM_BOT_TOKEN=token_regenerado
TELEGRAM_ALLOWED_USER_IDS=
DIKAMAHA_BOT_API_URL=http://127.0.0.1:8000
```

## Primer arranque

Iniciar la API DIKAMAHA en una terminal:

```bash
DIKAMAHA_MODE=operational_readonly \
DIKAMAHA_EXTERNAL_CALLS_ENABLED=true \
uvicorn src.dikamaha_service:app --host 127.0.0.1 --port 8000
```

Iniciar el bot en otra terminal:

```bash
python scripts/run_phase_97_telegram_bot.py
```

Enviar `/whoami` al bot. Copiar el ID recibido a:

```dotenv
TELEGRAM_ALLOWED_USER_IDS=123456789
```

Reiniciar el bot. Mientras la allowlist esté vacía, `/whoami`, `/start` y
`/help` funcionan, pero `/premium` y `/mi_plan` permanecen bloqueados.

## Uso recomendado

Desde la Fase 125 el bot quedó reducido a cuenta y cobro: catálogo,
predicciones pre-match y en vivo, mercados, play-by-play, estadísticas,
plantillas, jugadores, favoritos, alertas e historial de aciertos viven
exclusivamente en la Mini App. `/start` y `/help` muestran el botón
`📊 Abrir DIKAMAHA` -un botón `web_app`, sin ida y vuelta al bot- para
abrirla; ahí está todo lo que antes navegaban los menús de botones del chat.

## Comandos

```text
/whoami
/start
/help
/premium
/mi_plan
```

`/whoami`, `/start` y `/help` no requieren membresía. `/premium` activa el
plan de pago -o muestra el enlace para hacerlo, o el estado si ya se tiene- y
`/mi_plan` consulta el plan vigente y, en el plan gratuito, cuántas
predicciones quedan hoy. Ver `docs/runbooks/telegram_stars_subscriptions.md`
para la operación del cobro con Telegram Stars.

## Alcance

- sólo chats privados autorizados;
- máximo diez solicitudes por usuario/minuto por defecto;
- suscripción mensual con Telegram Stars (Fase 125); sin stakes, ROI, Kelly ni
  ejecución de apuestas -ver la restricción de comunicación en
  `docs/phases/phase_125_star_subscription_tiers.md`-;
- long polling para pruebas internas;
- un despliegue público posterior debe usar token rotado, proceso supervisado
  y, si se migra a webhook, HTTPS y `secret_token`.
