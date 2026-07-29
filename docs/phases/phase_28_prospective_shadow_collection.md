# Fase 28 — captura prospectiva del contrato shadow

## Objetivo

Acumular una cohorte nueva posterior al cutoff congelado y observar el
contrato shadow sin modificar el router oficial, recalcular modelos,
evaluar desempeño o escribir en PostgreSQL.

## Alcance autorizado

- Lectura de `prospective_staging_v2` mediante consultas `SELECT`.
- Uso de lambdas OOS congeladas como entrada pre-kickoff.
- Captura de snapshots causales y resultados finales sólo para partidos cerrados.
- Replay determinista y auditoría de procedencia, temporalidad y referencialidad.

Quedan fuera de esta fase la evaluación confirmatoria, calibración, bootstrap,
reentrenamiento, promoción de mercados y activación de Hawkes o candidatos
shadow.

## Gates de salida

- Al menos 30 partidos completos.
- Cada snapshot sólo contiene eventos con `event_ts <= snapshot_ts`.
- Cero snapshots duplicados y orden temporal estable.
- Cero reutilización de partidos históricos o del ID bloqueado `704766`.
- Conteos de staging idénticos antes y después, conexión cerrada y cero escrituras.
- Replay idéntico, sin evaluación ni modificación de la salida oficial.

## Resultado

La fase queda `ready_for_evaluation` con 42 partidos completos y 6,795
snapshots. Se observaron 9,072 eventos de staging y los conteos permanecieron
idénticos (`44` partidos y `9,072` eventos). No hubo partidos huérfanos,
duplicación temporal ni leakage de eventos futuros. El staging registra 176
eventos sin equipo asociado; se conserva como dato de calidad observado y no
impidió el gate porque no genera orfandad de partido.

La evaluación no fue ejecutada. La auditoría posterior de Fase 29 determinó
que los 42 partidos ya aparecen en la calibración de Fase 20, por lo que esta
cohorte no puede pasar a confirmación independiente. Se conserva únicamente
como observación prospectiva read-only.

## Evidencia

- `artifacts/phase_7_11_prospective_collection/final_report.md`
- `artifacts/phase_7_11_prospective_collection/collection_status.json`
- `artifacts/phase_7_11_prospective_collection/temporal_audit.json`
- `artifacts/phase_7_11_prospective_collection/postgres_readonly_audit.json`
- `artifacts/phase_7_11_prospective_collection/provenance_audit.json`
- `artifacts/phase_7_11_prospective_collection/manifest.json`

## Siguiente paso permitido

Obtener una cohorte nueva ausente de todo ajuste, calibración y selección del
router antes de preparar una evaluación confirmatoria independiente.
