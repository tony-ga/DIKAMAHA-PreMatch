# Fase 47 — catálogo de reutilización multi-modelo

## Hallazgo

El gate prospectivo sólo consultaba los IDs reutilizados por el router
histórico. La cohorte multi-liga almacenada en `prospective_staging_v2` podía
aparecer como independiente aunque ya hubiera sido usada por la calibración o
la evaluación OOS multi-liga.

## Corrección

El gate v2 une el catálogo oficial con todos los IDs de
`phase_38_multileague_event_windows_v1/event_windows.json` antes de clasificar
candidatos. La comprobación sigue siendo SELECT-only y no genera métricas ni
modifica el router.

## Resultado

Antes de la corrección, 1,801 partidos pasaban el gate. La auditoría de
reutilización mostró que 1,791 ya estaban en las predicciones de Fase 42 y
1,794 en las ventanas multi-liga; por tanto no eran una cohorte independiente
para ese modelo. Con el catálogo ampliado, sólo 7 registros superan las
exclusiones, por debajo del mínimo de 30, y la cohorte queda bloqueada hasta
que existan partidos posteriores al corpus y ausentes de todo ajuste y
evaluación.

Clasificación esperada: `waiting_for_new_independent_cohort`.

Como parte del cierre se corrigió la dependencia downstream: Fases 32 y 33
ahora respetan la clasificación del gate y Fases 34 y 35 permanecen en espera
sin generar artefactos de predicción ni scoring cuando no existe una cohorte
aprobada.
