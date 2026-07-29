# Rollback Manual - 008 seed teams

Este archivo es documentación manual. No debe ejecutarse como script destructivo.

## Alcance

- La propuesta `008_seed_teams_esp_1_2024_25.sql` no inserta filas automáticamente.
- Si en una iteración futura se decide convertir esta propuesta en inserción real,
  el rollback debe hacerse con respaldo previo y aprobación explícita.

## Procedimiento manual recomendado

1. Confirmar que exista backup reciente.
2. Identificar filas insertadas por `espn_team_id`.
3. Revertir solo las filas aprobadas manualmente.
4. Verificar que `teams.id=1` y `teams.id=2` permanezcan canónicos.
5. No eliminar ni fusionar `teams.id=3..6` sin un plan separado de migración de FKs.

## Notas de seguridad

- No usar `DELETE` automático como rollback general.
- No tocar `matches`, `events_ledger`, `events_timeline` ni `match_statistics`.
- Toda eliminación futura debe ser quirúrgica y respaldada.
