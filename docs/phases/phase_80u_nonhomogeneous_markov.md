# Fase 80U — Markov no homogéneo pre-match

## Objetivo

Modelar `P(Y_t | Y_{t-1}, X_pre, ventana)` sin discretizar el contexto.

## Contrato

- `Y_t` es la clase direccional observable;
- `X_pre` contiene sólo perfiles congelados antes del kickoff;
- la interacción `Y_{t-1} × X_pre` parametriza matrices distintas por matchup;
- el modelo define pre-match la distribución conjunta de todas las secuencias;
- el scoring condicional no implica consumo live.

## Comparadores

- tabular factorized;
- Markov directo;
- modelo continuo same-data sin estado anterior.

Los gates de Fase 80 permanecen sin cambios. El router y Fase 81 siguen
bloqueados.

## Resultado

`rejected_for_revision`

Se seleccionaron `C=0.003` y temperatura `1.0`. Contra el Markov directo,
80U mejora log-loss `0.001797` con IC95% estrictamente positivo. Sin embargo,
el mejor comparador congelado fue el continuo same-data: frente a éste la
mejora cae a `0.000431`, Brier `0.000119`, IC95%
`[-0.000161, 0.001051]` y sólo 55.17% de ligas no degradan.

El candidato se conserva en shadow como la mejor formulación encontrada, pero
no supera promoción. La confirmación queda clausurada para nuevas decisiones.
