# Fase 06 — `markov_pre_match v2`

## Objetivo

Construir el proveedor canónico de intensidad de gol y el simulador Markov v2 sin promocionarlo.

## Entregables

- Contrato por partido para los priors Dixon-Coles/Kalman.
- Redistribución temporal de intensidad condicionada por estado.
- Auditoría de masa, causalidad, determinismo y provenance.

## Gate de salida

- La intensidad total de cada equipo se conserva entre el prior y las seis ventanas.
- Ningún target del partido evaluado entra al proveedor.
- La salida queda marcada como experimental hasta una nueva evaluación OOS.
