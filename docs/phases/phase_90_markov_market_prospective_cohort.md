# Fase 90 — cohorte prospectiva Markov por mercado

## Objetivo

Congelar antes del kickoff las cuatro líneas Markov integradas en Fase 89,
sin recalcular ni modificar la cohorte agregada de Fase 86.

## Fuente de fixtures

Fase 86 se usa únicamente como catálogo pre-match de liga, identidad,
orientación y kickoff. Sólo entra un fixture si `captured_at < kickoff`.
No se leen outcomes, summary, estadísticas ni play-by-play.

## Mercados

- `away_shots_second_half_over_5_5`;
- `home_corners_second_half_over_2_5`;
- `home_shots_first_half_over_5_5`.
- `home_shots_second_half_over_5_5`.

## Persistencia

- store aislado `data/phase_90/markov_market_cohort.sqlite`;
- unicidad `league_slug + match_id`;
- inserción append-only;
- probabilidades Markov y baseline congeladas juntas;
- hash del modelo y snapshot persistidos.

## Gates

- al menos 500 partidos y 10 ligas;
- exactamente cuatro probabilidades válidas por fixture;
- captura estrictamente anterior al kickoff;
- un único hash de modelo;
- cero outcomes o endpoints post-match;
- replay idempotente;
- router oficial intacto.

## Siguiente fase

Fase 91 materializa outcomes sólo desde `kickoff + 3h`, contando corners y
tiros por equipo/mitad desde play-by-play y reconciliando sus totales contra
el boxscore.

## Resultado

Estado: `ready_for_next_phase`.

- 520 predicciones congeladas antes del kickoff;
- 18 ligas;
- cuatro probabilidades Markov y cuatro baselines por partido;
- un único hash del modelo comercial corregido;
- tres fixtures ya iniciados quedaron excluidos;
- cero outcomes y cero endpoints post-match;
- replay append-only e idéntico.
- suite integral con PostgreSQL: `397 passed`.
