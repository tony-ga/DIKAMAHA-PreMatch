# Rollback manual para `007_create_ingestion_runs.sql`

Este rollback es manual y no destructivo por defecto.

## Cambios introducidos
- Creación de la tabla `ingestion_runs`.
- Creación de índices de apoyo.
- Adición de restricciones de unicidad, FK y CHECK.
- Adición de `heartbeat_at` para control de expiración y reanudación.

## Consideraciones
- La tabla solo almacena control de corridas; no contiene datos de negocio.
- Si existen corridas registradas, decidir primero si se preservan para auditoría.
- No ejecutar `DROP TABLE` automáticamente sin confirmación explícita.

## Secuencia recomendada de reversión manual
1. Revisar dependencias de aplicación y reportes sobre `ingestion_runs`.
2. Exportar la tabla si se desea conservar trazabilidad.
3. Deshabilitar primero el uso del runner en producción o lotes automáticos.
4. Si la eliminación es aceptada explícitamente, ejecutar manualmente:
   - `DROP TABLE ingestion_runs;`

## Reanudación y recuperación
- `resume-from-run-id` identifica una corrida concreta.
- Solo puede reutilizarse si el estado es `pending`, `failed` o `running` expirado.
- Una corrida `success` nunca se duplica ni se reutiliza.
- Una corrida `running` activa debe rechazarse.
- La recuperación de una corrida `running` requiere verificar que `heartbeat_at` esté vencido según la política del runner.
- Al reclamarla, el runner debe registrar el cambio de propietario o el cambio de estado en la trazabilidad operativa.

## Identidad del partido
- No crear `matches` hasta que estén resueltos:
  - equipos ESPN;
  - orientación;
  - fecha;
  - competencia.
- Cualquier fallo de identidad debe producir cero escrituras del partido.

## Errores
- Error de partido: solo continuar con `--continue-on-error`.
- Error sistémico de conexión, esquema, autenticación o fuente: abortar siempre.
- Los códigos de error deben persistirse en `error_code` y acompañarse de `error_message`.

## VAR
- Atribuir por equipo solo cuando exista alta confianza.
- En caso contrario, guardar a nivel partido o `NULL`.
- Nunca asignar arbitrariamente a un equipo.

## `ON DELETE RESTRICT`
- Si existen corridas asociadas, la eliminación de un `match` debe seguir un procedimiento manual explícito con respaldo previo.

## Reversibilidad
- Reversible en términos de esquema.
- No reversible en términos de trazabilidad si se eliminan filas sin respaldo.
