# Fase 53 — refresco multi-liga post-2025

## Objetivo

Ampliar el refresco que inicialmente cubría `mex.1` a los slugs documentados,
manteniendo el snapshot activo intacto durante el descubrimiento y la prueba.

## Resultado ejecutado

- 42 ligas documentadas procesadas.
- 101 referencias ESPN descubiertas entre `2026-07-20` y `2026-07-27`.
- 24 referencias seleccionadas, con límite de tres por liga.
- 6 partidos completos materializados.
- 72 ventanas nuevas de 15 minutos.
- 18 referencias excluidas: 17 por `window_score_mismatch` y una por
  `match_not_complete`.
- 113,616 filas finales en el snapshot combinado.
- PostgreSQL no fue escrito; los payloads crudos no se incorporaron al
  snapshot.

## Gate y activación

La primera ejecución fue `dry-run`: produjo `merged_event_windows.json` y
`audit.json` sin escribir el registro. Tras pasar la reconciliación, se publicó
y activó `phase53_multileague_post2025_v1_20260727`. El puntero conserva
rollback a `phase52_post2025_mex_v1_20260727` y al snapshot multi-liga anterior.

La ejecución ampliada queda disponible con `--max-matches-per-league` y un
rango de fechas mayor. `--activate` es obligatorio para cambiar el snapshot;
sin esa bandera el script es sólo lectura respecto al registro.

## Verificación

Se repitió el flujo real Puebla–Guadalajara después de la activación:

- resolución ESPN: aprobada;
- servicio `/v1/predict/fixture`: HTTP 200;
- leakage temporal: cero;
- persistencia durante la predicción: cero;
- clasificación: `real_fixture_flow_verified`.

La fase actualiza cobertura operativa, no promueve Markov ni modifica el
router oficial. La evaluación OOS independiente sigue siendo una fase aparte.

