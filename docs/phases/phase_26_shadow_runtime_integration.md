# Fase 26 — integración runtime del catálogo shadow

## Objetivo

Conectar el catálogo de Fase 25 al flujo de observación pre-match sin cambiar
los valores oficiales ni ejecutar candidatos experimentales.

## Alcance

- Validar el contrato shadow al iniciar el servicio local.
- Adjuntar `shadow_catalog` como metadatos de sólo lectura en la respuesta
  pre-match.
- Mantener el router oficial y todos sus campos matemáticos intactos.
- No calcular probabilidades, scores ni features de candidatos shadow.
- No usar eventos, estadísticas ni marcador del partido objetivo.

## Gate de salida

La fase sólo queda lista si una observación local demuestra simultáneamente:

1. todos los campos oficiales son idénticos a la inferencia vigente;
2. el bloque shadow declara modo `read_only` y no contiene outputs;
3. todos los candidatos siguen con `enabled_by_default=false` y
   `official_output_allowed=false`;
4. el contrato publicado y la respuesta son reproducibles.
5. la imagen Docker incluye el contrato shadow y completa el recorrido HTTP
   sin depender de PostgreSQL ni de servicios externos.

## Artefactos

`config.json`, `input_manifest.json`, `coverage.json`, `metrics.json`,
`audit.json`, `validation_report.md`, `final_report.md` y `hashes.json`.

## Siguiente paso permitido

Observar el contrato en ejecuciones pre-match reales de sólo lectura y acumular
evidencia operacional. No entrenar, cargar nuevas cohortes ni promover modelos
sin una decisión posterior.
