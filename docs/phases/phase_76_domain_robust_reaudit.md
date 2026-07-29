# Fase 76 — estados robustos entre dominios

## Clasificación final

`rejected_for_revision`

## Candidato congelado

`predictive_latent_state_v3` reemplaza conteos absolutos por 19 emisiones
direccionales: actividad, contraste equipo-rival, cuotas suavizadas y
eficiencias. Añade medias causales de 10 y 15 minutos sin cruzar secuencias.
La regresión está fuertemente regularizada (`C=0.0001`) y genera cuatro estados.

## Selección sin holdout

- spread: `0.051049`;
- NMI temporal: `0.737042`;
- ocupación mínima: `23.029%`;
- estabilidad: `29/30` ligas;
- duración explícita: `+0.116564` NLL;
- permutación: `p=0.004975`.

Todos los criterios internos se cumplieron antes de evaluar el holdout.

## Holdout sellado

- 376 partidos utilizables de 9 ligas;
- cero solapamiento con los 9,444 partidos de Fase 74;
- spread: `0.042423`, frente a `0.034152` del v2;
- ocupación mínima: `20.693%`;
- estabilidad: `5/6` ligas con soporte;
- duración explícita: `+0.180466` NLL.

El candidato mejora la generalización semántica, pero no alcanza spread
`0.05` ni cobertura de 10 ligas. Por tanto no desbloquea Fase 77.

## Infraestructura corregida

El conector ESPN 1.2 usa `summary/commentary` como fallback raw-first cuando
Core `/plays` está vacío. El fallback conserva el summary completo y resuelve
IDs de equipo exclusivamente desde el header del mismo payload.

## Decisión

El holdout ha sido observado por v2 y v3 y queda clausurado para selección.
No se permiten más cambios de features, estados o umbrales basados en sus
resultados. La línea Markov v4 sólo puede reabrirse con una cohorte prospectiva
nueva que alcance cobertura causal suficiente.
