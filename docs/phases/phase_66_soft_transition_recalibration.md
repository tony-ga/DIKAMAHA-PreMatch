# Fase 66 — recalibración suave de transiciones

## Objetivo

Corregir la dominancia de los tiers `global/uniform` mediante pooling
jerárquico suave entre `team`, `competition`, `window` y `global`.

## Diseño

- 5,880 partidos y 58,800 transiciones para desarrollo.
- 3,921 partidos walk-forward para evaluación.
- Cinco valores de especificidad: `2, 4, 8, 16, 32`.
- Selección en los primeros 1,960 partidos.
- Holdout en los 1,961 partidos restantes.
- Emisiones, state_0 y baseline sin cambios.

## Resultado

- Especificidad seleccionada: `2.0`.
- Holdout Markov: `0.641220`.
- Holdout baseline: `0.639682`.
- Mejora: `-0.001539`.
- IC bootstrap: `[-0.003164, -0.000244]`.
- Clasificación: `soft_transition_recalibration_no_incremental_value`.

La variante mejoró marginalmente en validación, pero perdió en todo el
holdout. Por lo tanto, el pooling suave no corrige por sí solo la degradación.
La evidencia desplaza la investigación hacia la emisión de gol condicionada al
estado y hacia la calidad semántica de `state_0`; no se autoriza otra búsqueda
de pesos de transición sin una hipótesis nueva.

## Artefactos

`artifacts/phase_66_soft_transition_recalibration_v1/`

