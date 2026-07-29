# Fase 46 — Markov condicionado por perfil pre-match

## Hipótesis

El prior del estado inicial de Markov puede mejorar si, además de equipo y
localía, se condiciona por el perfil reciente del equipo. El perfil usa los
últimos cinco partidos anteriores al kickoff y resume ritmo (`tiros + tiros a
puerta + corners`), presión (`presión + tiros a puerta`) y disciplina
(`faltas + 2·amarillas + 4·rojas`).

## Control causal

- Los perfiles se calculan por fecha, antes de incorporar los partidos de esa
  misma fecha al historial.
- Los umbrales de terciles se ajustan sólo con desarrollo.
- Las distribuciones de estado se ajustan sólo con la ventana 0 del bloque de
  desarrollo.
- El backoff es `perfil -> equipo -> liga -> global`.
- Los targets del partido evaluado no se usan como feature ni para elegir
  parámetros de confirmación.

## Resultado

Se generaron 3,713 predicciones: 1,856 de validación y 1,857 de confirmación.
El prior específico por perfil se utilizó en 5,863 de 7,426 roles equipo-
partido (`78.95%`); el resto usó backoff explícito.

En confirmación frente al Poisson estructural:

| Mercado | Mejora de log-loss | IC 95% |
| --- | ---: | --- |
| Primer tiempo | `-0.028410` | `[-0.059542, -0.002891]` |
| Segundo tiempo | `-0.079817` | `[-0.138943, -0.029396]` |

El signo negativo indica que el candidato perdió frente al baseline. La misma
dirección ya aparecía en validación (`-0.061832` y `-0.088497`). Los mercados
completos permanecen analíticos y coinciden con el baseline estructural.

## Gate

Clasificación: `profile_candidate_evaluated_no_promotion`.

La hipótesis queda rechazada para promoción en esta formulación. El router
oficial no cambia y el candidato queda sólo como evidencia experimental.

Artefactos:

- `artifacts/phase_46_profile_conditioned_markov_v1/final_report.md`
- `artifacts/phase_46_profile_conditioned_markov_v1/audit.json`
- `artifacts/phase_46_profile_conditioned_markov_v1/metrics.json`
- `artifacts/phase_46_profile_conditioned_markov_v1/predictions.json`

