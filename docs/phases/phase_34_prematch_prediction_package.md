# Fase 34 — paquete de predicciones pre-match

## Objetivo

Generar la salida de inferencia para candidatos aprobados por Fase 32 sin
esperar a disponer de sus resultados. La fase usa el histórico congelado de
Fase 20 y la selección por target de Fase 21.

## Contrato

- Markov v2 se reconstruye con transiciones y estados históricos versionados.
- Las intensidades base se calculan con el prior dinámico venue-aware de Fase
  20, usando sólo partidos anteriores al kickoff.
- `first_half_goal` usa Markov porque es la selección congelada; los demás
  targets conservan sus modelos seleccionados por Fase 21.
- Se publican probabilidades estructurales, targets temporales y provenance,
  pero no targets observados, pérdidas ni bootstrap.
- No se modifica el router oficial ni se promueve ningún mercado.

## Ejecución

```bash
python scripts/run_phase_34_prematch_prediction_package.py
```

## Gates

- Sólo procesa candidatos preparados por Fase 32.
- No lee eventos ni marcador final del candidato.
- Cero campos `target_*` o `loss_*` en la salida.
- Cobertura completa de candidatos preparados.
- Simulaciones reproducibles por `match_id`.
- Promoción bloqueada hasta evaluación confirmatoria independiente.

## Resultado actual

La fase queda preparada pero en espera: hay 0 candidatos independientes y, por
tanto, 0 predicciones generadas. No se ejecutaron targets, pérdidas ni cambios
del router.

## Siguiente paso

Cuando Fase 31 encuentre una cohorte válida, ejecutar `33 -> 32 -> 34` y
conservar el paquete de predicciones antes de ejecutar Fase 35.
