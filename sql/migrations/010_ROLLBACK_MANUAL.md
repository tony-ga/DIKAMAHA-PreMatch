# Rollback Manual - 010 add reconciliation quality

Este rollback es manual y no debe ejecutarse automáticamente como operación destructiva.

## Qué cambia la migración

- Agrega `source_confidence` a `match_statistics`.
- Agrega `reconciliation_confidence` a `match_statistics`.
- Agrega `needs_review` a `match_statistics`.
- Agrega `conflict_details` a `match_statistics`.
- Agrega `reconciliation_status` a `match_statistics`.
- No elimina ni sobrescribe `confidence`.
- No recalcula filas históricas.

## Política de versiones

- `v1` conserva el modelo antiguo.
- `v2` usa los nuevos campos de calidad.
- Las nuevas ingestas deben usar `reconciliation_version = v2` cuando persistan estos campos.
- `v1` queda disponible solo para lectura y trazabilidad.

## Cómo revertir manualmente

Solo si existe respaldo y validación de que no se perderá trazabilidad crítica:

1. Revisar si hay filas `v2` persistidas.
2. Confirmar que no hay dependencias externas sobre los nuevos campos.
3. Eliminar o deshabilitar el uso de `v2` en el loader antes de tocar el esquema.
4. Retirar manualmente las columnas nuevas si el operador decide hacerlo.

## Advertencia

- No hacer rollback automático en producción.
- No eliminar filas de `match_statistics`.
- No modificar `confidence`.
- No recalcular históricos como parte del rollback.
