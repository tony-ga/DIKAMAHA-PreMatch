# Fase 73 — snapshots pre-match multicutoff

## Estado

`active_collecting`

Primera ronda 2026-07-27:

- 5 fixtures en `mex.1`, `usa.1`, `bra.1`, `arg.1` y `col.1`;
- 60 snapshots contextuales raw-first;
- 36 filas `T-24h` y 24 filas `T-72h`;
- 100% anteriores al kickoff;
- replay posterior: 60/60 duplicados evitados antes de red;
- clasificación vigente: `insufficient_coverage` hasta obtener un segundo
  bucket por fixture.

## Objetivo

Capturar la disponibilidad real de contexto ESPN antes del kickoff en cortes
`T-168h`, `T-72h`, `T-24h`, `T-6h` y `T-90m`, utilizando exclusivamente el
contrato raw-first validado en Fase 72.

## Entradas

- `raw_responses` y migración 011.
- `EspnRawFirstProvider`.
- fixtures próximos resueltos por scoreboard.
- plan `docs/plan_markov_prematch_v4.md`.

## Alcance

- planificar cortes por fixture y liga;
- capturar scoreboard, roster, injuries, schedule, standings, odds y officials;
- conservar `fetched_at`, `cutoff_ts`, `kickoff_ts`, hash y estado de cobertura;
- registrar campos ausentes sin imputación retrospectiva;
- reintentar fallos externos con backoff sin duplicar semánticamente un cutoff.

## Fuera de alcance

- parsear cuotas `current`, `close` o live como features;
- entrenar o evaluar Markov;
- usar datos recuperados después del kickoff;
- modificar el router oficial.

## Gate de salida

- 100% de snapshots utilizables cumplen `fetched_at < kickoff_ts`;
- cobertura publicada por liga, recurso y bucket de cutoff;
- al menos dos snapshots pre-match por fixture cuando ESPN responda;
- cero timestamps fabricados desde caché;
- ledger reproducible por hash;
- cohortes insuficientes permanecen `active_collecting` o
  `insufficient_coverage`.

## Artefactos requeridos

`config.json`, `input_manifest.json`, `coverage.json`, `audit.json`,
`metrics.json`, `validation_report.md`, `final_report.md` y `hashes.json`.

## Siguiente paso permitido

La captura puede continuar mientras Fase 74 trabaja únicamente con información
histórica causal. Los campos snapshot-only no entran a entrenamiento hasta que
este gate cierre y exista cobertura prospectiva suficiente.
