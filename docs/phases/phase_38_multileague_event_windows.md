# Fase 38 — ventanas multi‑liga de 15 minutos

## Resultado

La materialización leyó staging en SELECT-only y produjo 111,528 ventanas
para 9,294 partidos utilizables de 42 ligas. Cada partido válido aporta 12
filas, dos equipos por seis ventanas de 15 minutos.

## Exclusiones auditadas

- 30 partidos incompletos sin marcador final.
- 438 partidos con marcador pero sin timeline ESPN usable.
- 13 partidos con timeline parcial que no reconcilia sus goles con el marcador.
- Tandas de penales detectadas por la notación ESPN `x(n)` y excluidas de los
  goles del tiempo reglamentario/extra.

## Variables materializadas

Se conservan identidad multi‑liga, temporada derivada del kickoff, competición,
localía, marcador al inicio, goles, tiros, tiros a puerta, tiros bloqueados,
corners, faltas, tarjetas, sustituciones, presión, presión concedida y
auditoría de eventos desconocidos/anulados/sin equipo.

La etiqueta `foul` se recupera desde `event_type_raw` sin modificar el staging.
Los eventos auxiliares restantes permanecen contabilizados como
`unclassified` y no se inventan como variables de juego.

## Gate

`validated_for_multileague_labeling_with_exclusions`. El corpus limpio queda
listo para etiquetado de estados; todavía no se entrenó Markov ni se modificó
el router oficial.

