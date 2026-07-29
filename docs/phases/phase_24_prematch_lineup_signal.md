# Fase 24 — señal pre-match de alineaciones

## Objetivo

Evaluar si la composición de titulares, formación y continuidad histórica
añaden señal para `first_half_goal`. Se compara una variante de alineaciones
sola y una fusión con el ritmo histórico de Fase 22.

## Exclusiones

Las cuotas no entran al modelo: sólo hay 10 observaciones `open` en los 241
partidos de confirmación. No se usan estadísticas de jugadores del partido
objetivo, sustituciones, marcador ni eventos posteriores.

## Gate

La promoción requiere mejora de la fusión frente al baseline con intervalo
bootstrap estrictamente positivo en la confirmación independiente. La fase no
promueve mercados ni cambia el router automáticamente.
