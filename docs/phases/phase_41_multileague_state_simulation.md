# Fase 41 — simulación de estados multi-liga

## Objetivo

Ejecutar trayectorias pre-match de los cuatro estados operativos usando las
matrices Markov de Fase 40 y priors iniciales estimados únicamente con el
bloque temporal de desarrollo.

## Alcance deliberadamente limitado

Esta fase simula dinámica de estados, no goles ni mercados. El corpus global
todavía no tiene un prior Dixon-Coles/Kalman comparable y auditado para todas
las ligas; fabricar intensidades impediría distinguir la señal Markov de una
suposición arbitraria. La fusión estructural queda para la siguiente fase.

La liga `fifa.intercontinental_cup` queda excluida porque no tiene partidos en
el bloque de desarrollo; sus cinco partidos aparecen en validación/confirmación
y no se usan para construir priors ni para simular.

## Artefactos

- `scripts/run_phase_41_multileague_state_simulation.py`
- `artifacts/phase_41_multileague_state_simulation_v1/`

## Gate de salida

- 5,000 trayectorias reproducibles por liga elegible.
- Seis ventanas de 15 minutos por trayectoria.
- Priors de estado inicial calculados sólo con desarrollo temporal.
- Backoff `league_context → window → global` con soporte y smoothing de Fase 40.
- Probabilidades normalizadas y semilla determinista.
- Cero eventos, scores, targets o IDs del partido objetivo usados.
- Router oficial, mercados oficiales y `match_features v1` sin modificaciones.

## Estado

`ready_for_multileague_structural_fusion`: 40 ligas simuladas; una liga se
retuvo por ausencia de soporte de desarrollo. No es evidencia de promoción.
