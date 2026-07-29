# Tracker — Markov pre-match v4

Este tracker resume seguimiento. Los gates normativos viven en
`docs/plan_markov_prematch_v4.md` y en cada documento de fase.

| Fase | Trabajo | Estado | Evidencia de cierre |
| --- | --- | --- | --- |
| 72 | Contrato causal y expansión ESPN | ready_for_next_phase | `artifacts/phase_72_markov_causal_contract/final_report.md` |
| 73 | Snapshots pre-match multicutoff | insufficient_coverage, acumulando | `artifacts/phase_73_prematch_multicutoff_snapshots/final_report.md` |
| 74 | Corpus secuencial causal | ready_for_phase_75 | `artifacts/phase_74_causal_sequence_corpus/final_report.md` |
| 75 | Targets direccionales y baselines fuertes | ready_for_next_phase | `artifacts/phase_75_temporal_baseline_targets/final_report.md` |
| 76 | Descubrimiento de estados latentes | ready_for_next_phase; 76R aprueba dos folds OOS | `docs/phases/phase_76_crossfit_reaudit.md` |
| 76C | Confirmación prospectiva ciega v3 | retirada como ruta activa; evidencia negativa preservada | `docs/phases/phase_76_v3_prospective_gate.md` |
| 77 | Estado inicial pre-match | ready_for_next_phase con representación dual | `artifacts/phase_77_dual_state_reaudit/final_report.md` |
| 78 | Transición contextual y duración | ready_for_next_phase | `artifacts/phase_78_context_transitions/final_report.md` |
| 79 | Simulación pre-match coherente | autorizada | pendiente |
| 80 | Walk-forward anidado y ablaciones | programada | pendiente |
| 81 | Confirmación prospectiva independiente | programada | pendiente |
| 82 | Integración oficial Markov v4 | bloqueada | pendiente |
| 83 | Valor de mercado | bloqueada | pendiente |

## Regla de actualización

Al cerrar una fase:

1. enlazar su `final_report.md`;
2. copiar sólo la clasificación controlada;
3. actualizar `docs/status.md` y `docs/00_roadmap_actual.md`;
4. señalar la siguiente fase permitida;
5. no convertir una mejora de desarrollo en promoción.
