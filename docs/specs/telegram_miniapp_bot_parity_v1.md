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

## Paridad dentro de un nivel (Fase 125)

La matriz de arriba dejó de ser incondicional cuando existen niveles de pago.
Ahora afirma que **para un mismo nivel**, bot y Mini App exponen lo mismo. La
frontera Free/Premium debe ser idéntica en las dos superficies:

| Capacidad | FREE | PREMIUM |
| --- | --- | --- |
| Aciertos del día e historial | ✅ | ✅ |
| Catálogo, ligas, equipos, plantillas, jugadores | ✅ | ✅ |
| Play-by-play y estadísticas | ✅ | ✅ |
| Estado, `/whoami`, modelos en operación | ✅ | ✅ |
| Predicción pre-match y mercados | 3 partidos al día | sin límite |
| Partidos en vivo y predicción live | ❌ | ✅ |
| Menú de mayor probabilidad | ❌ | ✅ |
| Favoritos / alertas | 10 / 20 | sin tope |

El punto donde las dos superficies podrían divergir —y donde divergir sería
directamente explotable— es el **contador diario**: si cada una llevara el suyo,
un usuario obtendría tres predicciones en el bot y otras tres en la Mini App. Por
eso el contador vive en PostgreSQL, no en memoria de proceso, y las tres
superficies (bot, Mini App y tarjeta compartida) lo indexan con la misma clave
`league_slug:match_id`. Un humano, un presupuesto.

El bot resuelve el nivel por HTTP contra la Mini App y **degrada a `free`** si no
obtiene respuesta. Esa degradación es visible en la respuesta: nunca puede
presentarse como "necesitas Premium", porque acusaría a un suscriptor de no haber
pagado por un fallo propio.

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
