# Fase 116 — plan verificable del motor matemático in-live

Implementar `live_probability_engine_v1` como salida live oficial inmediata,
con rollback por configuración y fallback automático a Markov Live. La ruta
pre-match Dixon-Coles/Kalman, sus snapshots y sus probabilidades permanecen
intactos.

El contrato `live_probability_engine_contract_v1` recibe un snapshot causal e
inmutable con identidad, kickoff, timestamp, periodo, reloj, marcador,
expulsiones, prior pre-match y eventos normalizados/deduplicados. Debe rechazar
eventos futuros, relojes inválidos, scores irreconciliables y priors posteriores
al kickoff, y publicar hashes de snapshot, parámetros, configuración y salida.

La composición oficial integra intensidades, no promedia probabilidades:

1. Poisson dinámico por intervalos para goles restantes y mercados 1X2,
   periodos, O/U 0.5–3.5, BTTS, marcador exacto y distribución de goles.
2. CTMC de regímenes con matriz generadora válida, marcador, reloj, presión y
   tarjetas rojas, conservando masa en cada propagación.
3. Hazard/Cox acotado con eventos observados y decaimiento en 5/10/20 minutos.
4. Elo live como prior latente con shrinkage, sin usar el resultado objetivo.
5. `hawkes_live_v2` únicamente como residual logarítmico subcrítico; `rho=0`
   debe reproducir exactamente el baseline analítico.

Monte Carlo es diagnóstico, asincrónico y determinista por
`event_id + snapshot_hash`; ejecuta 20,000 simulaciones, publica incertidumbre y
comparación analítica, pero nunca bloquea ni decide la salida oficial.

Las rutas `/v1/predict/live` y `/v1/predict/live/fixture` conservan sus nombres.
Publican `official_live_prediction` y `live_probability_engine`; los campos de
Fase 114 se mantienen como alias temporales. ESPN Predictor y Pickcenter son
benchmark visual externo y sus probabilidades/cuotas nunca son features.

Mini App, bot y worker consumen primero el contrato oficial. La vista live se
refresca cada 15 segundos, muestra ambos equipos, datos observados, mercados,
periodos, próximos eventos, componentes, timestamp, calidad y fallback. El
navegador y el worker nunca llaman ESPN ni reciben la API key DIKAMAHA.

El replay histórico usa exclusivamente PostgreSQL existente en modo read-only,
reconstruye snapshots pseudo-live, separa desarrollo/validación/confirmación por
partido completo y kickoff, y aplica bootstrap agrupado por partido. La
evaluación no bloquea la activación inmediata ni permite afirmar superioridad
económica. No se añaden ROI, Kelly, stakes ni recomendaciones financieras.

Gates: probabilidades normalizadas, intensidades finitas/no negativas, CTMC
válido, Hawkes subcrítico, determinismo, causalidad, p95 analítico menor a
250 ms, Monte Carlo no bloqueante, fallback operativo, API key ausente del
frontend, suites Python/Vitest/Playwright/typecheck y builds Docker aprobados.

Rollback:

```text
LIVE_PROBABILITY_ENGINE_OFFICIAL=false
```
