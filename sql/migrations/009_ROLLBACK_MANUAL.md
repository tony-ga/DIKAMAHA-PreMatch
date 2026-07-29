# Rollback Manual - 009 relax team metadata

Este rollback es manual y no debe ejecutarse de forma destructiva automática.

## Qué cambia la migración

- `teams.altitude` pasa a permitir `NULL`.
- `teams.foundation_year` pasa a permitir `NULL`.
- No cambia `teams.id`.
- No cambia `teams.name`.
- No cambia `teams.espn_team_id`.
- No cambia ninguna FK.

## Cómo revertir manualmente

Solo si existe validación previa de que no hay filas con `NULL` en esas columnas:

1. Confirmar backup reciente.
2. Confirmar que `altitude` y `foundation_year` no tienen `NULL`.
3. Volver a imponer `NOT NULL`.
4. Restaurar el default histórico de `altitude` si se desea.

## Advertencia

- No rellenar `NULL` con `0`.
- No modificar `teams.id=1` ni `teams.id=2`.
- No fusionar ni borrar duplicados en este paso.
- Si existen `NULL`, el rollback debe ser tratado como procedimiento manual y revisado por un operador.
