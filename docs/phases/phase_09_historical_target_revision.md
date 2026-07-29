# Fase 09 — extensión histórica y targets temporales v2

## Objetivo

Auditar una extensión histórica completa y congelar targets temporales con
mejor cobertura para una futura evaluación pre-match. La fase no entrena ni
promueve Markov.

## Entrada aprobada

- `artifacts/phase_01_event_windows_v1/event_windows.json`.
- Tablas `prospective_staging_v2.matches` y `prospective_staging_v2.events`,
  leídas exclusivamente con `SELECT`.
- `docs/specs/temporal_targets_v2.md`.

## Artefactos obligatorios

- `config.json`
- `input_manifest.json`
- `coverage.json`
- `metrics.json`
- `audit.json`
- `validation_report.md`
- `final_report.md`
- `hashes.json`

## Gates

1. Los 44 partidos candidatos tienen identidad, marcador y estado completo.
2. Se generan 12 ventanas por partido, sin huérfanos ni relojes inválidos.
3. Las ventanas reproducen el marcador final canónico.
4. La conexión PostgreSQL es sólo lectura y sus conteos antes/después son
   idénticos.
5. La cohorte candidata queda fuera de los folds OOS históricos congelados.
6. Ningún target se usa como feature ni habilita promoción automática.

## Clasificaciones permitidas

- `validated_for_target_revision`: auditoría técnica aprobada, sin promoción.
- `blocked_by_data`: la fuente no tiene cobertura suficiente o no puede
  auditarse.
- `rejected_for_revision`: falla de integridad o fuga.

## Siguiente paso permitido

Si todos los gates pasan, preparar una partición temporal nueva y una
evaluación confirmatoria de `temporal_targets v2`; no reutilizar la confirmación
de Fase 07.

# Version: 1.0.0
# Created: 2026-07-26
