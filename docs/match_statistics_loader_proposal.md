# Propuesta de loader para `match_statistics`

## Objetivo
Construir una rutina de lectura y reconciliación que:

- lea el summary de ESPN;
- aplique `source_priority`;
- calcule métricas derivadas del play-by-play sólo como auditoría o fallback;
- no modifique `events_ledger` ni `events_timeline`;
- empiece en modo `--dry-run`.

## Validaciones previas obligatorias

Antes de producir cualquier fila, el loader debe abortar si no puede confirmar todo lo siguiente:

- correspondencia exacta `espn_event_id -> match_id`;
- coincidencia de equipos;
- coincidencia de orientación home/away;
- mapeo completo `teams.espn_team_id -> teams.id`;
- todos los equipos requeridos están presentes en el catálogo interno;
- summary y play-by-play pertenecen al mismo evento.

## Flujo propuesto

1. Cargar configuración desde `.env`.
2. Resolver `match_id` y `espn_event_id`.
3. Descargar `summary` de ESPN.
4. Descargar `play-by-play` consolidado.
5. Construir métricas derivadas desde play-by-play.
6. Reconciliar por `source_priority`.
7. Generar una propuesta de filas `match_statistics`.
8. En `--dry-run`, imprimir:
   - filas candidatas;
   - conflictos detectados;
   - valores finales por métrica;
   - métricas sólo de auditoría.

## Regla de precedencia

- `espn_summary` tiene prioridad 1.
- `derived_play_by_play` tiene prioridad 2.
- Si ambas fuentes difieren:
  - conservar ambos valores;
  - `has_conflict = true`;
  - el valor final debe venir de `espn_summary`.

## Evolución de reconciliación

- `v1`: esquema histórico, sin campos de calidad.
- `v2`: mantiene trazabilidad conservadora de conflicto y revisión.
- `v3`: agrega diferencias explícitas por métrica y separa conflicto esperado de conflicto crítico.

### `v3`

Para `v3`, `conflict_details` debe incluir:

- `metric_conflicts`: mapa por métrica con:
  - `summary`
  - `derived`
  - `difference`
  - `severity`

La severidad admitida es:

- `none`
- `low`
- `critical`

Regla operativa:

- `accepted`:
  - identidad validada;
  - summary completo;
  - no hay conflicto crítico;
  - pueden existir diferencias semánticas esperadas con `has_conflict = true`.
- `needs_review`:
  - identidad incierta;
  - falta una métrica obligatoria del summary;
  - el evento es correcto pero la reconciliación requiere revisión.
- `rejected`:
  - evento incorrecto;
  - equipos, orientación o fecha incompatibles;
  - datos imposibles.

## Política de confianza

La confianza no debe reducirse a un único número fijo. El loader debe separar:

- `source_confidence`: calidad intrínseca del `espn_summary`.
- `reconciliation_confidence`: confianza en la reconciliación final entre summary y play-by-play.
- `has_conflict`: indica diferencia entre fuentes.
- `needs_review`: marca casos que requieren revisión humana o bloqueo según política.

Reglas:

- `has_conflict = false`:
  - `source_confidence` alta;
  - `reconciliation_confidence` alta.
- `has_conflict = true` con summary completo y conflicto no crítico:
  - `source_confidence` alta;
  - `reconciliation_confidence` ligeramente menor que un caso limpio;
  - conservar `espn_summary` como valor final;
  - no usar `0.99` de forma automática.
- conflicto semántico esperado en `v3`:
  - `has_conflict = true`;
  - `needs_review = false`;
  - `reconciliation_status = accepted`;
  - `conflict_details.metric_conflicts` conserva la diferencia.
- conflicto crítico:
  - `reconciliation_confidence` baja;
  - `needs_review = true`;
  - el loader debe abortar o dejar la fila fuera de persistencia si la política lo exige.
- métrica no disponible:
  - `source_confidence` o `reconciliation_confidence` deben bajar;
  - la ausencia debe ser explícita en la salida de dry-run.

## Política de conflicto

- `has_conflict = false`:
  - la fila puede insertarse.
- conflicto no crítico:
  - se conserva;
  - se marca;
  - se usa `espn_summary` como valor final.
- conflicto crítico en marcador, equipos, fecha u orientación:
  - abortar;
  - no insertar.
- conflicto crítico en una métrica agregada:
  - no insertar hasta resolverlo;
  - salvo que la política explícita permita usar `espn_summary` como fuente primaria y mantenga trazabilidad completa en JSONB.

## Versión de reconciliación

- `v1` conserva el esquema histórico.
- `v2` debe usarse para persistir `source_confidence`, `reconciliation_confidence`, `needs_review`, `conflict_details` y `reconciliation_status`.
- `v3` habilita `metric_conflicts` explícitos y la política de `accepted` con conflicto semántico esperado.
- El loader debe rechazar persistencia de esos campos si `reconciliation_version` no coincide con la versión soportada por el modo seleccionado.

## Estructura de salida

Cada fila candidata debe incluir:

- `match_id`
- `team_id`
- `source`
- `reconciliation_version`
- `shots_total`
- `shots_on_target`
- `fouls`
- `yellow_cards`
- `red_cards`
- `corners`
- `saves`
- `possession_pct`
- `goals`
- `var_annulled_events`
- `source_event_id`
- `source_fetched_at`
- `created_at`
- `reconciled_at`
- `has_conflict`
- `primary_source`
- `fallback_source`
- `confidence`
- `source_confidence`
- `reconciliation_confidence`
- `needs_review`
- `espn_summary` JSONB
- `derived_play_by_play` JSONB

## Pseudocódigo

```python
summary = fetch_summary(espn_event_id)
plays = fetch_play_by_play(espn_event_id)
derived = derive_metrics_from_plays(plays)
final = reconcile(summary, derived, source_priority=["espn_summary", "derived_play_by_play"])
if dry_run:
    log(final)
else:
    persist(final)
```

## Verificación

- Una fila por `match_id`, `team_id`, `source`, `reconciliation_version`.
- `possession_pct` entre 0 y 100.
- Métricas no negativas.
- `derived_play_by_play` y `espn_summary` siempre conservados para auditoría.
- `reconciliation_version` debe persistirse en cada fila.
- `--dry-run` debe imprimir validaciones, conflictos y filas candidatas sin escribir nada.
