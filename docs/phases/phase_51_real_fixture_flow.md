# Fase 51 — flujo real de fixture futuro

## Objetivo

Comprobar el recorrido completo desde una solicitud de usuario hasta una
predicción pre-match real: ESPN resuelve el fixture, el servicio carga el
snapshot versionado y devuelve los mercados sin persistir la request.

## Ejecución verificada

- Liga: `mex.1`
- Fixture: Puebla vs Guadalajara
- Match ESPN: `401877027`
- Kickoff: `2026-08-01T01:00:00+00:00`
- Estado ESPN: `pre`
- HTTP: `200`
- Snapshot inicial: `phase38_multileague_v1_20260727`

Salida estructural observada:

- Local: `0.485864`
- Empate: `0.255023`
- Visitante: `0.259113`
- Over 2.5: `0.479827`
- BTTS: `0.511450`

## Gates

- `target_match_data_used`: `False`
- `cutoff_causal`: `True`
- `persistence`: `False`
- `markov_used`: `False`
- Snapshot versionado: `True`

## Advertencia operativa de la primera corrida

La primera ejecución fue correcta, pero el snapshot inicial terminaba en
diciembre de 2025 y el fixture era de agosto de 2026. El sistema lo expuso como
`history_freshness_warning`.

Fase 52 actualizó `mex.1` y repitió el flujo con el snapshot
`phase52_post2025_mex_v1_20260727`; la advertencia quedó en `False`, el corte
histórico llegó al 2026-07-18 y la antigüedad quedó en 13 días.

## Siguiente paso

Repetir la materialización para las demás ligas con partidos post-2025 antes de
tratar la frescura global como completa.
