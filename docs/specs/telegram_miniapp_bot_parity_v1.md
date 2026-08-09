# Telegram Mini App — paridad funcional con el bot v1

## Objetivo

Exponer en la Mini App todas las capacidades de consulta del bot sin duplicar
ESPN, inferencia ni autenticación. El bot permanece como fallback y canal de
notificaciones.

## Matriz de paridad

| Bot Telegram | Mini App | API DIKAMAHA |
| --- | --- | --- |
| Inicio y ayuda | `/`, `/help` | contratos locales y sesión |
| Estado y `/whoami` | `/status`, `/settings` | `/v1/readiness`, sesión |
| Todos los próximos | `/upcoming` | `/v1/upcoming` |
| Próximos por liga/fecha | filtros en `/upcoming` | `/v1/explorer/leagues`, `/v1/explorer/dates`, `/v1/upcoming` |
| Predicción y mercados 1T/2T/total | `/predictions/[fixtureId]` | `/v1/predict/upcoming` |
| Contexto del partido | detalle pre-match e histórico | `/v1/explorer/fixture/context` |
| Partidos en vivo | `/live`, `/live/[fixtureId]` | `/v1/live`, `/v1/predict/live/fixture` |
| Modelos en operación | `/models` | `/v1/models` |
| Play-by-play clave/completo | Centro de partidos | `/v1/explorer/match/plays` |
| Estadísticas 1T/2T/total | Centro de partidos | `/v1/explorer/match/statistics` |
| Ligas, equipos y búsqueda | Centro de equipos | `/v1/explorer/leagues`, `/v1/explorer/teams` |
| Plantilla | detalle de equipo | `/v1/explorer/team/roster` |
| Perfil de jugador | detalle de jugador | `/v1/explorer/player` |

## Controles

- El navegador sólo consulta `/api/*` same-origin.
- El BFF sólo permite rutas explorer enumeradas; no acepta un path upstream
  arbitrario.
- Todo endpoint requiere sesión Telegram válida y rate limit por usuario.
- Los datos ESPN visibles permanecen `display_only` o `live_only` según su
  contrato; no se incorporan al modelo.
- No se exponen odds, ROI, Kelly, stakes ni recomendaciones de apuesta.
- Las vistas de Markov Live, Hawkes residual y combinado conservan `shadow`.

## Gate

- cada fila de la matriz tiene ruta, estado vacío/error y prueba de navegación;
- la suite verifica la allowlist BFF y ausencia de llamadas ESPN en cliente;
- un smoke de conexión comprueba readiness, modelos, ligas y catálogo próximo
  contra la API DIKAMAHA configurada, sin imprimir claves ni payload sensible.
