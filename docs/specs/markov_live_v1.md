# Especificación `markov_live_v1`

## Rol

Markov Live es el baseline dinámico in-play. Parte de las intensidades
estructurales congeladas antes del kickoff y actualiza un posterior de régimen
con marcador, reloj y eventos ya observados. No sustituye Markov pre-match ni
usa resultados futuros.

## Estado y salida

El filtro mantiene cuatro regímenes observables:
`balanced`, `home_pressure`, `away_pressure` y `chaotic`. Las transiciones son
estocásticas y normalizadas; las observaciones actualizan el posterior con
pesos versionados por evento y equipo.

La salida contiene posterior de estado, estado dominante, intensidades de
goles restantes por equipo, mercados derivados del marcador actual, hazards
de próximo evento, auditoría y provenance. Todos los valores deben ser
finitos; las probabilidades normalizan y el tiempo restante nunca es negativo.

## Fallback

Sin eventos válidos, el filtro conserva una evolución temporal hacia el prior.
Sin reloj o marcador confiable no emite mercados live. Si la capa Hawkes está
ausente, Markov Live produce exactamente la misma salida.

## Gate

Permanece `experimental_shadow_not_promoted`. Su gate histórico usa snapshots
pseudo-live de partidos completos, prior walk-forward, selección temporal y
confirmación sellada. Fase 114 lo aprobó sobre 7,400 partidos/34 ligas: delta
objetivo `-0.002259`, IC95% `[-0.002858, -0.001635]` y 84.375% de ligas no
degradadas frente al baseline score/tiempo.

Version: 1.1.0
Created: 2026-08-07; updated: 2026-08-08
