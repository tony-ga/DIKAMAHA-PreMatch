# Rollback manual — migración 015

La operación es recuperable:

```sql
DROP INDEX IF EXISTS idx_high_probability_pick_freezes_kickoff_ts;
```

Eliminar el índice no modifica ninguna fila de `high_probability_pick_freezes`
-tabla append-only por diseño (DEC-182)-; sólo restaura el plan de consulta
anterior (seq scan) para `unsettled()`/`frozen_on_date()`.
