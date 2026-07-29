# Fase 64 — evaluación OOS selectiva Markov

## Objetivo

Evaluar la salida congelada de Fase 63 para `first_half_goal` después de que
termine la cohorte independiente. La fase nunca recalibra el modelo ni cambia
el router.

## Entrada post-match

El ejecutor recibe explícitamente un JSON de ventanas observadas mediante
`--observed-windows`. Debe contener exactamente 12 filas por fixture: seis
ventanas de 15 minutos para cada equipo. Sin ese argumento, no se leen targets.

## Métricas y gate

- log-loss de Markov frente al baseline temporal sin estado;
- Brier score de ambos modelos;
- bootstrap agrupado por partido;
- mínimo operativo de 30 partidos;
- intervalo de mejora estrictamente positivo;
- cero violaciones temporales o de identidad.

Con nueve partidos, aunque exista scoring completo, el resultado sólo puede ser
`selective_oos_evaluation_insufficient_support`; nunca permite promoción.

## Replay histórico diagnóstico

Se añadió `scripts/run_phase_64_historical_markov_replay.py` para reproducir
30 partidos recientes en orden temporal. Cada partido se excluye antes de
predecirlo y sus ventanas se incorporan al historial sólo después de calcular
la predicción. El resultado actual es:

- log-loss Markov: `0.796682`;
- log-loss baseline: `0.730142`;
- mejora Markov-baseline: `-0.066540`;
- IC bootstrap de la mejora: `[-0.151079, 0.017641]`.

Se probó la integración residual con `p_fusion=(1-alpha)*p_baseline +
alpha*p_markov`. En los primeros 15 partidos, la validación eligió `alpha=0.0`;
la fusión óptima fue exactamente el baseline. En los 15 partidos de holdout,
la fusión obtuvo log-loss `0.777966`, sin mejora frente al baseline.

El replay confirma que el flujo técnico funciona, pero no demuestra valor
incremental: Markov pierde en promedio, el intervalo cruza cero y la selección
de peso descarta completamente su contribución. Además, el
bloque se encuentra dentro del periodo de confirmación usado para auditar
state_0, por lo que no puede contar como evidencia independiente ni habilitar
promoción.

Artefacto: `artifacts/phase_64_historical_markov_replay_v1/`.

## Estado actual

El ejecutor está preparado y su corrida sin targets queda en
`waiting_for_postmatch_targets`. La primera fecha de la cohorte es posterior a
la hora actual de la última auditoría. No se observaron resultados, no se
calcularon pérdidas y el router permanece baseline-only.

## Ejecución posterior

```bash
python scripts/run_phase_64_selective_oos_evaluation.py \
  --observed-windows RUTA_A_VENTANAS_POST_MATCH.json
```
