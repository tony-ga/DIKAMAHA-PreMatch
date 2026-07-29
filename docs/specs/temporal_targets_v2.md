# Especificación `temporal_targets v2`

## Propósito

Definir targets temporales pre-match evaluables con denominadores explícitos y
separar la reacción de un equipo de la remontada estricta. Esta versión es de
investigación; no habilita mercados ni modifica `market_targets v1`.

## Cohortes

- `canonical_v1`: 381 partidos de `event_windows v1`.
- `staging_extension_candidate`: 44 partidos completos de
  `prospective_staging_v2`, incorporados sólo como extensión candidata.
- `combined_audit_candidate`: unión de ambas cohortes, 425 partidos, sin
  reutilizar sus partidos en los folds OOS ya congelados.

## Definiciones congeladas para auditoría

Las ventanas son `[0,15)`, `[15,30)`, `[30,45)` y `[45,60)`, `[60,75)`,
`[75,90+]`. El descanso se obtiene después de la ventana 2 y el marcador
final después de la ventana 5.

### Targets de tiempo

- `first_half_goal`: existe al menos un gol en ventanas 0–2.
- `second_half_goal`: existe al menos un gol en ventanas 3–5.
- `first_half_goal_count`: número de goles en ventanas 0–2.
- `second_half_goal_count`: número de goles en ventanas 3–5.

### Targets de reacción condicionada

Sólo existe una oportunidad cuando el equipo va perdiendo al descanso.

- `home_recovery_draw_or_win`: el local pierde al descanso y termina empatando
  o ganando.
- `away_recovery_draw_or_win`: el visitante pierde al descanso y termina
  empatando o ganando.
- `home_reaches_level_after_half`: el local pierde al descanso y alcanza al
  rival en alguna ventana 3–5.
- `away_reaches_level_after_half`: equivalente para el visitante.

### Diagnóstico, no target de promoción

- `home_comeback_win`: el local pierde al descanso y gana al final.
- `away_comeback_win`: el visitante pierde al descanso y gana al final.

La remontada estricta se conserva para medir el objetivo original, pero no se
usará para justificar promoción mientras su soporte confirmatorio sea escaso.

## Reglas de evaluación

- El denominador de cada reacción es su número de oportunidades, no el total de
  partidos.
- La unidad IID es el partido completo; nunca se bootstrappean ventanas como
  si fueran partidos independientes.
- Los targets sólo son labels post-match; no pueden entrar como features del
  mismo partido.
- Una futura promoción exige partición temporal nueva, baseline propio,
  calibración, log-loss por partido, bootstrap y auditoría de leakage.
- La salida de esta especificación es `research_only` hasta que exista una
  evaluación confirmatoria independiente.

## Gate de salida

La especificación queda lista para una evaluación v2 sólo si la extensión
conserva identidad, marcador, timestamps, eventos nulos/anulados y hashes; no
se permite reabrir Markov antes de ese gate.

# Version: 1.0.0
# Created: 2026-07-26
