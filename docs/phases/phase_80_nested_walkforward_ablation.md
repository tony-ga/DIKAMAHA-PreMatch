# Fase 80 — walk-forward anidado y ablaciones

## Objetivo

Demostrar si la dependencia secuencial de Markov aporta valor incremental
frente al mejor baseline estructural/temporal y frente a un modelo tabular con
exactamente la misma información causal.

## Folds

1. `fit → selection`: selección de variante, regularización y calibración.
2. `fit + selection → confirmation`: evaluación cerrada de la única variante
   elegida.

Todos los estados, priors iniciales, transiciones, emisiones y calibradores se
reajustan dentro del train correspondiente. La unidad IID es el partido.

## Ablaciones obligatorias

- contexto rival;
- persistencia/duración;
- dirección local/visitante;
- granularidad 5 frente a 15 minutos.

## Gate

- mejora de log-loss `>= max(0.005, 1%)`;
- IC bootstrap 95% estrictamente positivo;
- mejora Brier `>=0.002`;
- ECE no empeora más de `0.005`;
- al menos 70% de ligas con soporte no degradan;
- ninguna liga con `n>=100` empeora más de `0.01`;
- estabilidad por temporada/cutoff;
- mejora frente al tabular same-data o rechazo de la estructura Markov.

El router permanece congelado durante toda la fase.

## Resultado

`rejected_for_revision`

La variante `15m` con fuerza residual positiva `0.1` fue elegida únicamente en
selection. En confirmation obtuvo mejora de log-loss `-0.000002`, mejora
Brier `+0.000002` e IC95% `[-0.000019, 0.000016]`. Sólo 44.83% de las 29 ligas
admitidas tuvieron delta no negativo.

La auditoría corrigió y volvió a ejecutar tres fuentes de degradación ajenas a
Markov: reemplazo del carrier temporal, pérdida de dependencia `both` y
shrinkage sobredimensionado. Después de corregirlas, el efecto converge a cero.
La ablación sin duración reproduce exactamente el tabular, demostrando que la
transición marginalizada no añade información pre-match al score por ventana.

Fase 81 queda bloqueada. El próximo trabajo permitido es revisar targets de
trayectoria completa o mercados secuenciales; no seguir ajustando pesos sobre
el mismo marginal.
