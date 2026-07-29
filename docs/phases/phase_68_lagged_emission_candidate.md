# Fase 68 — emisión Markov temporalmente alineada

Se probaron dos variantes sobre 3,921 partidos walk-forward:

- `shifted`: baseline en ventana 0 y emisión `state_t→goles_t+1` después;
- `same_first`: emisión contemporánea sólo en ventana 0 y emisión desplazada
  después.

La validación seleccionó `same_first`, pero el holdout quedó:

- Markov: `0.640806`;
- baseline: `0.639682`;
- mejora: `-0.001124`;
- IC: `[-0.002553, 0.000002]`.

Clasificación: `lagged_emission_no_incremental_value`. La corrección temporal
es conceptualmente necesaria, pero no basta para producir valor.

Artefacto: `artifacts/phase_68_lagged_emission_candidate_v1/`.

