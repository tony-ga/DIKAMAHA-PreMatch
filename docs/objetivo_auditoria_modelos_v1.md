# Objetivo: auditoría de modelos y escalera completa adaptativa

**Fecha:** 2026-08-12
**Estado:** objetivo fijado, Etapa 1 en ejecución.

## Objetivo

Auditar todos los modelos matemáticos que intervienen en las predicciones
pre-match, reparar los que no resistan la auditoría, y convertir la salida de
la Mini App en un sistema adaptativo por partido capaz de manejar la escalera
over/under completa (0.5 a 12.5 en córners, y el rango equivalente en tiros)
con una cifra por línea en la que se pueda confiar.

## Criterio de éxito

Una línea se considera **apta** cuando cumple las dos condiciones:

1. **Calibración honesta.** Cuando el sistema declara 78%, la frecuencia
   observada es 78% (dentro de tolerancia), y esto se cumple **en toda la
   escalera**, no sólo en las líneas centrales.
2. **Ventaja sobre la tasa base.** El modelo supera a la estrategia ingenua de
   predecir siempre el lado mayoritario de esa línea, con intervalo de
   confianza que no cruce cero.

El porcentaje de aciertos se sigue publicando al usuario, pero **siempre junto
a la tasa base de esa línea**, para que no pueda halagarse solo.

### Por qué no se usa el % de aciertos como criterio único

Con una media de ~5.4 córners por equipo, P(over 0.5) ≈ 98% y
P(over 12.5) ≈ 2%. Predecir el lado obvio de una línea extrema acierta ~98%
sin que el modelo aporte nada. Medir el éxito por aciertos agregados haría que
**añadir líneas extremas suba el "acierto" de ~55% a ~85% sin tocar una sola
fórmula**, premiando un sistema inútil que se reporta como excelente.

El proyecto ya chocó con esto: Fase 122 midió `home_corners_over_4_5` con
76.1% de acierto sobre una tasa base de 72.0% -4 puntos de ventaja real, no
76-, y 6 de sus 9 celdas aprobadas resultaron `base_rate_driven` en vez de
`model_edge`. La auditoría hereda esa distinción como criterio central.

## Restricción única que se mantiene

Causalidad estricta pre-kickoff: ninguna feature puede usar eventos,
estadísticas ni marcador del propio partido evaluado. No se mantiene por
respetar un contrato, sino porque un auditor que mira el resultado del partido
produce porcentajes que son ficción, y el objetivo entero deja de ser medible.

Todo lo demás -gates de fase, etiquetas shadow, orden del roadmap, la
prohibición de tocar `match_features v1`, la separación oficial/shadow- queda
explícitamente levantado para este trabajo. Se pueden corregir modelos, crear
nuevos, y crear conexiones que hoy no existen.

## Estado real de partida (verificado en código, 2026-08-12)

- **La escalera 0.5–12.5 ya se calcula.** `_ladder()`
  (`src/team_count_market_runtime.py:858`) genera líneas desde 0.5 hasta
  `LADDER_MAXIMUMS - 0.5`: córners partido completo llega a **12.5**, tiros a
  **28.5**, medias partes a 6.5/14.5.
- **Se descarta casi toda en presentación.** `_bounded_market_grid` recorta a
  `VISIBLE_LINE_MIN=1.5` / `VISIBLE_LINE_MAX=9.5` y `_centered_lines` conserva
  sólo **3 líneas** alrededor de P(over)≈50%. La Mini App
  (`miniapp/components/prediction-detail.tsx:118`) consume únicamente ese
  recorte: se calculan 13–29 líneas por grupo y el usuario ve 3.
- **La base distribucional es sólida**: binomial negativa con dispersión por
  métrica y cola adaptativa que no trunca masa material
  (`src/team_count_markets.py:114`).
- **Nadie ha validado nunca las colas.** Fase 84A validó 5 líneas fijas, Fase
  88 doce, Fase 103 doce. Ninguna fase midió la fiabilidad de la línea 0.5 ni
  de la 12.5. Ese es exactamente el hueco que abre esta auditoría.

Conclusión: esto es en su mayor parte un problema de **validación y
exposición**, no de construir modelos nuevos desde cero. Los modelos nuevos
sólo se justifican donde la auditoría demuestre que los actuales fallan.

## Modelos bajo auditoría

Cadena pre-match que sirve a usuarios reales:

| Modelo | Archivo | Rol | Estado hoy |
| --- | --- | --- | --- |
| Dixon-Coles v1 | `src/dixon_coles_v1.py` | Fuerza estructural ataque/defensa | oficial |
| Kalman v2 | `src/kalman_v2.py` | Estado temporal pre-kickoff | oficial |
| Cadena de goles | `src/official_goal_chain.py` | Compone DC+Kalman → 1X2, Over 2.5 | oficial |
| BTTS | `src/btts_probability.py` | Ambos marcan, con calibrador Fase 106 | oficial |
| Conteos de equipo | `src/team_count_markets.py` | NB → córners/tiros/tarjetas | shadow |
| Runtime de conteos | `src/team_count_market_runtime.py` | Escalera, rejilla, recomendaciones | shadow |
| Markov de equipo | `src/team_market_markov.py` | Mercados por mitad | shadow, 4/12 líneas |
| Calibración de mercado | `src/market_calibration.py` | Shrinkage bayesiano Fase 106/119 | conectado, 0 mercados corregidos |
| Soporte contextual | `src/contextual_support.py` | Features de contexto | en cadena |

## Protocolo de medición

