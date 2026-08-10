# Fase 100 — enriquecimiento ESPN de bots y contexto

## Objetivo

Materializar los objetivos 100A–100F del
`docs/plan_espn_bot_data_enrichment.md` mediante adaptadores read-only,
snapshots raw-first y contratos de presentación sin alterar inferencia.

## Entrada

- Fase 98 Telegram y Fase 99 Discord operativas en shadow.
- `docs/documentacion_api_futbol.md` como inventario de endpoints.
- API DIKAMAHA como única puerta a ESPN para los bots.

## Gates comunes

- endpoint permitido, parámetros normalizados y paginación completa;
- raw-first, hash, timestamp y cache versionado;
- identidad de liga/fixture/equipo/jugador preservada;
- fallbacks explícitos, sin imputar datos de proveedor;
- datos pre-match sólo si `source_fetched_at <= cutoff`;
- router, modelos, probabilidades oficiales y política económica intactos.

## Subfases

| Subfase | Entrega | Gate específico |
| --- | --- | --- |
| 100A | Venue, árbitros, broadcast, fase y branding | fixture card reconciliada |
| 100B | Standings, forma, calendario y líderes | historial sólo anterior al kickoff |
| 100C | Lesiones, roster y perfil ampliado | `validated_as_display_only` |
| 100D | Dataset de candidatos causales | `blocked_by_outcome_coverage` |
| 100E | Live/settlement robusto y benchmark opcional | `implemented_display_only` |
| 100F | Noticias y archivo de odds aislado | `validated_isolated_no_odds_exposure` |

## Evidencia 100A

`validated` el 2026-07-29. El contrato `fixture_context_v1` consume sólo
snapshots raw-first, expone identidad, fase, sede, árbitros, broadcast y
branding por API DIKAMAHA y ambos bots lo muestran con fallback explícito.
No constituye feature ni modifica inferencia. Ver
`artifacts/phase_100_espn_context_enrichment/phase_100a_report.md`.

## Evidencia 100B

`validated` el 2026-07-29. `fixture_context_v1` expone standings y calendario
por equipo desde snapshots raw-first; el calendario se filtra estrictamente a
eventos previos al kickoff y el standing Site con entries prevalece sobre un
Core vacío. Es información visible, no feature. Ver
`artifacts/phase_100_espn_context_enrichment/phase_100b_report.md`.

## Evidencia 100C

`validated_as_display_only` el 2026-07-29. Roster, estado activo e incidencias
publicadas se muestran con fuentes raw y fallback `not_published`; no se
declara “cero lesiones” cuando ESPN devuelve un cuerpo vacío. Ver
`artifacts/phase_100_espn_context_enrichment/phase_100c_report.md`.

## Evidencia 100D

`blocked_by_outcome_coverage` el 2026-07-29. El manifiesto causal contiene 83
fixtures y únicamente referencias capturadas antes de su kickoff, pero ningún
outcome sellado. No se ejecutó ablation ni se promovió una feature. Ver
`artifacts/phase_100_espn_context_enrichment/phase_100d_report.json`.

## Evidencia 100E

`implemented_guarded_not_activated`. Los recursos live/settlement están
clasificados y aislados; la muestra actual no contiene fixtures elegibles live
ni outcomes. Ver `phase_100e_report.md`.

DEC-153 añade un contrato tolerante a ausencia para predictor 1X2 externo y
una curva de presión derivada del PBP. Dos summaries reales no publicaron el
predictor, por lo que el estado normal fue `not_published`; no se fabricó un
fallback desde mercado. Ambos recursos son presentación y no features.

## Evidencia 100F

`validated_isolated` el 2026-07-29. Noticias son contexto editorial sin feature
de modelo y 83 snapshots de odds están archivados sin consumidores en la ruta
predictiva. Ver `phase_100f_report.md`.

`pickcenter` sólo expone disponibilidad financiera aislada. El contrato
visible omite bookmaker, líneas, moneyline y probabilidad implícita.

## Clasificación inicial

`promising_unconfirmed`.
