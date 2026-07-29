# Fase 23 — captura de contexto pre-match de ESPN

## Objetivo

Medir y materializar de forma reproducible la cobertura de dos fuentes que
pueden mejorar `first_half_goal`: composición de titulares/formación y cuotas
de apertura. La fase es de adquisición y auditoría; no modifica el router ni
promueve mercados.

## Contrato causal

- El identificador y kickoff deben coincidir con la cohorte limpia de Fase 22.
- De `summary.rosters` sólo se conservan titulares, posiciones y formación.
  No se usan estadísticas de jugadores, sustituciones ni datos posteriores.
- De `summary.odds` sólo se conservan mercados del bloque `open` de proveedores
  no live. Se excluyen `current`, `close`, `ESPN BET - Live Odds` y cualquier
  proveedor sin valores de apertura.
- La hora de captura local no se interpreta como hora de publicación histórica.
  Por eso la señal queda como `research_only` hasta que la evaluación OOS
  demuestre robustez y el contrato temporal sea aceptado.
- Los payloads crudos permanecen en la caché local; los artefactos públicos sólo
  contienen hashes, cobertura y variables sanitizadas.

## Gates

- Cobertura completa o clasificación `insufficient_coverage`/`blocked_by_data`.
- Cero desalineaciones entre evento, equipos y kickoff.
- Cero uso de estadísticas del partido objetivo como features.
- Ningún proveedor live ni cuota `current`/`close` entra al dataset candidato.
- La fase no entrena ni promueve modelos si la disponibilidad temporal no es
  defendible.

## Artefactos

`config.json`, `input_manifest.json`, `coverage.json`, `audit.json`,
`context_rows.json`, `hashes.json`, `validation_report.md` y `final_report.md`.
