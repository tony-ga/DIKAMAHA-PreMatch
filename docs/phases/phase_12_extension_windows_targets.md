# Fase 12 — ventanas y targets de extensión

## Objetivo

Convertir la extensión histórica validada por Fase 11 en ventanas de 15 minutos
y labels `temporal_targets v2`, sin tocar el histórico canónico ni entrenar con
resultados del partido objetivo.

## Resultado

- clasificación: `validated_for_target_revision`;
- 241 partidos, 2,892 ventanas y 48,061 eventos fuente;
- cero discrepancias entre goles de eventos y marcador final;
- cero eventos huérfanos, relojes fuera de rango o solapamientos;
- 21 recuperaciones locales y 16 visitantes a empate o victoria;
- 6 remontadas estrictas locales y 8 visitantes.

Los labels son post-partido y sólo sirven para evaluación. No son features
pre-match y no habilitan mercados.

## Artefactos

- `artifacts/phase_12_extension_windows_targets/event_windows.json`;
- `artifacts/phase_12_extension_windows_targets/target_labels.json`;
- `artifacts/phase_12_extension_windows_targets/audit.json`;
- `artifacts/phase_12_extension_windows_targets/hashes.json`.

## Siguiente gate

Evaluación OOS ampliada con train canónico, confirmación posterior y bootstrap
por partido completo.

# Version: 1.0.0
# Created: 2026-07-26
