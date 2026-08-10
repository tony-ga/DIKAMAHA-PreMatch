# Fase 116 — Motor matemático oficial in-live

## Objetivo

Sustituir la salida live heredada por `live_probability_engine_v1`, compuesto
por Poisson dinámico, CTMC, hazard, Elo live y Hawkes residual, con Monte Carlo
diagnóstico asincrónico y fallback automático. Pre-match permanece intacto.

## Entregables

- contrato `live_probability_engine_contract_v1`;
- motor analítico determinista y runner Monte Carlo de 20,000 simulaciones;
- integración oficial en API, Mini App, bot y worker;
- aliases compatibles de Fase 114 y rollback por configuración;
- runner histórico read-only sobre la base existente;
- artefactos de configuración, auditoría, métricas, cobertura y hashes.

## Gate técnico

- causalidad, identidad y score/PBP fail-closed;
- probabilidades normalizadas, CTMC conservativa y hazards acotados;
- Hawkes subcrítico y residual;
- replay y Monte Carlo deterministas;
- latencia analítica p95 menor a 250 ms;
- fallback exacto comprobado;
- API, bot, Mini App, worker y builds sin regresión.

## Política de promoción

DEC-155 autoriza por instrucción explícita la activación oficial inicial sin
hacer depender el despliegue de la evaluación histórica. Las métricas OOS se
publican como diagnóstico y no autorizan afirmaciones de superioridad o valor
económico. El rollback permanece disponible durante toda la fase.

## Exclusiones

- cambios a la ruta pre-match o `match_features v1`;
- predictor/odds ESPN como features;
- ROI, Kelly, stakes, apuestas o enlaces financieros;
- reuso de snapshots como unidades IID.

Version: 1.0.0
Created: 2026-08-09
