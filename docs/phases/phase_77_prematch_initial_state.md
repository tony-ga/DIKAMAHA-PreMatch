# Fase 77 — estado inicial pre-match

## Objetivo

Predecir la distribución de los cuatro estados iniciales de 76R sin observar
eventos del partido objetivo.

## Entradas permitidas

- localía y liga conocidas antes del kickoff;
- aperturas y perfiles agregados de partidos estrictamente anteriores;
- soporte, antigüedad y máscara de disponibilidad;
- contexto ESPN causal cuando exista.

El estado observado de la primera microventana es exclusivamente el target.

## Comparador

Prior jerárquico rolling `global → liga+localía`, estimado sólo con partidos
anteriores al bloque evaluado.

## Gate

- mejora relativa de log-loss ≥1% frente al prior;
- Brier no empeora;
- ECE no empeora;
- mejora presente en selección y confirmación temporal;
- modo core disponible con backoff aunque un equipo no tenga historia;
- cero eventos del partido objetivo en features.

## Artefactos

Contrato estándar más predicciones OOS, parámetros serializados, auditoría de
cutoff y cobertura por nivel `core/contextual/lineup_confirmed`.

## Primera ejecución

`rejected_for_revision`

| Bloque | Modelo log-loss | Baseline | Mejora relativa |
| --- | ---: | ---: | ---: |
| selección | 0.900988 | 0.892492 | -0.95% |
| confirmación | 0.932138 | 0.921090 | -1.20% |

Brier y ECE también empeoran. Añadir el historial explícito de aperturas del
equipo no resuelve el problema: con shrinkage débil sobreajusta y con shrinkage
fuerte converge al prior liga+localía.

La semántica 76R separa riesgo futuro una vez observada actividad, pero el
estado de apertura no contiene suficiente estilo persistente pre-match. Fase
78 no queda autorizada. La revisión debe incorporar perfiles históricos
causales al espacio de estado o separar `style_state` de `match_regime`.

## Revisión dual

`ready_for_next_phase`

La representación `style_state(2) × match_regime(3)` produce seis estados.
El estilo se congela antes del kickoff y el régimen evoluciona con las
emisiones causales del partido.

| Fold | Spread | NMI | Ocupación mínima | Ligas estables | Mejora state_0 |
| --- | ---: | ---: | ---: | ---: | ---: |
| selección | 0.064109 | 0.892442 | 6.34% | 100% | 46.75% |
| confirmación | 0.064365 | 0.891485 | 5.99% | 95.45% | 46.59% |

Brier y ECE mejoran en ambos bloques. Fase 78 queda autorizada; la mejora no
autoriza promoción y deberá superar el comparador tabular same-data en Fase 80.

## Verificación

- 340,740 asignaciones serializadas para Fase 78;
- parámetros de estilo y régimen portables, sin objetos de entrenamiento;
- replay `ready_for_next_phase`;
- suite completa con PostgreSQL: `341 passed`;
- migración 013 aplicada para el orden causal de `events_timeline`.
