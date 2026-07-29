# Fase 63 — predicciones Markov candidatas congeladas

## Propósito

Crear una salida pre-match reproducible para el primer mercado experimental de
la nueva faceta Markov: `first_half_goal`. La salida no es oficial y no puede
activar el router.

## Entradas congeladas

- Cohorte: `phase_62_independent_cohort_lock_v1`, 9 fixtures futuros.
- Histórico: `phase_60_taxonomy_snapshot_candidate_v1/event_windows.json`.
- Clasificador: `phase_63_initial_state_calibration_v1/state0_classifier.joblib`.
- Transiciones: `phase_40_multileague_markov_calibration_v1`.
- Baseline de referencia: auditoría de Fase 56.

## Método

1. Construye perfiles de los últimos cinco partidos estrictamente anteriores al
   kickoff.
2. Estima `P(state_0)` para local y visitante con el clasificador calibrado.
3. Propaga la masa de supervivencia sin gol por las tres ventanas de la
   primera mitad usando las transiciones con backoff team→liga→ventana→global.
4. Usa tasas de gol suavizadas por liga, localía, ventana y estado.
5. Conserva un baseline temporal sin estado para comparación futura.

La aproximación de transición para la salida pre-match mantiene el diferencial
en `level` al no observar goles del partido objetivo. Esto queda registrado como
supuesto candidato y será evaluado, no presentado como verdad operativa.

## Resultado

- Artefacto: `artifacts/phase_63_frozen_markov_candidate_v1/`.
- Predicciones congeladas: `9`.
- Mercado: `first_half_goal`.
- Resultados y play-by-play observados: `False`.
- Router oficial modificado: `False`.
- Markov promovido: `False`.
- Clase escasa: `repliegue` no aparece en desarrollo.

## Gate siguiente

Después de los kickoffs, leer sólo el resultado final y las ventanas
observadas, calcular log-loss y bootstrap agrupado por partido, y comparar con
el baseline reforzado. No se permite seleccionar umbral, recalibrar ni cambiar
el router usando la cohorte congelada antes de publicar la evaluación.

