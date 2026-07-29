# Fase 80V — test interpretable de 100 partidos

## Objetivo

Generar un reporte partido por partido del mejor candidato Markov actual y
compararlo con resultados y estadísticas ya conocidos.

## Selección

Los 100 partidos más recientes de `confirmation`, ordenados por
`match_date, match_id`. La selección ocurre antes de puntuar.

## Modelos

- Markov no homogéneo 80U;
- continuo same-data;
- baseline tabular.

## Ranking

El reporte se ordena por log-loss medio de trayectoria 80U ascendente. También
publica delta contra comparadores, ventanas acertadas, secuencia prevista,
marcador y estadísticas reales.

Es una auditoría descriptiva sobre una cohorte ya clausurada. No cuenta como
confirmación nueva ni modifica el router.

## Resultado

- Clasificación: `validated` como diagnóstico, no promocionable.
- Cobertura: 100 partidos únicos, 100 outcomes, 13 ligas.
- Periodo: 2026-04-11 a 2026-07-27.
- Log-loss medio: 80U `0.954427`, continuo same-data `0.953792`, baseline
  `0.958411`.
- 80U superó al continuo en 55/100 partidos y al baseline en 59/100, pero
  perdió frente al continuo por `0.000635` en la media.
- Media de ventanas acertadas: `3.97/6`.
- Accuracy descriptiva: `first_half_goal 61%`,
  `second_half_goal 70%`.
- Replay idéntico por hash y cero campos internos del scoring publicados.
- Regresión integral con PostgreSQL: `362 passed`.

## Evidencia

`artifacts/phase_80v_100_match_prediction_test/final_report.md` contiene los
100 partidos ordenados por NLL 80U ascendente. JSON y CSV conservan
probabilidades por ventana, comparadores, marcadores y estadísticas reales.
