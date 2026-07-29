# Fase 75 — baseline temporal fuerte y targets direccionales

## Estado

`ready_for_next_phase`

## Resultado

- 9,444 partidos y 56,664 intervalos direccionales;
- soporte: 35,899 `neither`, 10,709 `home_only`, 8,161 `away_only` y
  1,895 `both`;
- el modelo tabular con `C=0.05` y temperatura `1.0` fue seleccionado
  exclusivamente en `selection`;
- log-loss confirmatorio tabular `0.992701` frente a `1.003269` analítico;
- Brier confirmatorio `0.540081` y ECE `0.007085`;
- replay doble con hashes idénticos y diferencia métrica máxima `0.0`;
- targets, features de inferencia y predicciones publicados por separado.

## Entradas

- `artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl`;
- particiones temporales inmutables de Fase 74;
- `directional_interval_targets_v1`;
- DEC-078.

## Entregables

- labels direccionales separados de features;
- perfiles rolling pre-match de equipo/liga;
- baseline analítico jerárquico;
- clasificador tabular multinomial same-data;
- calibración y selección exclusivamente en `selection`;
- métricas por partido en `fit`, `selection` y `confirmation`.

## Gate

- probabilidades normalizadas y calibradas;
- cero eventos del partido objetivo en features;
- cero `match_id` compartidos entre particiones;
- reproducción de métricas dentro de `1e-6`;
- modelo definitivo seleccionado sólo con `selection`;
- router y entrenamiento Markov sin cambios.

## Artefactos

`config.json`, `input_manifest.json`, `coverage.json`, `audit.json`,
`metrics.json`, `predictions.jsonl`, `inference_features.jsonl`,
`targets.jsonl`, `validation_report.md`, `final_report.md` y `hashes.json`.
