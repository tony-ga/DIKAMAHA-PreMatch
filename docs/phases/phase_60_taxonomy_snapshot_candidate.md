# Fase 60 — candidato de snapshot con taxonomía v1.1

## Resultado

Se rematerializó un candidato aislado desde `prospective_staging_v2` usando
`src/espn_event_taxonomy.py`. No se activó, no se cambió el snapshot oficial y
no se modificó el router.

- staging: 10,232 partidos, 10,202 completos.
- eventos raw reclasificados: 1,323,635.
- eventos `unclassified`: 0.
- partidos con marcador no reconciliado excluidos del candidato: 401.
- candidato: 9,801 partidos, 117,612 filas.
- coincidencia con el snapshot activo: 9,751 partidos, 117,012 filas.
- partidos extra de staging: 50.
- filas con cambios frente al activo: `3,893`, todas exclusivamente en
  `fouls`; no cambiaron goles, tiros, corners, tarjetas, sustituciones, presión
  ni marcador inicial.

## Bloqueo restante

La cobertura activa quedó cerrada y la equivalencia de estados está confirmada;
los partidos extra de staging se mantienen fuera de la comparación oficial:

- 457 partidos del snapshot activo no estaban presentes en staging y fueron
  recuperados por Fase 61 (`457/457`, sin fallos).
- 50 partidos válidos de staging todavía no están en el snapshot activo y se
  conservan como extras aislados.
- Los 401 excluidos por marcador no reconciliado requieren mantenerse fuera
  hasta recuperar una fuente consistente; 267 son `uru.1` y 104
  `fifa.friendly`, todos de 2025.

La diferencia histórica se resolvió corrigiendo el `competition_id` no numérico
que Fase 57 había guardado en esos 457 registros. Los 401 partidos con
marcador no reconciliado permanecen fuera; los extras no activos incluyen
`uru.1` y partidos amistosos.

## Gate

La taxonomía v1.1 pasa calidad semántica y cubre todo el snapshot activo. Las
3,893 diferencias de faltas son recuperación de señal antes no clasificada y
requieren recalibrar estados/transiciones en artefactos candidatos. La auditoría
de estados encontró `0` estados modificados en las 111,528 etiquetas comunes.
Los extras no se usan en la comparación OOS.

## Artefactos

- `scripts/run_phase_60_taxonomy_snapshot_candidate.py`
- `artifacts/phase_60_taxonomy_snapshot_candidate_v1/audit.json`
- `artifacts/phase_60_taxonomy_snapshot_candidate_v1/final_report.md`
- `artifacts/phase_60_taxonomy_snapshot_candidate_v1/event_windows.json`
- `scripts/run_phase_60_candidate_state_audit.py`
- `artifacts/phase_60_candidate_state_audit_v1/audit.json`

Version: 1.0.0
Created: 2026-07-27
