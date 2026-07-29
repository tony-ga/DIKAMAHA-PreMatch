# Fase 97 — interfaz shadow de Telegram

## Objetivo

Permitir pruebas privadas del sistema pre-match desde Telegram reutilizando la
API universal existente y sin convertir mercados experimentales en apuestas.

## Comandos

- `/start` y `/help`: contrato y advertencias;
- `/whoami`: muestra el ID necesario para la allowlist;
- `/partido <liga> <YYYYMMDD> <local> | <visitante>`;
- `/predict <liga> <home_id> <away_id> <kickoff_iso> [match_id]`;
- `/estado`: comprueba readiness del servicio DIKAMAHA.

La ruta principal es el teclado inline: `Próximos partidos` consulta el
catálogo ESPN de las próximas 96 horas, crea botones por encuentro y permite
obtener la predicción con una sola pulsación adicional. El catálogo usa
`DIKAMAHA_UPCOMING_LEAGUES` o, por defecto, `esp.1,eng.1,mex.1`.

## Controles

- token exclusivamente en `TELEGRAM_BOT_TOKEN`;
- usuarios permitidos en `TELEGRAM_ALLOWED_USER_IDS`;
- sólo chats privados;
- long polling con offset `update_id + 1`;
- timeout, retry exponencial y rate limit por usuario;
- mensajes escapados y divididos bajo el límite Telegram;
- ningún payload o secreto sensible en logs;
- sin ROI, stakes, Kelly, ejecución de apuestas o pagos.

## Gate

- comandos y errores cubiertos con transporte falso;
- una actualización se procesa una sola vez;
- payload Telegram → DIKAMAHA exacto;
- salida identifica baseline y mercados shadow;
- servicio de inferencia y router permanecen idénticos;
- catálogo próximo y callbacks de menú cubiertos;
- replay determinista y suite integral aprobada.

## Resultado

Clasificación: `ready_for_next_phase`.

- seis comandos implementados;
- búsqueda por nombres y predicción avanzada por IDs;
- nueve mercados renderizados en el smoke E2E;
- allowlist, chat privado, rate limit, retry y offset verificados;
- token ausente de fuentes, logs y artefactos;
- replay completo con hashes idénticos;
- router y salida oficial sin cambios;
- suite integral con PostgreSQL: 410 pruebas aprobadas;
- falta únicamente configurar un token rotado y el ID autorizado para realizar
  el primer smoke real contra Telegram.

La expansión de datos, periodos y navegación se versiona por separado en
Fase 98 para conservar este gate.
