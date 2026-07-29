# Fase 80W — prueba de sistema completo en 100 partidos

## Objetivo

Evaluar la ruta histórica más cercana al producto final sobre 100 partidos
terminados, con probabilidades y fiabilidad por mercado.

## Cadena evaluada

`Dixon-Coles/Kalman -> conservación de intensidad -> simulación Markov ->
calibración temporal congelada -> mercados`

Hawkes se excluye porque sigue en shadow y no tiene valor incremental aprobado.

## Selección

Los 100 partidos cronológicamente más recientes del bloque `confirmation` de
Fase 45, seleccionados por `match_date, match_id` antes del scoring.

## Mercados

- 1X2;
- over 2.5;
- ambos equipos anotan;
- gol en primera mitad;
- gol en segunda mitad.

## Métricas

La fiabilidad porcentual es el acierto de la decisión: `argmax` para 1X2 y
umbral 0.5 para mercados binarios. Se acompaña con log-loss, Brier y calidad
probabilística normalizada para evitar confundir acierto con calibración.

El total es el macro-promedio de los cinco mercados. Es diagnóstico histórico,
no evidencia de promoción.

## Resultado

- Cobertura: 100 partidos únicos, seis ligas.
- Periodo: 2025-12-26 a 2025-12-30.
- Fiabilidad: 1X2 `39%`, over 2.5 `52%`, BTTS `49%`, gol 1T `60%` y
  gol 2T `72%`.
- Fiabilidad macro total: `54.4%`.
- Referencia ingenua macro por mayoría: `56.2%`; delta del sistema `-1.8 pp`.
- Calidad probabilística macro Brier-normalizada: `73.45%`.
- Replay idéntico por hash; selección y ranking separados del scoring.
- Regresión integral con PostgreSQL: `364 passed`.

## Clasificación

`validated` como diagnóstico reproducible, no promocionable. La cadena
completa disponible no supera una referencia ingenua de decisión en esta
cohorte; el router oficial no se modifica.

## Evidencia

`artifacts/phase_80w_complete_system_100_match_test/final_report.md` contiene
los 100 partidos ordenados, los cinco mercados y sus comparaciones reales.
