# Fase 71 — revisión semántica de estados Markov

## Objetivo

Determinar si una cadena conjunta de ritmo, temporalmente alineada y fusionada
como residual, aporta información pre-match distinta del baseline para
`first_half_goal`.

## Entradas

- Snapshot candidato de Fase 60.
- Corte temporal de 5,880 partidos de desarrollo.
- Replay causal posterior de 3,921 partidos.
- Baseline temporal causal de Fase 65.

## Entregables

- Configuración y umbrales congelados desde desarrollo.
- Labels de ritmo y control separados.
- Matrices de transición conjuntas con cobertura.
- Predicciones walk-forward y métricas de validación/holdout.
- Auditoría de causalidad, soporte, estabilidad por liga y abstención.

## Gate de salida

La fase sólo queda lista para una cohorte independiente si:

- los estados tienen soporte y semántica causal;
- `state_0` y la transición son predecibles antes del kickoff;
- el residual supera al baseline en holdout;
- el IC bootstrap por partido es estrictamente positivo;
- no existe degradación material en ligas con soporte.

Si falla cualquier condición, Markov permanece fuera del router y la salida
funcional es el baseline sin alteración.

