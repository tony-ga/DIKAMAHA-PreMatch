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
`/help` funcionan, pero las predicciones permanecen bloqueadas.

## Uso recomendado

Después de `/start`, pulsa `📅 Próximos y predicciones`. El bot permite:

- `🌍 Todos los próximos`: agrega las 18 ligas y ordena por kickoff;
- `🏆 Buscar por liga`: abre el selector de competición;
- `📅 Buscar por fecha`: muestra ocho fechas futuras seleccionables.

Después se selecciona el partido y `🔮 Ver predicción`. No es necesario
conocer IDs, ligas ni nombres de equipos. `✅ Estado del servicio` comprueba
la disponibilidad local.

El menú principal también permite:

- navegar liga→fecha→partido para play-by-play;
- consultar estadísticas separadas en 1T, 2T y total;
- navegar liga→equipo→jugador para perfiles y acumulados;
- buscar equipos enviando una parte del nombre después de pulsar
  `🔎 Buscar equipo`;
- abrir mercados de predicción en submenús de 1T, 2T y total.

Telegram no ofrece autocompletado dinámico dentro de un chat normal. La
alternativa implementada devuelve coincidencias como botones después de
enviar dos o más caracteres.

Todas las pantallas comparten el mismo formato visual:

- encabezado e icono para identificar el módulo;
- contexto del partido o jugador debajo del encabezado;
- tablas monoespaciadas para probabilidades y comparaciones;
- tarjetas compactas para eventos play-by-play;
- botones para periodo, página o siguiente nivel;
- mensajes largos divididos y descripciones recortadas con elipsis.

## Comandos de respaldo

```text
/estado
/buscar_equipo mex.1 Cruz A
/partido esp.1 20300110 Barcelona | Real Madrid
/predict esp.1 94 86 2030-01-10T20:00:00+00:00
```

`/partido` resuelve nombres mediante ESPN. `/predict` utiliza IDs ESPN
explícitos. Ambos delegan en la API DIKAMAHA y muestran baseline oficial más
mercados shadow experimentales.

## Alcance

- sólo chats privados autorizados;
- máximo diez solicitudes por usuario/minuto por defecto;
- sin cuotas, stakes, ROI, Kelly, pagos o ejecución de apuestas;
- long polling para pruebas internas;
- un despliegue público posterior debe usar token rotado, proceso supervisado
  y, si se migra a webhook, HTTPS y `secret_token`.
