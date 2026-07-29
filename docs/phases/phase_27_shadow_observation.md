# Fase 27 — observación read-only de predicciones pre-match

## Objetivo

Observar predicciones pre-match oficiales ya generadas por el router de Fase
21 junto con el catálogo shadow, sin recalcular modelos, modificar datos ni
activar candidatos experimentales.

## Fuentes permitidas

- Predicciones oficiales de `phase_21_target_model_router`.
- Filas causales de `phase_22_prematch_first_half_signal`.
- Identidad y contexto auditado de `phase_23_prematch_context_fetch`.
- Contrato shadow congelado de Fase 25.

La observación no convierte tasas auxiliares en parámetros Dixon-Coles o
Kalman: esa semántica requiere una decisión y una especificación aparte.

## Gates

- Cada predicción oficial debe tener una fila causal y una fila de contexto.
- Los cutoffs deben coincidir después de normalizar timestamps.
- Las fuentes deben declarar que no usaron datos del partido objetivo.
- Los modelos seleccionados deben coincidir con el router oficial congelado.
- El catálogo debe mantenerse en modo `read_only` y con todos sus candidatos
  desactivados.
- Los artefactos de observación no deben contener targets ni pérdidas calculadas
  con el resultado final.

## Cierre de fase

La fase se considera completada cuando la cohorte oficial histórica permitida
queda observada de forma completa y reproducible. La ausencia de una cohorte
prospectiva nueva no invalida este cierre; sólo impide iniciar la siguiente
evaluación prospectiva.

## Artefactos

`config.json`, `input_manifest.json`, `coverage.json`, `metrics.json`,
`observations.json`, `audit.json`, `validation_report.md`, `final_report.md` y
`hashes.json`.

## Siguiente paso permitido

La Fase 27 queda cerrada. El siguiente hito es acumular observaciones
prospectivas de sólo lectura cuando exista una cohorte nueva válida. No
reentrenar, incorporar nuevas cohortes ni promover mercados sin una decisión
posterior.
