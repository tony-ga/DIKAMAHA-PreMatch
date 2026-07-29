# Fase 50 — materialización de snapshot versionado

## Objetivo

Convertir el histórico validado en una versión inmutable que el servicio pueda
seleccionar por configuración, validar por hash y revertir sin sobrescribir el
snapshot fuente.

## Implementación

- `src/prematch_snapshot_registry.py` valida esquema, filas, partidos, ligas y
  SHA-256 antes de publicar.
- Cada versión vive en `artifacts/prematch_snapshots/<snapshot_id>/` con su
  `event_windows.json` y `manifest.json`.
- `active.json` es un puntero atómico con historial de activaciones.
- `scripts/manage_prematch_snapshot.py` permite `publish`, `activate`,
  `rollback` y `status`.
- `DIKAMAHA_PREMATCH_SNAPSHOT_ID` permite seleccionar una versión concreta;
  si no se define, el servicio usa el puntero activo.
- El servicio devuelve `snapshot_id`, fuente y bandera de versionado en
  `provenance`.

## Gate ejecutado

La versión `phase38_multileague_v1_20260727` quedó activa con:

- 111,528 filas;
- 9,294 partidos;
- 41 ligas;
- hash íntegro y manifiesto válido;
- snapshot fuente intacto;
- Markov sin promoción y evaluación no ejecutada.

## Rollback

La activación conserva historial. Para volver a la versión anterior:

```text
python scripts/manage_prematch_snapshot.py rollback
```

También se puede indicar una versión concreta con `--to`. El servicio debe
reiniciarse después de cambiar el puntero para cargar la versión seleccionada.

## Siguiente paso

Conectar el refresco de staging de Fase 49 a una nueva materialización sólo
cuando existan filas nuevas completas, y probar una consulta real de fixture
futuro con el snapshot seleccionado.
