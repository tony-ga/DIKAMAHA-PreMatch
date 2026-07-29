# Fase 105 — auditoría histórica completa de 1,000 partidos

## Resultado

Se evaluaron los 1,000 partidos más recientes con cobertura simultánea de
PBP, Fase 84A, Fase 88 y la cadena oficial selectiva Fase 104. Cada partido
se predijo antes de aplicar su actualización walk-forward y después se liquidó
contra sus eventos reconciliados.

- 21 ligas;
- 12,000 decisiones;
- 12 mercados por partido;
- accuracy global: `60.11%`;
- confianza media: `61.00%`;
- log-loss: `0.707251`;
- Brier: `0.270837`;
- 4 partidos con 12/12;
- 0 partidos con 0/12.

## Motores

| Motor | Mercados | Accuracy |
|---|---|---:|
| Dixon-Coles + Kalman | 1X2, Over 2.5 | 50.55% |
| Poisson estructural baseline | BTTS | 51.60% |
| Fase 84A | corners/tiros agregados | 64.28% |
| Markov Fase 88 | mercados temporales | 61.80% |

Clasificación: `historical_diagnostic`; no modifica promoción ni autoriza
apuestas.
