# Rollback manual — migración 011

La tabla `raw_responses` es aditiva y no reemplaza `raw_api_responses`.

Antes de cualquier rollback:

1. detener colectores de Fase 72–73;
2. exportar filas y hashes;
3. verificar que ningún parser nuevo referencia sus identificadores;
4. ejecutar manualmente `DROP TABLE raw_responses` sólo con autorización.

No se incluye un script automático porque eliminar snapshots prospectivos
destruiría evidencia temporal que no puede reconstruirse.
