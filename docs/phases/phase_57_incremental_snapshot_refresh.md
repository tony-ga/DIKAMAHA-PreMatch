# Fase 57 — refresco incremental

## Resultado

Se implementó una operación reutilizable para revisar los últimos siete días
de las 42 ligas documentadas. El `dry-run` encontró 101 referencias, seleccionó
69, materializó 7 partidos completos y 84 ventanas candidatas. Tras deduplicar
contra el snapshot activo, el resultado neto fue de 12 filas nuevas. Se
excluyeron 62 referencias por discrepancia de marcador o partido incompleto.

La versión publicada es:

`phase57_incremental_v1_20260727`

La activación conservó el snapshot anterior para rollback y después se repitió
la solicitud universal Puebla–Guadalajara con HTTP 200 y cutoff causal.

## Operación futura

El script es `scripts/run_phase_57_incremental_snapshot_refresh.py`. Por
defecto ejecuta `dry-run`; para publicar una ventana validada se debe añadir
`--activate`. El rango se controla con `--lookback-days` y `--end-date`, y el
límite por liga con `--max-matches-per-league`.

