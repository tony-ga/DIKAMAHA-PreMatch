# Fase 76 — reauditoría de calidad de ingesta PostgreSQL

## Resultado

`coverage_gate_recovered_model_gate_failed`

La auditoría separó defectos de normalización de ausencia real de datos. El
payload crudo y las filas fuente de PostgreSQL se conservaron sin cambios; la
reconciliación corregida se aplica en la materialización derivada y es
reversible.

## Hallazgos

- El inventario PostgreSQL actual contiene `10,221` partidos completos de 42
  ligas. La rematerialización admite `9,775`, excluye `438` sin timeline y
  conserva fuera sólo `8` discrepancias reales; produce `117,300` ventanas.
- El fallback `summary.commentary` entregaba equipos con `team.id`, pero el
  parser sólo aceptaba `$ref`; ahora admite ambas identidades explícitas.
- Cinco partidos del holdout se recuperan con evidencia del proveedor:
  goles duplicados, progresión repetida del marcador o tanda de penaltis.
- La cohorte útil pasa de `376/9` a `381/10`, por lo que satisface el mínimo
  nominal de `200 partidos/10 ligas`.
- `396/777` partidos continúan excluidos. La mayoría no contiene eventos y los
  restantes no reconcilian; no se fabricaron goles ni timestamps.
- El candidato v3 congelado obtiene spread `0.042241`, menor que el gate
  `0.05`; estabilidad de orden `5/6` ligas y mejora de duración `+0.179853`.

## Controles incorporados

- Una exclusión semántica sólo se acepta si el conteo final coincide
  exactamente por local y visitante.
- Si la reconciliación propuesta no cierra el marcador, se revierte completa.
- Dos progresiones distintas se conservan aunque ESPN redondee ambas al mismo
  minuto.
- Los datos crudos nunca se actualizan o eliminan.
- Los goles ausentes permanecen ausentes; el marcador final no se convierte
  en una secuencia sintética.

## Decisión

La causa de `insufficient_coverage` del holdout histórico queda corregida, pero
Fase 76 no está aprobada: el criterio semántico principal sigue fallando. El
holdout permanece cerrado para selección y el lock prospectivo v3 conserva su
cutoff y hash. La única confirmación válida para promoción sigue siendo la
cohorte prospectiva posterior al lock.

## Verificación

- suite completa: `337 passed`;
- integraciones PostgreSQL opt-in: ejecutadas;
- rematerialización global: `validated_for_multileague_labeling_with_exclusions`.