- **Corpus:** histórico causal ya existente (Fase 74: 9,465 partidos / 39
  ligas; Fase 103: 9,646 partidos con play-by-play reconciliado). Sin datos
  externos nuevos y sin esperar eventos futuros.
- **Unidad:** el partido completo, no la línea ni la ventana.
- **Walk-forward:** cada predicción se genera sólo con información anterior a
  su kickoff.
- **Por cada celda (métrica, lado, periodo, línea)** se mide: muestra,
  frecuencia observada, probabilidad media declarada, error de calibración,
  tasa base, ventaja sobre tasa base con IC bootstrap, y estabilidad por liga.
- **La auditoría mide modelos congelados; no selecciona ni ajusta.** Cualquier
  reparación que surja se ajusta en un bloque de datos separado del bloque
  donde se mide su resultado.

## Etapas

1. **Auditoría de la escalera completa.** Medir cada línea de 0.5 al máximo en
   córners, tiros, tiros a puerta y tarjetas, por lado y periodo. Producir el
   mapa de qué líneas son fiables, cuáles están mal calibradas y cuáles no
   aportan ventaja. *(en ejecución)*
2. **Reparación.** Corregir donde falle: dispersión por liga/periodo en vez de
   global, calibración por tramo de línea, o sustitución de familia de
   distribución si las colas de la NB resultan sistemáticamente sesgadas.
3. **Exposición adaptativa.** Sustituir el recorte fijo de 3 líneas por una
   selección adaptativa por partido que muestre la escalera útil completa,
   etiquetando cada línea con su fiabilidad medida y su origen de ventaja.
4. **Números aproximados en tiros.** Además del over/under, publicar el rango
   probable de tiros (intervalo central de la distribución) en vez de obligar
   al usuario a leer 29 líneas.
5. **Conexión a la Mini App.** Integrar todo en `prediction-detail.tsx` con la
   escalera completa navegable y las etiquetas de fiabilidad.

## Etapa 1 — hallazgo bloqueante (2026-08-12)

**Datos ausentes registrados como ceros contaminan el corpus y la salida en
producción.** Medido sobre `artifacts/phase_84a_team_count_markets/team_predictions.json`
(3,790 filas equipo-partido, el walk-forward real de Fase 84A):

- **31.8% de todas las filas tienen `corners = 0`.**
- Seis ligas tienen **100%** de córners en cero: `esp.2`, `eng.3`, `eng.4`,
  `eng.5`, `esp.w.1`, `esp.super_cup`. Además `chi.1` 91%, `fifa.friendly.w`
  80%, `eng.fa` 72%. Los tiros muestran el mismo patrón (23–30% ceros) en esas
  mismas ligas.
- Un partido profesional real no tiene cero córners. El patrón -100% exacto en
  ligas enteras frente a ~1% en ligas top- indica que **ESPN no publica esa
  estadística para esas competiciones y el pipeline la almacenó como cero** en
  vez de como ausente.
- El modelo aprendió esos ceros: para `eng.5` predice **0.1776 córners
  esperados** en un partido completo, contra ~8.4 en `esp.1`.
- **Las nueve ligas afectadas están en el catálogo de 63 que se sirve a
  usuarios** (`docs/league_catalog_v1.json`).
- **No existe ningún guard de cobertura** en
  `src/team_count_market_runtime.py`: ni mínimo de historial, ni verificación
  de que la métrica exista para esa liga.

Impacto en el usuario, calculado con las funciones reales de producción
(`negative_binomial_distribution`, dispersión `corners = 0.966`):

| Liga | Esperado | Lo que la Mini App muestra hoy |
| --- | --- | --- |
| `esp.2` (Segunda División) | 0.18 córners | **"Menos de 4.5: 99.99%"** |
| `eng.5` | 0.18 córners | **"Menos de 4.5: 99.99%"** |
| `esp.1` (cobertura correcta) | 8.39 córners | "Menos de 4.5: 42.5%" |

No es un problema de calibración fina: el sistema afirma certeza prácticamente
absoluta sobre un evento que en realidad ronda el 50%. La maquinaria NB en sí
funciona bien -la escalera de `esp.1` decrece de forma suave y razonable-; el
modelo aprendió fielmente datos corruptos.

Consecuencias para el objetivo:

1. **Extender la escalera a 0.5–12.5 antes de reparar esto multiplicaría el
   error** en vez de mejorarlo: más líneas servidas sobre la misma base falsa.
2. Cualquier medición de "% de aciertos" sobre estas ligas es ficción: el
   modelo predice ~0, el "real" registrado es 0, y el acierto aparente es
   ~100%. Es exactamente el mecanismo que el criterio de éxito elegido evita.
3. La dispersión global de córners (`0.966`) se estimó sobre una mezcla de
   datos reales y ceros fabricados, así que **también está sesgada para las
   ligas sanas**. Requiere reestimación tras excluir las ligas sin cobertura.
4. La celda `home_corners_over_4_5` de Fase 122 (76.1% observado sobre 72.0%
   de tasa base) debe re-verificarse: puede estar contaminada por estas ligas.

Orden de trabajo corregido: reparar cobertura → reestimar dispersión →
auditar escalera completa → exponer.

## Qué NO promete este objetivo

- No promete que todas las líneas 0.5–12.5 resulten aptas. Es probable que las
  colas extremas queden como informativas de alta certeza y sin ventaja, y
  eso se declarará así en la interfaz en vez de disfrazarse.
- No promete ROI, valor esperado ni recomendación de apuesta. Sigue fuera de
  alcance por decisión permanente del proyecto.
