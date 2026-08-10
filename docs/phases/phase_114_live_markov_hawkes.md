# Fase 114 — Markov Live y Hawkes residual

## Objetivo

Implementar la infraestructura shadow para seguir partidos ESPN por polling,
actualizar una inferencia Markov in-play y aplicar Hawkes como residual
complementario, sin modificar salidas oficiales ni la ruta pre-match.

## Entregables

- contratos `live_event_stream_v1`, `markov_live_v1` y `hawkes_live_v2`;
- captura fresca scoreboard/event/plays/situation con fallback summary;
- normalizador causal y ledger raw-first inyectable;
- filtro Markov Live con mercados de goles restantes y próximo evento;
- residual Hawkes subcrítico y combinación logarítmica con shrinkage;
- bloques API shadow compatibles con `/v1/predict/live`;
- catálogo `/v1/live` y selección `/v1/predict/live/fixture` para producto;
- prior causal reconstruido desde el snapshot histórico versionado cuando el
  kickoff ya pasó, con hash, identidad y cutoff estricto auditables;
- pruebas de causalidad, replay, fallback y estabilidad.

## Gate técnico de esta entrega

- eventos posteriores al snapshot rechazados;
- reloj, score, identidad y probabilidades validados fail-closed;
- replay idéntico y deduplicación determinista;
- `rho=0` equivalente a Markov Live;
- Hawkes ausente no altera Markov Live;
- endpoint live anterior mantiene compatibilidad;
- suite dirigida e integral aprobadas.

## Gate histórico sustitutivo

DEC-143 reemplaza la espera prospectiva por reconstrucción pseudo-live sobre
PostgreSQL read-only. El gate exige al menos 5,000 partidos reconciliados y 20
ligas, priors walk-forward estrictamente anteriores, kickoffs atómicos,
desarrollo/validación/confirmación separados y bootstrap por partido.

Resultado: 7,400 partidos elegibles, 34 ligas y 6,985 snapshots en
confirmación. Markov mejora el objetivo frente a score/tiempo en `-0.002259`,
IC95% `[-0.002858, -0.001635]`, y no degrada en 84.375% de ligas. Hawkes global
mejora en `-0.000648`, pero sólo no degrada en 59.375% de ligas. La política
seleccionada exclusivamente en validación admite 17 ligas con al menos 30
partidos; en confirmación mejora `-0.000398`, IC95%
`[-0.000650, -0.000135]`, y alcanza 84.375% de ligas no degradadas. Markov y
Hawkes selectivo quedan históricamente validados en shadow; el resto usa
fallback Markov exacto.

## Integración de producto v1.3

DEC-147 activa las capas ya validadas sin promoverlas. La API descubre sólo
fixtures ESPN con estado `in|live`, captura scoreboard/event/plays/situation
raw-first y ejecuta Markov Live como baseline universal. Hawkes usa la
allowlist congelada de 17 ligas y sólo ajusta mercados de gol; próximo evento
y ligas no admitidas conservan Markov exacto.

Telegram no llama ESPN ni contiene modelos. El menú `Partidos en vivo`
consulta la API, permite recalcular el snapshot y muestra por separado Markov,
residual Hawkes y combinado. `Modelos en operación` declara oficiales y
shadow visibles. Si no hay encuentros activos, responde vacío de forma normal
y permite actualizar.

DEC-152 corrige la frontera de día de ESPN: sin fecha explícita, el runtime
inspecciona D-1, D y D+1 UTC y deduplica por `match_id`; una fecha explícita
conserva alcance exacto. El detalle localiza primero el scoreboard correcto y
después realiza la captura raw-first. La respuesta añade
`observed_live_statistics`, `recent_actions` y una recomendación de refresco de
10 segundos. Estos campos son de presentación y se derivan del mismo flujo
causal ya observado; no son features nuevas ni modifican las tres capas shadow.

El prior reconstruido no se denomina prospectivamente congelado: se declara
`reconstructed_causal_prematch_prior`, excluye el match objetivo y usa sólo
historia anterior al kickoff. Esto permite operar con la base existente sin
esperar partidos nuevos.

## Exclusiones

- promoción oficial de Markov Live, Hawkes o combinado;
- reentrenamiento sobre holdouts pre-match clausurados;
- cuotas, ROI, Kelly o staking;
- uso de `probabilities` ESPN como feature;
- cambios a `match_features v1`.

Version: 1.4.0
Created: 2026-08-07; updated: 2026-08-09
