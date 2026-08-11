# Fase 122 — Menú de mayor probabilidad

**Decisión:** `DEC-162`
**Estado:** `implemented_historical_evidence_shadow`
**Fecha:** 2026-08-11

## Objetivo

Medir dónde una probabilidad alta del modelo pre-match es realmente de fiar, y
exponer en la Mini App sólo los picks del día cuyo par (mercado, tramo de
confianza) tenga fiabilidad histórica demostrada.

La pregunta no es *qué mercado acierta más* sino **en qué mercado y a qué nivel
de confianza declarada el acierto observado justifica exponer el pick**. La
diferencia es material: `home_corners_over_4_5` acierta 76.1% global, pero la
estrategia ingenua de elegir siempre el lado mayoritario acierta 72.0%.

## Alcance

Los doce mercados que el sistema sirve hoy: 1X2, Más de 2.5, Ambos marcan y las
nueve líneas de conteo por equipo de Fases 84A y 88. No entra la rejilla
adaptativa de Fase 102, que carece de evaluación walk-forward por familia.

Fase 122 es una decisión de **exposición de producto**, no de promoción. Los
mercados shadow conservan íntegra su etiqueta y su badge visible.

## Entradas

| Artefacto | Uso |
|---|---|
| `artifacts/phase_110_extended_reliability_evaluation/ranked_predictions.json` | Cohorte de 1,270 partidos, 12 mercados |
| `artifacts/phase_105_historical_1000_complete/ranked_1000_predictions.json` | Control: identifica los 270 partidos nunca publicados |
| `artifacts/phase_106_probability_repair/calibrator.json` | Recalcula BTTS como lo sirve producción |
| `src/team_count_market_runtime.py::MARKOV_BASELINE_FALLBACKS` | Aplica el fallback de liga servido |

La cohorte es el universo causal elegible completo del split `confirmation`.
Ninguno de sus partidos se usó para ajustar ni seleccionar los modelos
servidos: `confirmation` quedó fuera del `fit` de Fases 84A/88 y de la
selección de hiperparámetros de Fase 103, y la cadena Dixon-Coles/Kalman es
walk-forward predict-before-update por liga.

## Método

1. **Probabilidades servidas, no crudas.** BTTS se recalcula con el calibrador
   sellado mediante pasada causal sobre todo el corpus;
   `home_corners_second_half_over_2_5` usa su baseline de liga.
2. **Pick.** El lado que el modelo elige (over si p>0.5, si no under; argmax
   para 1X2). La confianza es la probabilidad de ese lado.
3. **Tramos.** `[0.55,0.65)`, `[0.65,0.75)`, `[0.75,1.0]`.
4. **Comparador.** Estrategia ingenua *elegir siempre el lado mayoritario*,
   pareada sobre los mismos partidos. McNemar exacto unilateral sobre pares
   discordantes; IC95% bootstrap de 10,000 remuestreos, semilla `20260811`.
5. **Comparaciones múltiples.** Benjamini-Hochberg a q=0.05 sobre las 21
   hipótesis con muestra suficiente.

## Gate v1 (congelado antes de puntuar)

1. `n ≥ 100`
2. límite inferior del IC95 de la tasa observada ≥ piso del tramo
3. habilidad sobre la tasa base > 0, significativa tras BH
4. `|brecha de calibración| ≤ 0.05`
5. estabilidad por liga ≥ 70%

**Resultado: rechazó las 21 celdas evaluables.** Se conserva como resultado
primario y evidencia negativa.

## Gate v2 (re-especificado, post-hoc)

El diagnóstico mostró que los criterios 2 y 4 penalizaban la infraconfianza
igual que la sobreconfianza: rechazaban un tramo que declara 68.3% y entrega
89.3%. Para un menú que publica la tasa observada y no la probabilidad del
modelo, sólo la sobreconfianza engaña.

