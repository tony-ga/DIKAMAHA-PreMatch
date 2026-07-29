# Fase 10 — evaluación temporal de targets v2

## Objetivo

Evaluar fuera de muestra los targets temporales v2 usando un Markov entrenado
con el histórico canónico y una confirmación temporal posterior, sin promover
mercados automáticamente.

## Partición

- `train`: 381 partidos canónicos de `event_windows v1`.
- `validation`: vacío; no se presenta como evidencia.
- `confirmation`: 44 partidos completos de `prospective_staging_v2`.
- Unidad de bootstrap: partido completo.

## Priors y modelo

- Priors `lambda_base` disponibles antes del kickoff para los 44 partidos.
- Transiciones, estados iniciales y emisiones ajustados sólo con `train`.
- Simulación Markov v2 con 5,000 trayectorias por partido.
- Ningún target post-match se usa como feature.

## Gates

1. Partición sin solapamiento y orden temporal válido.
2. Priors y predicciones completas para la confirmación.
3. Log-loss por partido contra baseline estimado en train.
4. Bootstrap agrupado por partido.
5. Soporte mínimo: 20 positivos y 30 oportunidades cuando el target es
   condicionado; los targets que no pasan se reportan sólo como descriptivos.
6. La promoción permanece bloqueada aunque un target aislado mejore.

## Resultado vigente

La fase queda `promising_unconfirmed`: `first_half_goal` tiene intervalo de
mejora estrictamente positivo, pero la cohorte sigue siendo pequeña y no existe
confirmación independiente posterior. Ningún mercado se habilita.

# Version: 1.0.0
# Created: 2026-07-26
