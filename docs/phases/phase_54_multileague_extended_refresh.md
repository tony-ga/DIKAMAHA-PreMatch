# Fase 54 — ampliación multi-liga post-2025

## Resultado

Se amplió el rango de ESPN del `2026-01-01` al `2026-07-27` para las 42 ligas
documentadas. La ejecución se realizó primero en `dry-run` y después se
publicó con un identificador nuevo, sin sobrescribir snapshots anteriores.

- 4,873 referencias ESPN descubiertas.
- 322 referencias seleccionadas, con máximo de 10 por liga.
- 293 partidos completos aceptados.
- 3,516 ventanas nuevas de 15 minutos.
- 29 referencias excluidas: 15 con timeline vacío y 14 con discrepancia de
  marcador.
- Snapshot final: 117,000 filas, 9,750 partidos y 42 ligas.
- PostgreSQL no fue escrito.

## Snapshot activo

`phase54_multileague_post2025_v1_20260727`

La publicación creó una versión inmutable y conservó rollback hacia
`phase53_multileague_post2025_v1_20260727`, `phase52_post2025_mex_v1_20260727`
y `phase38_multileague_v1_20260727`.

## Verificación de flujo

Después de activar el snapshot se repitió Puebla–Guadalajara:

- HTTP 200;
- snapshot versionado utilizado correctamente;
- advertencia de frescura: `False`;
- cutoff causal respetado;
- datos del partido objetivo utilizados: `False`;
- Markov promovido: `False`.

La ampliación mejora la cobertura operativa para solicitudes universales, pero
no cambia el router ni constituye una evaluación OOS independiente.

