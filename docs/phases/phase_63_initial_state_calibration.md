# Fase 63 — calibración inicial `state_0`

## Resultado

Se entrenó un clasificador multinomial calibrado con perfiles de los cinco
partidos previos de cada equipo. El desarrollo, validación y confirmación son
temporales y no leen la cohorte independiente de Fase 62.

- 9,801 partidos y 11,760 registros de equipo en desarrollo.
- Log-loss validación: modelo `0.697986`, prior global `0.773386`, prior liga
  `0.709563`.
- Log-loss confirmación histórica: modelo `0.745715`, prior global `0.838251`,
  prior liga `0.757864`.
- `repliegue` no aparece en desarrollo; queda como clase explícita con respaldo
  mínimo y el candidato se marca con soporte escaso.
- router modificado: `False`; Markov promovido: `False`.

## Gate

El clasificador queda listo para evaluación independiente del target
`first_half_goal`, pero no se activa. Antes de promoción se requiere que la
cohorte de Fase 62 alcance sus kickoffs, se congelen las probabilidades y se
evalúe contra baseline reforzado con bootstrap por partido.

## Artefactos

- `scripts/run_phase_63_initial_state_calibration.py`
- `artifacts/phase_63_initial_state_calibration_v1/audit.json`
- `artifacts/phase_63_initial_state_calibration_v1/final_report.md`
- `artifacts/phase_63_initial_state_calibration_v1/state0_classifier.joblib`

Version: 1.0.0
Created: 2026-07-27
