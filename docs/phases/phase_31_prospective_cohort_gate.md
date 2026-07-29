# Fase 31 — gate de cohorte prospectiva

## Objetivo

Filtrar automáticamente el staging prospectivo para dejar sólo partidos
completos, posteriores al cutoff y ausentes de todo ajuste, calibración,
confirmación y router oficial.

## Operación

```bash
python scripts/run_phase_31_prospective_cohort_gate.py
```

El proceso usa exclusivamente `SELECT`, conserva los conteos antes/después y
publica candidatos sin calcular métricas, pérdidas, bootstrap o calibración.

## Gate

- Estado `cohort_ready_for_confirmatory_evaluation` con al menos 30 candidatos.
- Estado `waiting_for_new_independent_cohort` mientras no exista cobertura.
- Cero escrituras PostgreSQL.
- Cero reutilización de IDs del modelo.
- Unidad de análisis: partido completo.

## Resultado actual

La implementación quedó validada contra el staging actual. Los 44 partidos
existentes quedan rechazados por cutoff o reutilización; no hay candidatos
independientes y no se ejecuta evaluación.

## Siguiente paso

Ejecutar este gate después de cada sincronización de Fase 30. Si alcanza 30
partidos independientes, preparar la evaluación confirmatoria sin modificar el
router oficial.
