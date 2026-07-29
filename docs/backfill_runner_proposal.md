# Propuesta de `backfill_runner.py`

## Objetivo
Orquestar el backfill histórico partido por partido con control de estado, reanudación segura y trazabilidad completa.

## Modos
- `--dry-run`: valida fixtures, equipos, orientación, fechas, duplicados y plan de trabajo sin escribir.
- `--persist`: ejecuta la ingesta real partido por partido.
- `--continue-on-error`: registra el error de un partido y continúa con el siguiente.
- `--limit N`: limita la cantidad de partidos procesados.
- `--league LEAGUE`: liga ESPN a procesar.
- `--season SEASON`: temporada a procesar.
- `--reconciliation-version VERSION`: versión de reglas aplicada.
- `--resume-from-run-id ID`: reanuda una corrida concreta si está pendiente, fallida o running expirado.

## Reanudación
- `resume-from-run-id` identifica una corrida concreta.
- Reutilizar solo si el estado es `pending`, `failed` o `running` expirado.
- Nunca duplicar una corrida `success`.
- Una corrida `running` activa debe rechazarse.

## Recuperación
- La tabla `ingestion_runs` conserva `heartbeat_at TIMESTAMPTZ`.
- El runner debe considerar expirado un `running` cuando `heartbeat_at` supere el timeout operativo definido en configuración.
- Solo una corrida expirada puede ser reclamada.
- Al reclamarla, registrar el cambio de propietario o de estado para trazabilidad.

## Flujo operativo
1. Obtener fixtures desde ESPN.
2. Normalizar candidatos a partido.
3. Resolver equipos por `teams.espn_team_id -> teams.id`.
4. Validar orientación:
   - home ESPN == `matches.home_team_id`
   - away ESPN == `matches.away_team_id`
5. Resolver fecha y competencia.
6. Resolver o crear `matches` sin duplicar y solo después de pasar la validación completa de identidad.
7. Registrar la corrida en `ingestion_runs` como `running`, con `heartbeat_at`.
8. Ejecutar la ingesta por partido en una transacción independiente.
9. Al finalizar, marcar `success`, `failed` o `skipped`.

## Reglas de control
- No usar nombres de equipos como identidad única.
- No continuar si falla la identidad del partido.
- Registrar el error y continuar solo si se especifica `--continue-on-error`.
- Error de partido: continuar solo con `--continue-on-error`.
- Error sistémico de conexión, esquema, autenticación o fuente: abortar siempre.
- Cada partido debe dejar un rastro en `ingestion_runs`.
- La reanudación debe filtrar por `status IN ('pending', 'failed', 'running')` según política operativa.
- La política de reclamación debe rechazar `running` activo y permitir solo `running` expirado.
- La identidad del partido debe validarse completa antes de cualquier escritura:
  - equipos ESPN;
  - orientación;
  - fecha;
  - competencia.
- Un fallo de identidad debe producir cero escrituras del partido.

## Códigos de error
- `identity_mismatch`
- `teams_mismatch`
- `orientation_mismatch`
- `match_exists`
- `run_active`
- `run_expired_reclaimed`
- `source_error`
- `schema_error`
- `connection_error`
- `authentication_error`
- `unknown_error`

## Auditoría de `var_annulled_events`
- No atribuirlo arbitrariamente al equipo si el evento no lo define con alta confianza.
- Si la atribución es incierta:
  - guardar a nivel de partido o dejar `NULL` por equipo;
  - conservar el detalle en ledger y/o JSON de auditoría;
  - no mezclar con métricas de equipo sin evidencia.
- Si la fuente indica claramente el equipo afectado, se puede atribuir a `team_id` interno.

## Salida esperada
- `fixtures_found`
- `fixtures_validated`
- `matches_created`
- `matches_reused`
- `runs_started`
- `runs_succeeded`
- `runs_failed`
- `runs_skipped`
- `rows_ledger`
- `rows_timeline`
- `rows_statistics`
