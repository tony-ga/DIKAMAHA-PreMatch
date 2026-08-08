# Especificación `live_event_stream_v1`

## Objetivo

Representar una observación in-play de ESPN de forma raw-first, causal y
reproducible. El contrato es exclusivo de inferencia live y settlement; nunca
puede alimentar una predicción pre-match del mismo partido.

## Fuentes permitidas

- Site scoreboard para descubrir fixtures y estado.
- Core event/competition para identidad y estado.
- Core plays paginado para eventos.
- Core situation como contexto live auxiliar.
- Site summary como fallback cuando Core plays está vacío.

El transporte conserva `site.api.espn.com` como primario. Si Akamai responde
403, puede repetir una sola vez el mismo path y parámetros en
`site.web.api.espn.com`; esto cubre `/apis/site/v2` y `/apis/v2` (standings).
Ambos son hosts ESPN permitidos y la URL efectiva se guarda en raw provenance.
Core no cambia de host. CDN queda excluida mientras responda 202 sin cuerpo
JSON verificable.

`probabilities` sólo puede usarse como benchmark externo. `odds` permanece
`financial_isolated`. La CDN no es dependencia de Fase 114.

## Snapshot canónico

Cada snapshot conserva `league_slug`, IDs de evento/competición/equipos,
kickoff, `source_fetched_at`, estado, periodo, reloj monotónico, marcador,
endpoint, parámetros, hash raw, secuencia de polling y versión del parser.

Cada evento conserva ID ESPN o clave semántica, tipo raw y canónico, equipo,
periodo, `match_clock_seconds`, instante observado, anulación y hash raw.
Eventos `auxiliary`, `unclassified`, anulados, duplicados o sin equipo se
retienen en auditoría y no se reinterpretan silenciosamente.

## Reglas temporales

- Sólo se modelan eventos con reloj confiable y no posterior al snapshot.
- La edad del evento usa tiempo de partido; `source_fetched_at` mide latencia.
- La captura live no reutiliza el cache TTL pre-match.
- Un snapshot stale, con reloj regresivo o marcador inválido falla cerrado.
- Replay usa exclusivamente payloads raw previamente persistidos.

El reloj usa el máximo coherente entre `clock.value`, `clock.displayValue` y
`displayClock`. Formatos de descuento como `90'+8'` se convierten a 5,880
segundos; Markov conserva una cola live móvil de un minuto cuando el partido
sigue activo más allá del tiempo reglamentario, sin tratarlo como prórroga.

Version: 1.2.0
Created: 2026-08-07; updated: 2026-08-08
