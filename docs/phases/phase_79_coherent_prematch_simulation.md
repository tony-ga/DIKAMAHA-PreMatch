# Fase 79 — simulación pre-match coherente

## Objetivo

Producir trayectorias conjuntas de 18 microventanas y mercados agregados de
15 minutos sin consumir información live ni alterar la capacidad goleadora
estimada por Dixon-Coles/Kalman.

## Contrato

- el estado dual es `style_state(2) × match_regime(3)`;
- el estilo permanece fijo durante toda la trayectoria;
- ambos regímenes avanzan desde el estado conjunto anterior;
- los pesos positivos de riesgo se normalizan por equipo y trayectoria;
- la suma de las 18 intensidades es exactamente la lambda estructural;
- una liga sin soporte usa backoff `core` global, nunca falla ni copia un
  reparto temporal plano.

## Resultado

`ready_for_next_phase`

| Gate | Resultado | Umbral |
| --- | ---: | ---: |
| Replay con misma semilla | idéntico | idéntico |
| Error máximo de conservación | `6.661e-16` | `<1e-6` |
| Error de suma 1X2 | `0.0` | `<=1e-9` |
| Lecturas posteriores al cutoff | `0` | `0` |
| Cambios de estilo | `0` | `0` |
| Distancia core vs reparto plano | `0.016304` | `>0` |

Se simularon 5,000 trayectorias en modo contextual y 5,000 en modo core. La
salida contiene 1X2, over 2.5, BTTS, gol en primera mitad y probabilidad de gol
por cada ventana de 15 minutos.

## Alcance

La fase valida coherencia mecánica y causal, no valor incremental OOS. El
router oficial conserva el baseline. Fase 80 queda autorizada para comparar
Markov contra el mejor baseline y el tabular same-data bajo walk-forward
anidado.

La regresión integral posterior cerró con `348 passed`, incluidas las pruebas
PostgreSQL; sólo permanece una advertencia de deprecación de TestClient ajena
al simulador.
