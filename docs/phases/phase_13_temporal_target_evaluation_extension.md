# Fase 13 — evaluación OOS ampliada de targets temporales v2

## Objetivo

Evaluar la extensión de 241 partidos con Markov v2 ajustado exclusivamente en
los 381 partidos canónicos. Las intensidades pre-kickoff provienen de
Dixon-Coles congelado antes del partido; ningún target del partido evaluado se
usa como feature.

## Resultado

Clasificación: `rejected_for_revision`.

- train: 381 partidos;
- confirmación: 241 partidos;
- cobertura completa de priors y predicciones;
- `first_half_goal`: Markov 0.669821 vs baseline 0.629704;
- `second_half_goal`: Markov 0.479849 vs baseline 0.408920;
- ninguna mejora bootstrap tuvo intervalo estrictamente positivo;
- mercados promovidos: `False`.

La fase no invalida los targets como labels. Indica que la parametrización
actual de Markov v2 no es suficientemente robusta frente al cambio temporal de
temporada. El siguiente trabajo permitido es calibración/revisión con folds
OOS congelados, seguida de una nueva confirmación independiente.

## Artefactos

- `artifacts/phase_13_temporal_target_evaluation_extension/metrics.json`;
- `artifacts/phase_13_temporal_target_evaluation_extension/bootstrap_results.json`;
- `artifacts/phase_13_temporal_target_evaluation_extension/audit.json`;
- `artifacts/phase_13_temporal_target_evaluation_extension/final_report.md`.

# Version: 1.0.0
# Created: 2026-07-26
