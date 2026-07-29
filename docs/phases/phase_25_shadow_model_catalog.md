# Fase 25 — catálogo shadow de modelos

## Objetivo

Congelar la separación entre router oficial y señales experimentales para que
una mejora puntual no se convierta accidentalmente en salida de producción.

## Política

- El router oficial es el de Fase 21.
- Todas las señales de Fases 22-24 quedan desactivadas por defecto.
- Una activación requiere revisión explícita, nueva partición temporal y gate
  confirmatorio bootstrap.
- No se promueven mercados en esta fase.

## Artefactos

`config.json`, `input_manifest.json`, `coverage.json`, `metrics.json`,
`shadow_contract.json`, `audit.json`, `validation_report.md`, `final_report.md`
y `hashes.json`.
