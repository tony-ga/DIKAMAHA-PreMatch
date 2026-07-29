# Fase 58 — auditoría del rebaseline Dixon-Coles + Kalman

## Resultado

Se auditó el OOS canónico existente y el dry-run real de Kalman sin entrenar ni
activar una nueva versión.

En el OOS canónico de 66 partidos de confirmación, Dixon-Coles + Kalman no
supera a Dixon-Coles en todos los mercados. El dry-run de Kalman muestra señales
diagnósticas, pero no sustituye la evidencia confirmatoria.

Por tanto:

- no se activa Kalman automáticamente;
- no se promueve Markov;
- el router oficial no cambia;
- el baseline reforzado debe congelarse por fold antes de una nueva cohorte.

## Siguiente trabajo autorizado

1. Auditar calidad de timelines y cobertura causal.
2. Bloquear parámetros DC/Kalman sin mirar confirmación.
3. Crear una cohorte independiente no reutilizada.
4. Implementar sólo el residual Markov para `first_half_goal`.
5. Evaluar el estado inicial y el gate de entropía en validación.

La especificación queda en
`docs/specs/markov_residual_selective_v1.md`.

