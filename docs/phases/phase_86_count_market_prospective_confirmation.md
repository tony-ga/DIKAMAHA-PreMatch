# Fase 86 — confirmación prospectiva de mercados agregados

## Objetivo

Congelar predicciones reales antes del kickoff y evaluar posteriormente,
sin selección adicional, las cuatro líneas integradas en Fase 85.

## Colección

- fuente de fixtures: scoreboard ESPN documentado;
- persistencia raw-first en `raw_responses`;
- sólo eventos `pre`, con identidad home/away completa;
- una predicción inmutable por `league_slug + match_id`;
- modelo y prior liga/localía congelados en el mismo timestamp;
- cero summary, estadísticas, plays u outcomes durante la colección.

## Cobertura mínima

- 500 partidos completos;
- 10 ligas;
- al menos 30 partidos por liga elegible;
- las cuatro probabilidades presentes y válidas;
- modelo y hashes sin cambios durante toda la cohorte.

## Evaluación sellada

Después de completar la cobertura se obtienen targets post-match y se puntúa
por partido:

- log-loss;
- Brier;
- calibración;
- mejora por liga;
- bootstrap pareado de 10,000 remuestras de partidos.

Un mercado sólo puede avanzar si la mejora de log-loss frente al prior tiene
IC95% completamente positivo, Brier no peor y al menos 70% de ligas elegibles
no negativas. Los mercados se promueven o rechazan individualmente.

## Estado inicial

`insufficient_coverage` hasta materializar 500 partidos/10 ligas. Esto no
afecta el uso shadow ni la salida oficial de goles.

## Resultado de colección

`ready_for_next_phase`

- 523 predicciones congeladas;
- 18 ligas;
- 1,302 scoreboards persistidos raw-first;
- cuatro probabilidades de modelo y cuatro del baseline por fixture;
- un único hash de modelo y un único hash de snapshot;
- todos los timestamps de captura anteriores al kickoff;
- replay append-only: cero sobrescrituras;
- cero outcomes leídos y cero endpoints post-match consultados;
- nueve fixtures excluidos explícitamente por historia insuficiente;
- suite integral PostgreSQL: `376 passed`.

La cohorte está sellada. Los partidos abarcan del 29 de julio al 27 de agosto
de 2026; el scoring no puede ejecutarse hasta que los outcomes correspondientes
estén disponibles.
