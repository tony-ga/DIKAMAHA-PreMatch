# Rollback manual para `006_create_match_statistics.sql`

Este rollback es manual y no debe ejecutarse automáticamente.

## Cambio introducido

- creación de `match_statistics`
- creación de restricciones, índices y comentarios asociados

## Rollback permitido

Sólo si existe confirmación explícita y respaldo previo:

1. Revisar que no existan dependencias funcionales sobre `match_statistics`.
2. Validar que ninguna ingesta o proceso la esté usando.
3. Si se decide revertir, hacerlo manualmente con una migración de reversión explícita.

## Cambio no reversible automáticamente

- cualquier dato ya almacenado en `match_statistics`

## Nota

No se debe incluir `DROP` en scripts ejecutables de rutina.

