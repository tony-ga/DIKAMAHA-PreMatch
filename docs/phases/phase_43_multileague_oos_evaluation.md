# Fase 43 — evaluación OOS multi-liga

## Resultado

Se evaluaron 3,713 predicciones por partido completo: 1,856 de validación y
1,857 de confirmación. Los baselines se estimaron únicamente con 5,576
partidos de desarrollo.

En confirmación, la fusión Markov + prior estructural fue peor que el baseline
Poisson estructural en los cinco mercados principales:

| Mercado | Mejora baseline − modelo | IC bootstrap 95% |
|---|---:|---:|
| 1X2 | -0.052381 | [-0.103017, -0.010282] |
| Over 2.5 | -0.145486 | [-0.223800, -0.078113] |
| BTTS | -0.069113 | [-0.126793, -0.022520] |
| Gol primer tiempo | -0.027021 | [-0.060447, -0.002172] |
| Gol segundo tiempo | -0.065735 | [-0.121737, -0.021382] |

Una mejora positiva habría favorecido al modelo. Todos estos valores son
negativos y sus intervalos no cruzan cero. Las remontadas no tienen soporte
suficiente para promoción.

## Decisión

`rejected_for_promotion`. El router oficial permanece sin cambios y el
baseline estructural conserva el control. No se debe activar esta fusión en
producción ni crear mercados a partir de ella.
