# Fase 37 — ingesta multi‑liga en staging

## Objetivo

Convertir las referencias deduplicadas de Fase 36 en un corpus aislado con
detalle de partido, resumen y play‑by‑play, conservando liga, competición,
equipos, temporada derivada del kickoff, payloads crudos y hashes.

## Contrato

- Entrada: `artifacts/phase_36_multileague_discovery/references.json`.
- Endpoints: `event`, `summary` y `plays?limit=300` del Core/Site ESPN
  documentado.
- Persistencia: sólo `prospective_staging_v2`, con escritura explícita.
- La columna `matches.league_slug` debe existir antes de escribir.
- `--max-matches` permite una prueba acotada por liga; la ejecución completa
  se reserva para el backfill autorizado.

## Gates

1. No iniciar escritura si falta `DATABASE_URL` o la migración de liga.
2. Fallos de un partido se registran sin perder el resto de la liga.
3. Los payloads crudos se persisten antes de derivar eventos.
4. No se lee ni modifica el router, los artefactos oficiales ni los targets.
5. La normalización conserva `league_slug` y los IDs ESPN de equipos.

## Estado

El ejecutor está implementado y validado sintácticamente. La aplicación de la
migración y el smoke real quedan pendientes de una conexión PostgreSQL local
autorizada; en el entorno actual `DATABASE_URL` no está configurada.

