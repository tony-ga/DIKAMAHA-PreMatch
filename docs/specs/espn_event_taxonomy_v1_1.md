# Especificación de taxonomía ESPN v1.1

## Objetivo

Reducir falsos `unclassified` sin convertir cualquier jugada ESPN en una
señal para Markov. La taxonomía conserva el tipo original en
`event_type_raw`, clasifica la jugada y deja el comportamiento de Markov
cerrado sobre su lista explícita de eventos permitidos.

## Categorías

| Categoría | Tipos | Uso |
| --- | --- | --- |
| Modelable | `goal`, `shot_on_target`, `shot_off_target`, `shot_blocked`, `corner`, `foul`, `yellow`, `red`, `substitution`, `penalty_awarded`, `penalty_scored` | Puede alimentar ventanas; Markov sólo usa los tipos de `EVENT_TYPES_ALLOWED`. |
| Auxiliar válido | `auxiliary` | Se conserva para cobertura/provenance y cuenta como evento observado, pero no genera una señal Markov. |
| Desconocido | `unclassified` | Requiere investigación; no se usa para señales ni se descarta silenciosamente. |

## Mapeos ESPN v1.1

- `goal___header`, `own_goal` y cualquier `scoringPlay=true` → `goal`.
- `yellow-card`, `red-card`, `corner-awarded`, variantes de tiros y
  `penalty---scored` → su tipo canónico modelable.
- `throw-in`, `free-kick`, `blocked-pass`, `save`, `offside`, `assist`,
  `assists-shot`, `handball`, acciones de pase/duelo (`pass`, `clear`, `cross`,
  `tackle`, `aerial`, `interception`), eventos de control temporal y decisiones
  VAR → `auxiliary`.
- Una etiqueta no presente en el catálogo permanece `unclassified`.

## Gate de adopción

La cohorte raw de Fase 59 v1.1 produjo 1,893 eventos en 15 partidos válidos:
1,096 auxiliares, 797 modelables, 0 desconocidos, timestamps válidos y
marcadores reconciliados en 15/15 partidos. Esto valida la taxonomía, pero no
autoriza todavía el entrenamiento: primero se debe rematerializar un snapshot
aislado, repetir el gate global y comprobar que no cambian indebidamente goles,
tiros, corners, faltas, tarjetas, sustituciones ni estados Markov.

## Compatibilidad

- `event_windows_v1` acepta `auxiliary` sólo para cobertura observada.
- `markov_v1.EVENT_TYPES_ALLOWED` no incluye `auxiliary`.
- Las rutas v2, staging heredada, parser ESPN y materialización multi-liga usan
  la misma clasificación centralizada.

Version: 1.1.0
Created: 2026-07-27
