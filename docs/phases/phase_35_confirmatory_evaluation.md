# Fase 35 — evaluación confirmatoria independiente

## Objetivo

Medir el desempeño de las predicciones publicadas por Fase 34 sobre una
cohorte que no participó en calibración, entrenamiento ni selección.

## Orden causal

1. Fase 34 publica las predicciones pre-match.
2. El partido termina y ESPN deja score/eventos completos en staging.
3. Fase 35 lee esos datos como targets post-match.
4. Se calcula log-loss y bootstrap por partido completo.

Los targets nunca se envían de regreso a features, router o entrenamiento.

## Gates

- Predicciones y targets completos por `match_id`.
- PostgreSQL leído mediante `SELECT` y conteos idénticos.
- Mínimo de 30 partidos.
- Mínimo de 20 positivos y 30 oportunidades para targets condicionados.
- Bootstrap agrupado por partido.
- Promoción bloqueada incluso si una señal aislada mejora.

## Ejecución

```bash
python scripts/run_phase_35_confirmatory_evaluation.py
```

## Resultado actual

La fase queda esperando predicciones pre-match porque todavía no existe una
cohorte independiente. No se leyeron targets, no se calcularon pérdidas y no
se modificó el router.

## Siguiente paso

Cuando exista una cohorte válida, ejecutar `34 -> 35`; después revisar la
evidencia antes de cualquier decisión de promoción.
