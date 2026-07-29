# Fase 42 — fusión estructural multi-liga

## Objetivo

Construir predicciones pre-match OOS candidatas combinando:

1. Un prior de intensidad con parametrización compatible con Dixon-Coles,
   regularizado por tasas de gol históricas de desarrollo.
2. Una actualización Kalman v2 que parte del prior y sólo incorpora el
   resultado de un partido después de haber emitido su predicción.
3. Las transiciones Markov de Fase 40 para distribuir la intensidad entre seis
   ventanas de 15 minutos.

## Decisión metodológica

No se ejecuta un MLE Dixon-Coles global con los 853 equipos porque produce una
optimización desproporcionada para este corpus. Se ajusta por liga una forma
regularizada de la parametrización:

`lambda_home = exp(intercepto_liga + ventaja_local + ataque_local - defensa_visitante)`

`lambda_away = exp(intercepto_liga + ataque_visitante - defensa_local)`

La auditoría marca explícitamente `mle_optimized: false`. Esto queda como
prior estructural experimental; no se presenta como ajuste MLE definitivo.

## Resultado

- 3,713 predicciones OOS candidatas.
- Validación: 1,856; confirmación: 1,857.
- 32 ligas con partidos en esos bloques.
- Tres competiciones usan fallback neutral por soporte de desarrollo escaso.
- Cinco partidos de `fifa.intercontinental_cup` quedan excluidos por ausencia
  de desarrollo.
- 300 simulaciones por partido.
- Error máximo de conservación de intensidad: menor que `1e-14`.
- Probabilidades 1X2 normalizadas.

## Gate de salida

`ready_for_multileague_oos_evaluation`. Las predicciones no se evalúan en esta
fase, no se calculan métricas de mercado y no se modifica el router oficial.
