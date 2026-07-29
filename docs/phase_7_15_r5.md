# Fase 7.15-R5

La ejecución por rango consulta cada fecha ESPN y fusiona partidos/eventos en
`prospective_staging_v2`. La persistencia requiere ambas banderas explícitas:

```bash
python scripts/run_phase_7_15_espn_connector.py \
  --enable-source-fetch --start-date 20260714 --end-date 20260717 \
  --enable-staging-write --refresh-incomplete --max-concurrency 1 \
  --sleep-between-requests 0.5 --stop-on-error
```

Para inspección sin cambios usar `--dry-run` (no combinar con
`--enable-staging-write`). `--date YYYYMMDD` equivale a un rango de una fecha.
Los partidos históricos y `704766` se registran como excluidos. El refresco de
scheduled/live/incomplete está acotado y se detiene en completed o en el límite
de intentos; no se ejecuta evaluación estadística.
