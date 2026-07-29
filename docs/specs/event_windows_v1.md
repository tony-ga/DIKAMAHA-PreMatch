# Especificación `event_windows v1`

## Propósito

Materializar observaciones históricas por partido, equipo y ventana de 15 minutos para etiquetado de estados y calibración Markov. No es un dataset de inferencia del partido objetivo.

## Grano y clave

Una fila por `(match_id, team_id, window_index)`. Las ventanas son `[0,15)`, `[15,30)`, `[30,45)`, `[45,60)`, `[60,75)` y `[75,90+]`.

## Campos mínimos

- Identidad: `match_id`, `team_id`, `opponent_team_id`, `is_home`, `competition_id`, `season`.
- Tiempo: `window_index`, `window_start_minute`, `window_end_minute`, `period`.
- Contexto al inicio: `score_for_start`, `score_against_start`, `goal_difference_start`.
- Eventos válidos de la ventana: goles, tiros, tiros a puerta, tiros bloqueados, corners, amarillas, rojas, faltas y sustituciones.
- Variables derivadas: tiros concedidos, corners concedidos, presión, presión concedida, agresividad y comportamiento defensivo.
- Calidad: `event_coverage`, `unknown_event_count`, `annulled_event_count`, `null_team_event_count`, `source_hash`, `window_version`.

## Reglas temporales

- Incluir sólo eventos válidos cuyo timestamp pertenezca a la ventana.
- Excluir anulados de variables predictivas; conservarlos en auditoría.
- No usar información de `t+1` para etiquetar o describir `t`.
- Preservar el evento sin equipo como auditoría y no asignarlo silenciosamente.

## Variables derivadas v1

- `pressure = shots + shots_on_target + shots_blocked + corners`.
- `pressure_conceded` se calcula desde eventos válidos del rival.
- `aggression` y `defensive_behavior` no se materializan hasta que `state_labeling v1` congele sus fórmulas.

## Gates

- Cada ventana debe tener límites temporales y orientación verificables.
- No debe haber eventos duplicados ni fuera de ventana.
- La cobertura se reporta por competición, temporada, equipo y tipo de evento.
- La corrida debe ser determinista para el mismo ledger de eventos.
