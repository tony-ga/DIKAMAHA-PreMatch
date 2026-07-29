# Fase 85 — integración shadow de mercados agregados

## Objetivo

Conectar al flujo universal los cuatro mercados aprobados en Fase 84A sin
modificar la predicción oficial de goles.

## Contrato

- bloque aditivo: `experimental_team_markets`;
- estado positivo: `experimental_shadow_not_promoted`;
- estado de degradación: `shadow_unavailable`;
- líneas permitidas:
  - `home_corners_over_4_5`;
  - `away_corners_over_4_5`;
  - `away_shots_over_10_5`;
  - `first_half_cards_over_1_5`;
- ninguna otra línea de 84A puede publicarse;
- el catálogo preexistente conserva semántica `read_only`.

## Causalidad

El perfil se reconstruye con el snapshot activo y sólo con partidos cuyo
kickoff sea estrictamente anterior al partido solicitado. El `match_id`
objetivo se excluye explícitamente. Los modelos, pesos y dispersiones provienen
del artefacto congelado de Fase 84A.

## Gates

- campos oficiales idénticos con sidecar habilitado o ausente;
- exactamente cuatro probabilidades, todas en `[0, 1]`;
- cero eventos del partido objetivo;
- fallback seguro ante artefacto ausente o incompatible;
- replay idéntico;
- servicio universal y fixture exponen el mismo contrato;
- suite integral aprobada.

## Promoción

Esta fase no autoriza mercados oficiales. Sólo habilita observación shadow y
la posterior cohorte prospectiva por mercado.

## Resultado

`ready_for_prospective_shadow`

- flujo universal y resolución de fixture exponen el sidecar;
- exactamente cuatro líneas aprobadas y probabilidades válidas;
- diez fixtures de replay con campos oficiales idénticos;
- replay determinista;
- 18,888 observaciones equipo-partido compartidas entre entrenamiento y
  snapshot activo, 132,216 conteos comparados y cero diferencias;
- fallback `shadow_unavailable` probado;
- suite integral PostgreSQL: `373 passed`.

El siguiente paso autorizado es sellar y acumular una cohorte prospectiva por
mercado. La publicación oficial continúa bloqueada.
