# Rollback manual — migración 013

La operación es recuperable:

```sql
DROP INDEX IF EXISTS idx_events_timeline_causal_order;
```

Eliminar el índice no modifica eventos; sólo restaura el plan anterior.
