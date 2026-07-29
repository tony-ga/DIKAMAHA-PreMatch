# Fase 59 — auditoría de calidad de eventos

## Resultado

Se auditó el snapshot activo `phase57_incremental_v1_20260727` sin modificarlo
ni entrenar modelos.

- 117,012 filas auditadas.
- 9,751 partidos y 42 ligas.
- 0 fallos estructurales por partido.
- 0 partidos sin eventos observados en el agregado.
- 537,834 eventos clasificados/observados.
- 756,821 eventos no clasificados.
- Proporción no clasificada: `58.46%`.
- 6,391 partidos tienen al menos 50% de eventos no clasificados.
- 27,778 eventos no tienen equipo asignado.
- Los timestamps crudos no están disponibles localmente para auditar precisión,
  huecos y orden temporal.

## Gate

La estructura de `event_windows v1` es íntegra: cada partido conserva sus 12
filas esperadas y no hay duplicados estructurales. Sin embargo, el gate
semántico queda bloqueado. No se puede entrenar el Markov residual con este
corpus hasta recuperar los timelines crudos, identificar el origen de los
eventos no clasificados y recalcular la cobertura sin duplicar contadores por
equipo.

La mayor proporción no clasificada aparece en `mex.1` (`64.35%`), seguida por
`uefa.weuro` (`64.27%`) y `uefa.wchampions` (`63.66%`).

## Auditoría raw ESPN

Se recuperó una cohorte estratificada de 30 partidos. ESPN permitió normalizar
15 de ellos; los 15 restantes fallaron principalmente porque el endpoint de
plays rechazó referencias históricas o agotó timeout. En los 15 válidos se
observaron 1,893 eventos:

- 1,304 quedaron inicialmente como `unclassified`.
- 15/15 marcadores reconciliaron con las ventanas agregadas.
- 15/15 conservaron timestamps UTC, no futuros y ordenados.
- Los tipos raw dominantes fueron `throw_in`, `free_kick`, `foul`,
  `blocked_pass`, `save`, `offside` y `assists_shot`.

Esto cambia el diagnóstico: la proporción desconocida no representa
necesariamente eventos faltantes. En gran parte representa una taxonomía
normalizada que no asigna todavía categorías ESPN válidas a una variable de
ventana. El gate sigue bloqueado, pero la corrección prioritaria es ampliar y
clasificar la taxonomía antes de endurecer el filtro de partidos.

## Corrección de taxonomía v1.1

Se centralizó la clasificación en `src/espn_event_taxonomy.py` y se aplicó a
las rutas de ingesta v2, staging heredada, parser ESPN y materialización
multi-liga. Los eventos ESPN reconocibles que no son señales directas quedaron
como `auxiliary`; Markov mantiene una lista cerrada y no los consume.

La cohorte raw se reprocesó sin cambiar el snapshot activo:

- 1,893 eventos normalizados en 15 partidos válidos.
- 1,096 eventos `auxiliary` y 797 eventos modelables.
- 0 eventos `unclassified`.
- 15/15 marcadores reconciliados.
- timestamps UTC, no futuros y ordenados.

La taxonomía está validada en la cohorte raw. El gate global permanece abierto
sólo para rematerialización aislada: el snapshot activo aún contiene la
clasificación anterior y no se ha modificado ningún modelo ni router.

## Consecuencia

- No se modifica el router.
- No se activa Kalman.
- No se entrena Markov residual.
- No se descarta todavía todo el corpus histórico: primero se debe auditar la
  taxonomía de eventos crudos y determinar qué categorías son irrelevantes,
  recuperables o verdaderamente faltantes.

## Siguiente paso

Rematerializar un snapshot candidato aislado con la taxonomía v1.1 y comparar
contra `phase57_incremental_v1_20260727` goles, tiros, corners, faltas,
tarjetas, sustituciones, estados y hashes. Después se repetirá el gate global
y se decidirá si la cohorte queda habilitada para la calibración Markov.

La auditoría raw y la especificación quedaron en
`artifacts/phase_59_raw_timeline_audit_v1/`; sus payloads no se publican como
artefactos finales. La especificación es
`docs/specs/espn_event_taxonomy_v1_1.md`.
