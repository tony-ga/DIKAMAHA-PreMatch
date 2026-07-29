# Fase 89 — integración shadow de Markov por mercado

## Objetivo

Exponer en el flujo universal los mercados que superaron los gates corregidos
de Fases 84A/88 bajo semántica comercial de tiros.

## Contrato

- bloque aditivo existente: `experimental_team_markets`;
- cuatro líneas Markov permitidas:
  - `away_shots_second_half_over_5_5`;
  - `home_corners_second_half_over_2_5`;
  - `home_shots_first_half_over_5_5`;
  - `home_shots_second_half_over_5_5`;
- las otras nueve líneas Markov están prohibidas;
- modelo serializado, hash verificado y cutoff explícito;
- fallback seguro conserva las líneas 84A;
- salida oficial de goles idéntica.

## Gates

- exactamente nueve probabilidades shadow cuando ambos artefactos están sanos;
- exactamente cinco probabilidades 84A si Markov no está disponible;
- kickoff estrictamente posterior al cutoff de entrenamiento;
- probabilidades dentro de `[0, 1]`;
- cero datos del partido objetivo;
- replay idéntico;
- suite integral con PostgreSQL aprobada.

## Artefactos

Directorio: `artifacts/phase_89_team_market_markov_integration/`.

## Resultado

Estado: `ready_for_next_phase`.

- diez fixtures de replay;
- nueve mercados exactos: cinco de Fase 84A y cuatro de Fase 88;
- probabilidades válidas y cutoff causal;
- modelo Markov serializado con hash verificado;
- fallback conserva exactamente las cinco líneas 84A;
- campos oficiales idénticos con sidecar habilitado/deshabilitado;
- replay doble idéntico;
- router de goles intacto.
- suite integral vigente con PostgreSQL: `397 passed`.

El siguiente paso permitido es observación shadow de las cuatro líneas Markov.
No se habilitan apuestas oficiales ni se modifica la cohorte Fase 86.
