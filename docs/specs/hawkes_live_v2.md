# Especificación `hawkes_live_v2`

## Rol complementario

Hawkes Live no calcula un pronóstico autónomo. Recibe los hazards congelados
de Markov Live y modela únicamente excitación transitoria de eventos recientes.

La combinación por objetivo `c` es:

`log(lambda_combined_c) = log(lambda_markov_c) + rho_c * h_c(t)`

`h_c(t)` es no negativo, decae exponencialmente con tiempo de partido y queda
acotado. `rho_c` está en `[0, 1]` y se calibra por familia de objetivo. Fase
114 seleccionó `rho_goal=1.0` y `rho_next_event=0.0`: Hawkes puede corregir
mercados de gol sin alterar próximo evento. Si todos los `rho_c` son cero, la
salida reproduce bit a bit Markov Live. No se permite una suma directa de
lambdas.

## Seguridad matemática

- matriz self/cross no negativa y radio espectral menor que uno;
- memoria, beta, residual y multiplicador máximos versionados;
- intensidades positivas y finitas;
- eventos futuros, anulados, desconocidos o sin reloj no excitan;
- el residual, la salida Markov y la combinada se serializan por separado.

## Gate

La comparación es `combined_live` contra Markov Live congelado con los mismos
snapshots. La variante global mejoró agregado, pero sólo alcanzó 59.375% de
ligas no degradadas. Fase 114 congeló entonces una allowlist de 17 ligas usando
exclusivamente validación y un mínimo de 30 partidos por liga. En confirmación,
la política selectiva obtuvo delta objetivo `-0.000398`, IC95%
`[-0.000650, -0.000135]`, y 84.375% de ligas no degradadas. Hawkes sigue
shadow; fuera de la allowlist y en próximo evento cae exactamente a Markov.

La política versionada está en
`artifacts/phase_114_live_markov_hawkes_v1/hawkes_league_policy.json`. El runner
la carga por defecto y comprueba que fue seleccionada en `validation_only` sin
usar confirmación.

Version: 2.2.0
Created: 2026-08-07; updated: 2026-08-08
