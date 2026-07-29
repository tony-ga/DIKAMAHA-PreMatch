# Fase 72 — contrato causal y expansión ESPN

## Estado

`ready_for_next_phase`

Gate cerrado el 2026-07-27 con cobertura live de los 12 recursos obligatorios,
replay raw-first, 38 pruebas específicas y 295 pruebas de regresión aprobadas.

## Objetivo

Crear la infraestructura causal de datos que necesita `markov_pre_match v4`.
Toda respuesta externa debe persistirse cruda antes del parseo y conservar el
instante real de captura, identidad, endpoint, parámetros, hash y procedencia.

## Entradas

- `docs/plan_markov_prematch_v4.md`.
- `DEC-077`.
- `docs/documentacion_api_futbol.md`.
- `src/espn_prospective_connector.py`.
- ORM actual de `raw_api_responses`.
- snapshot activo `phase57_incremental_v1_20260727`.

## Alcance

- Diseñar repositorios y proveedores mediante interfaces abstractas.
- Versionar el contrato `raw_responses` para partido, equipo, atleta,
  competición, temporada y venue.
- Ampliar ESPN con roster, injuries, schedule, standings, athletes, officials,
  venue y odds.
- Persistir payload antes de cualquier normalización.
- Añadir retry, backoff, rate limit, caché, logging y hashes reproducibles.
- Preparar el colector de Fase 73 sin utilizar datos target post-kickoff.

## Fuera de alcance

- Entrenar estados latentes.
- Modificar Dixon-Coles, Kalman o el router oficial.
- Usar cuotas `current`, `close` o live.
- Evaluar ROI, Kelly o mercados.
- Activar Hawkes.

## Entregables obligatorios

- código y migración/adaptador del contrato raw-first;
- pruebas unitarias e integración con respuestas grabadas;
- `artifacts/phase_72_markov_causal_contract/config.json`;
- `input_manifest.json`;
- `coverage.json`;
- `audit.json`;
- `metrics.json`;
- `validation_report.md`;
- `final_report.md`;
- `hashes.json`.

## Gate de salida

- 100% de payloads parseados tienen una fila raw previa verificable;
- replay de parseo idéntico por hash;
- timestamps UTC y `fetched_at < kickoff` cuando el payload se use pre-match;
- cero lectura de plays/situation/probabilities del partido objetivo;
- cero escritura fuera de tablas o esquemas autorizados;
- caché, retry y errores auditables;
- tests del alcance aprobados;
- cobertura publicada por endpoint y tipo de entidad.

## Clasificación permitida

`ready_for_next_phase`, `insufficient_coverage`, `rejected_for_revision` o
`blocked_by_data`.

## Siguiente paso permitido

Si el gate cierra, continuar Fase 73 y abrir la materialización causal de Fase
74. Si falla, corregir únicamente el contrato o la cobertura; queda prohibido
entrenar Markov v4.
