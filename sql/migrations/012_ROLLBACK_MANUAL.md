# Rollback manual — migración 012

Las columnas contienen identidad temporal prospectiva y no deben eliminarse
automáticamente.

Para rollback autorizado:

1. detener el colector de Fase 73;
2. exportar `scope_event_id`, `snapshot_bucket` y hashes;
3. retirar el índice `ix_raw_responses_snapshot_scope`;
4. eliminar columnas sólo después de confirmar que no existen snapshots únicos.
