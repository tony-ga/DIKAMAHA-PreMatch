# Fase 32 — preparación de candidatos pre-match

## Objetivo

Verificar que cada candidato aprobado por Fase 31 tenga features históricas y
contexto pre-match alineados por partido y cutoff antes de ejecutar el router.
Los insumos prospectivos son materializados por Fase 33; los artefactos de
Fases 22–23 quedan como fallback histórico.

## Ejecución

```bash
python scripts/run_phase_32_prematch_candidate_preparation.py
```

La fase sólo lee artefactos locales, no genera probabilidades, no usa targets y
no modifica el router.

## Gates

- Feature y contexto presentes para cada candidato.
- Cutoff idéntico entre ambas fuentes.
- `target_match_data_used=False`.
- `target_match_statistics_used=False`.
- Cero predicciones generadas si algún input falla.

## Resultado actual

La fase queda en espera porque Fase 31 no tiene candidatos independientes.
No se generaron predicciones ni targets y el router oficial permanece intacto.

## Siguiente paso

Después de una sincronización ESPN que produzca candidatos, ejecutar Fases
31, 33, 32 y 34, en ese orden. Fase 34 genera el paquete pre-match; la
evaluación confirmatoria se ejecuta después y en un paso separado.
