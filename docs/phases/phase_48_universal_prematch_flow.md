# Fase 48 — flujo universal pre-match

## Objetivo

Reducir la solicitud de predicción a liga, equipos y kickoff, sin exigir que el
usuario construya manualmente las features internas.

## Flujo implementado

`request compacta -> snapshot histórico -> cutoff causal -> baseline estructural -> mercados`

Nuevo endpoint:

`POST /v1/predict/upcoming`

Ejemplo:

```json
{
  "league_slug": "esp.1",
  "home_team_id": 94,
  "away_team_id": 86,
  "kickoff_ts": "2030-01-10T20:00:00+00:00",
  "match_id": 990001
}
```

La respuesta entrega 1X2, Over 2.5, BTTS, goles esperados, lambdas,
provenance, antigüedad del histórico y auditoría de cutoff.

## Límites explícitos

- La vertical usa el snapshot local de `event_windows v1`; todavía no resuelve
  automáticamente nombres de equipos ni refresca ESPN dentro de la request.
- El modelo es `structural_poisson_baseline`; Markov multi-liga no está
  promovido ni se usa en esta salida.
- Si no hay al menos ocho partidos históricos de la liga, se rechaza la
  solicitud en lugar de inventar una predicción.
- El histórico puede devolver `history_freshness_warning` si está desactualizado.

## Gate

Clasificación: `universal_prematch_vertical_ready` para solicitudes por IDs
contra el snapshot local. No es todavía producción externa.

Artefactos:

- `artifacts/phase_48_universal_prematch_flow_v1/final_report.md`
- `artifacts/phase_48_universal_prematch_flow_v1/prediction.json`
- `artifacts/phase_48_universal_prematch_flow_v1/audit.json`

## Siguiente fase

Implementar el resolver de fixtures ESPN y el refresco seguro del snapshot
antes del kickoff; después conectar ese resolver al mismo endpoint sin cambiar
la lógica matemática ni habilitar Markov experimental.

