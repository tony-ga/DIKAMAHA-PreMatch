# Fase 78 — transición contextual y duración

## Objetivo

Estimar `P(regime[t+1] | regime[t], opponent_regime[t], liga, localía,
ventana)` manteniendo `style_state` fijo durante el partido.

## Baseline

Transición jerárquica `global → liga+ventana+régimen propio`.

## Candidato

Añade localía y régimen simultáneo del rival con pooling Dirichlet hacia el
baseline. El nivel equipo sólo se conservará si demuestra valor OOS.

## Gate

- mejora relativa de log-loss ≥1% en selección y confirmación;
- más de 50% de masa predictiva contextual;
- error relativo de duración <10%;
- matrices normalizadas y backoff completo;
- estabilidad temporal y por liga;
- ninguna liga con soporte degrada más de `0.06` en esta fase exploratoria
  (`0.01` seguirá siendo obligatorio en Fase 80);
- cero estados futuros en predictores.

## Resultado

`ready_for_next_phase`

| Bloque | Mejora log-loss | Masa contextual | Ligas no negativas | Error duración máx. |
| --- | ---: | ---: | ---: | ---: |
| selección | 1.95% | 54.77% | 93.33% | 5.21% |
| confirmación | 2.16% | 58.98% | 90.91% | 7.05% |

Se seleccionó `alpha=60` antes de confirmación. El nivel equipo fue descartado
por falta de generalización. Fase 79 queda autorizada; el router no cambia.

## Verificación

- parámetros de global, baseline y contexto serializados;
- matrices normalizadas y backoff probado sin soporte;
- régimen rival probado como contexto efectivo;
- replay `ready_for_next_phase`;
- suite completa con PostgreSQL: `344 passed`.
