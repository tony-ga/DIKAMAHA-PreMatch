# Fase 40 — calibración Markov multi‑liga

## Resultado

Se calibraron 92,940 transiciones de 9,294 partidos y 41 ligas con partición
temporal por partido: 5,576 desarrollo, 1,858 validación y 1,860 confirmación.

El modelo usa backoff `team → league_context → window → global`, smoothing
Dirichlet y conserva `league_slug` en cada contexto. El `competition_id` ESPN
no se usa como categoría porque identifica el partido individual.

- log-loss validación: `0.776450`.
- log-loss confirmación: `0.765990`.
- matrices normalizadas y particiones sin solapamiento.
- targets, router y modelo oficial no modificados.

La fase queda lista para simulación experimental; esto no constituye todavía
validación de mercados ni promoción del modelo.