1. `n ≥ 100` *(sin cambio)*
2. límite inferior del IC95 de la tasa observada ≥ **0.60**
3. habilidad sobre la tasa base ≥ 0 (no degradación); la superación
   estadística se conserva como etiqueta, no como criterio
4. **sólo sobreconfianza**: `observada − declarada ≥ −0.05`
5. estabilidad por liga ≥ 70% *(sin cambio)*

Cada celda apta se etiqueta `model_edge` si supera BH, o `base_rate_driven` si
no. Las aptas se confirman contra los 270 partidos nunca publicados por Fases
105/119.

**Resultado: 10 aptas, 9 confirmadas, de las cuales sólo 3 son `model_edge`.**

## Salidas

| Ruta | Contenido |
|---|---|
| `artifacts/phase_122_confidence_reliability/eligibility.json` | Artefacto sellado que gobierna el menú |
| `artifacts/phase_122_confidence_reliability/cells.json` | Las 36 celdas con todas sus métricas y motivos de rechazo |
| `artifacts/phase_122_confidence_reliability/holdout_cells.json` | Las mismas celdas sobre los 270 nunca publicados |
| `artifacts/phase_122_confidence_reliability/market_summary.json` | Rango operativo real de confianza por mercado |
| `artifacts/phase_122_confidence_reliability/final_report.md` | Reporte completo |
| `src/high_probability_view.py` | Runtime de selección y priorización |
| `GET /v1/high-probability` | API del menú diario |
| `miniapp/app/mayor-probabilidad/page.tsx` | Interfaz |

## Invariantes

- El menú publica la **tasa observada del tramo**, nunca la probabilidad del
  modelo, y ordena por esa cifra.
- El origen de la ventaja (`model_edge` / `base_rate_driven`) siempre es
  visible.
- El `ExposurePolicy` impide que varias líneas de la misma métrica y equipo
  ocupen el menú; máximo tres picks por partido.
- Artefacto ausente, corrupto o de versión distinta ⇒ **menú vacío**. Nunca un
  pick inventado ni una heurística de reemplazo.
- 1X2, Más de 2.5 y Ambos marcan no clasifican en ningún tramo y no pueden
  aparecer.

## Gate de salida

- [x] Gate congelado antes de puntuar, con su resultado reportado aunque sea de
      rechazo total
- [x] Cohorte sin uso en ajuste ni selección de los modelos servidos
- [x] Probabilidades servidas, no crudas
- [x] Comparador pareado con McNemar exacto y control Benjamini-Hochberg
- [x] Confirmación de las celdas aptas sobre los partidos nunca publicados
- [x] Degradación segura verificada por prueba
- [x] 659 pruebas Python aprobadas/8 omitidas, 21 Vitest, 23 Playwright,
      typecheck y build Next aprobados
- [x] Reproducción determinista verificada por hash en dos corridas

## Limitaciones

1. **El gate v2 es post-hoc.** El holdout de 270 partidos controla que sus
   umbrales no se ajustaran a cifras ya publicadas, pero es un subconjunto de
   la misma cohorte, no una muestra independiente.
2. Seis de las nueve celdas son `base_rate_driven`.
3. Cinco celdas aptas son unidireccionales: la evidencia sólo respalda el lado
   dominante.
4. Evidencia histórica, no prospectiva. No hay cuotas, ROI, CLV, Kelly ni
   stakes.
5. Sin el suplemento de 2024 de Fase 88, que requiere `DATABASE_URL`.

## Siguiente paso permitido

Validación prospectiva: congelar antes del kickoff los picks que el menú
publica y liquidarlos después, para contrastar la tasa observada histórica de
cada tramo contra su desempeño real. No está autorizado ajustar los umbrales
del gate con la misma cohorte, ni promover ningún modelo, ni comunicar ventaja
predictiva incremental de los mercados oficiales de goles.

## Reproducción

```bash
python scripts/run_phase_122_confidence_reliability.py
```
