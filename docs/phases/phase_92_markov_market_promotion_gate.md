# Fase 92 — gate de promoción Markov por mercado

## Objetivo

Evaluar individualmente las cuatro líneas prospectivas de Fase 90 después de
completar Fase 91.

## Métricas

- log-loss de modelo y baseline;
- Brier de modelo y baseline;
- accuracy descriptiva;
- IC95% bootstrap pareado de mejora de log-loss;
- proporción de ligas elegibles con mejora no negativa.

## Promoción

Una línea se aprueba únicamente si:

- límite inferior del IC95% de mejora de log-loss mayor que cero;
- Brier Markov no superior al baseline;
- al menos 70% de ligas con 30 partidos no degradan.

El scoring no se ejecuta con outcomes parciales. Las líneas se promueven o
rechazan por separado y el baseline permanece como fallback.

## Estado inicial

`insufficient_coverage`: 520 predicciones y 0 outcomes. El evaluador de
10,000 remuestras está implementado y permanece sellado.
