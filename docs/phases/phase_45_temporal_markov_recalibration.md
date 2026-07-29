# Fase 45 — recalibración temporal Markov

Se ajustó una mezcla convexa entre Markov temporal y el baseline estructural,
seleccionando el peso exclusivamente en validación:

- Primer tiempo: 25% Markov / 75% estructural.
- Segundo tiempo: 30% Markov / 70% estructural.

En confirmación:

- Primer tiempo: mejora `-0.000127`, IC 95% `[-0.001275, 0.001030]`.
- Segundo tiempo: mejora `-0.001421`, IC 95% `[-0.002902, -0.000009]`.

La señal temporal no aporta valor incremental confirmado. Clasificación:
`temporal_signal_no_incremental_value`. No se modifica el router.
