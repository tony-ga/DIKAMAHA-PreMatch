# Fase 29 — auditoría de elegibilidad confirmatoria

## Objetivo

Determinar si la cohorte prospectiva de Fase 28 puede usarse como confirmación
independiente del router vigente, sin calcular métricas ni modificar modelos.

## Gate de independencia

- La cohorte debe estar ausente de los bloques de ajuste y calibración.
- La cohorte debe estar ausente de la selección del router.
- La unidad debe ser el partido completo.
- No se calculan pérdidas, bootstrap, significancia ni promoción durante esta
  auditoría.

## Resultado

La fase queda `ineligible_for_confirmatory_evaluation`: los 42 partidos de
Fase 28 aparecen en `phase_20_full_preconfirmation_retraining/calibration.json`.
No hay solapamiento con la confirmación de Fase 20 ni con las predicciones
publicadas del router de Fase 21, pero el solapamiento de calibración basta para
invalidar una confirmación independiente.

El router oficial no se modificó, no se calcularon métricas y no se promovió
ningún mercado.

El inventario SELECT-only también confirma que el staging local sólo contiene
44 partidos entre `2025-10-26` y `2025-11-30`; por tanto, no existe dentro del
staging actual una cohorte posterior alternativa que pueda sustituirla.
Una consulta ESPN read-only para `2026-07-20`–`2026-07-26` devolvió cero
referencias elegibles y no realizó escrituras. La consulta ampliada
`2025-11-30`–`2026-07-26` encontró 245 partidos, pero 241 ya pertenecen a la
confirmación Fase 20/21 y 4 a su calibración; tampoco se escribió staging.

## Evidencia

- `artifacts/phase_29_confirmatory_eligibility_audit/eligibility_audit.json`
- `artifacts/phase_29_confirmatory_eligibility_audit/final_report.md`
- `artifacts/phase_29_confirmatory_eligibility_audit/input_manifest.json`
- `artifacts/phase_7_15_espn_connector/final_report.md`
- `artifacts/phase_7_15_espn_connector_r5/final_report.md`
- `artifacts/phase_7_15_espn_connector_r5/eligible_matches.json`
- `artifacts/phase_7_15_espn_connector_r5/audit.json`

## Siguiente paso permitido

Capturar una cohorte nueva que no esté presente en ningún ajuste, calibración o
selección del router. La evaluación confirmatoria sólo podrá comenzar después
de superar nuevamente este gate.
