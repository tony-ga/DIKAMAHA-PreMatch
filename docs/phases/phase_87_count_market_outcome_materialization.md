# Fase 87 — materialización de outcomes de mercados

## Objetivo

Obtener targets post-match para la cohorte sellada de Fase 86 sin recalcular
ni sobrescribir ninguna predicción.

## Fuentes y temporalidad

- summary ESPN para estado final, identidad y boxscore;
- plays Core paginado para tarjetas de primera mitad;
- ambos payloads se guardan en `raw_responses` antes del parseo;
- no se consulta un fixture hasta `kickoff + 3h`;
- una captura no final se conserva, pero no produce outcome.

## Targets

- `home_corners_over_4_5`;
- `away_corners_over_4_5`;
- `away_shots_over_10_5`;
- `first_half_cards_over_1_5`.

Corners y tiros se leen del boxscore orientado. Tarjetas 1T se derivan de
amarillas válidas anteriores a 45:00. No se imputan datos faltantes.

## Gates

- identidad y orientación exactas;
- estado ESPN final/completado;
- cuatro outcomes disponibles;
- summary y eventos reconciliados;
- escritura append-only e idempotente;
- hashes de las predicciones de Fase 86 intactos;
- scoring global bloqueado hasta completar los 523 partidos.

## Estado inicial

`insufficient_coverage`. El primer kickoff de la cohorte ocurre el 29 de julio
de 2026; antes de ese momento el colector debe devolver cero outcomes sin
realizar llamadas post-match.

## Primera ejecución

`insufficient_coverage`

- 523 predicciones preservadas;
- 0 fixtures elegibles al aplicar `kickoff + 3h`;
- 0 llamadas post-match nuevas;
- 0 outcomes y 0 rechazos;
- hash de predicciones idéntico antes/después;
- scoring no ejecutado;
- parser summary/plays y paginación aprobados;
- suite integral PostgreSQL: `379 passed`.

El colector se comportó como exige el contrato: antes del primer settlement no
consultó datos futuros ni fabricó outcomes.
