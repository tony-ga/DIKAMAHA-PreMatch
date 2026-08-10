# Contrato `provider_match_context_v1`

## Propósito

Añadir a la presentación pre-match y live un benchmark probabilístico externo
y una curva heurística de dinámica sin alterar los modelos DIKAMAHA.

## Clasificación de campos

| Campo | Clasificación | Uso permitido |
| --- | --- | --- |
| predictor 1X2 explícito | `display_only` / `live_only` | benchmark visual |
| historial win probability | `live_only` | curva externa observada |
| play-by-play ponderado | `live_only_display_heuristic` | gráfica de presión |
| `pickcenter` | `financial_isolated` | disponibilidad/provenance únicamente |
| Markov/Hawkes/combinado | `experimental_shadow_not_promoted` | capas DIKAMAHA separadas |

## Probabilidades del proveedor

- aceptar sólo valores explícitos `homeWinPercentage`,
  `awayWinPercentage` y `tiePercentage`, o equivalentes documentados por el
  parser;
- normalizar porcentajes 0–100 a 0–1 y exigir valores finitos en `[0,1]`;
- el vector 1X2 debe sumar uno dentro de tolerancia y se renormaliza sólo para
  redondeo, nunca para reparar campos ausentes incompatibles;
- conservar timestamp, alcance `pre_match|live`, fuente y estado de cobertura;
- `not_published` es una salida normal y no activa ningún fallback sintético;
- nunca leer `pickcenter` para fabricar el predictor.

## Presión de partido

Pesos congelados por evento local/visitante, con signo positivo/negativo:

```text
goal=25
shot_on_target=8
shot_off_target=4
corner=3
foul=1
```

Se agregan por minuto y se aplica una media móvil centrada de cinco minutos.
La serie es descriptiva, no calibrada, no probabilística y no se entrega a
Markov, Hawkes ni al prior pre-match. Los goles se conservan como marcadores
visuales sobre la serie.

## Política de sustitución

El predictor externo no sustituye Markov Live ni Hawkes. Una sustitución sólo
puede evaluarse con cobertura suficiente, snapshots timestamped, outcomes
separados, comparación OOS por partido y decisión nueva de promoción. La
ausencia del proveedor nunca elimina las capas DIKAMAHA.

Version: 1.0.0
Created: 2026-08-09
