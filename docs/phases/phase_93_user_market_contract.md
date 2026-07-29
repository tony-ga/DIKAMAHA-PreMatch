# Fase 93 — contrato de mercados para usuario

## Objetivo

Preparar una representación estable y legible de los mercados shadow para la
interfaz, sin declarar promoción oficial.

## Campos

- `key`;
- `metric`;
- `team_side`;
- `period`;
- `line`;
- `probability`;
- `baseline_probability`;
- `source_model`;
- `status`.

La vista es aditiva, conserva `probabilities` y
`baseline_probabilities`, y sólo incluye líneas habilitadas por los artefactos
verificados. Ante fallback Markov, desaparecen únicamente sus líneas.

## Resultado

Estado: `ready_for_next_phase`.

- diez fixtures de replay;
- nueve mercados por fixture;
- probabilidades y baselines equivalentes a los diccionarios originales;
- salida oficial idéntica;
- replay exacto;
- todas las líneas continúan etiquetadas como experimentales.
- suite integral con PostgreSQL: `397 passed`.
