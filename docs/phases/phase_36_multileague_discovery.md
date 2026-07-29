# Fase 36 — descubrimiento multi‑liga ESPN

## Objetivo

Construir un inventario amplio de partidos de todas las competiciones ESPN
documentadas en el catálogo local, conservando `league_slug`, competición,
fecha de consulta e identidad ESPN antes de descargar play‑by‑play.

## Alcance

- Consulta únicamente `site.api.espn.com/.../soccer/{league}/scoreboard` con
  `dates=YYYYMMDD`.
- Incluye los slugs explícitos de `docs/documentacion_api_futbol.md`, tanto
  ligas nacionales como copas, torneos internacionales y selecciones.
- Deduplica por `(league_slug, provider_match_id, competition_id)`.
- Guarda caché local y artefactos sanitizados; no escribe PostgreSQL.
- No genera features, targets, predicciones ni métricas.

## Gates

1. El catálogo debe provenir de la documentación local y validar cada filtro.
2. Cada tarea debe quedar registrada por liga y fecha, incluyendo fallos.
3. Las referencias deben ser únicas y conservar el slug de liga.
4. La fase no puede modificar el router oficial de LaLiga.
5. La descarga de `event`, `summary` y `plays` queda para la fase de ingesta
   posterior, con `league_slug` obligatorio en staging.

## Artefactos

Se publican en `artifacts/phase_36_multileague_discovery/`:

- `config.json`, `coverage.json`, `date_results.json` y `references.json`.
- `audit.json`, `final_report.md` y `hashes.json`.

## Criterio de salida

La fase queda técnicamente validada cuando el barrido termina con todas las
combinaciones liga‑fecha auditadas, se reportan errores por tarea y la
deduplicación es reproducible. Esto no autoriza a mezclar las ligas con el
entrenamiento oficial: primero deben completarse la ampliación de esquema,
normalización de equipos, temporadas y particiones temporales.

