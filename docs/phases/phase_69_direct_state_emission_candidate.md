# Fase 69 — emisión directa state_0

Se aisló la señal inicial sin transiciones: `P(state_0)` del clasificador se
combinó con una emisión suavizada por liga y par de estados para
`first_half_goal`. El peso se seleccionó en la primera mitad del replay.

- alpha seleccionado: `0.3`;
- holdout candidato: `0.640280`;
- holdout baseline: `0.639682`;
- mejora: `-0.000598`;
- IC: `[-0.001654, 0.000433]`.

Clasificación: `direct_state_emission_no_incremental_value`. `state_0` aporta
información sobre el label de estado, pero no demuestra valor incremental para
predecir gol en primer tiempo con los estados actuales.

Artefacto: `artifacts/phase_69_direct_state_emission_candidate_v1/`.

