# Fase 49 — resolvedor de fixtures y refresco de snapshot

## Objetivo

Permitir que el usuario solicite un partido próximo sin conocer los IDs internos
del sistema, resolviendo el fixture desde el scoreboard de ESPN y manteniendo
el histórico pre-match actualizado mediante una operación explícita.

## Entregables

- `src/espn_fixture_resolver.py` busca por ID, equipos o nombres normalizados,
  consulta una ventana UTC de tres fechas y exige un único fixture futuro.
- `POST /v1/predict/fixture` conecta la resolución con la vertical universal y
  devuelve la identidad resuelta junto con el baseline estructural y su auditoría.
- El endpoint sólo se habilita con `DIKAMAHA_MODE=operational_readonly` y
  `DIKAMAHA_EXTERNAL_CALLS_ENABLED=true`; no persiste resultados.
- `scripts/run_phase_49_snapshot_refresh.py` ejecuta el refresco R5 en dry-run
  por defecto. `--write-staging` autoriza exclusivamente `prospective_staging_v2`.

## Flujo

`usuario -> scoreboard ESPN -> fixture único futuro -> snapshot local -> baseline estructural -> respuesta`

El refresco histórico queda fuera de la request:

`operación explícita -> ESPN -> normalización -> staging prospectivo -> auditoría`

## Gates

- No hay play-by-play, marcador final ni estadísticas del partido objetivo en la
  predicción.
- La resolución rechaza ausencia, ambigüedad, fixture iniciado o fixture finalizado.
- El modo local sigue sin llamadas externas ni persistencia.
- El refresco no sustituye el snapshot canónico y no ejecuta evaluación ni entrenamiento.
- La escritura, cuando se autoriza, queda limitada al staging aislado y es verificable.

## Uso

Para una consulta local reproducible se mantiene `POST /v1/predict/upcoming`.
Para resolver ESPN, configurar el modo operativo de sólo lectura y enviar, por
ejemplo, `league_slug`, `kickoff_date` y los IDs o nombres de ambos equipos.

Para refrescar datos, ejecutar el script sin `--write-staging` para auditar la
fuente; añadir esa bandera sólo después de revisar el resultado y confirmar que
la conexión apunta al staging correcto.

## Estado y siguiente paso

Clasificación: `fixture_resolver_ready_snapshot_refresh_ready`.

La fase deja listo el camino operativo, pero no convierte automáticamente el
staging en el snapshot que consume la vertical. El siguiente paso es crear una
materialización versionada del snapshot, seleccionable por configuración y con
rollback, y probar el flujo completo contra una fixture real futura.
