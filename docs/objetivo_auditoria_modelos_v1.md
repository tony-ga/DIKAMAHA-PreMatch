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

### Reparación aplicada (2026-08-12)

Auditadas las siete métricas por liga, no sólo córners. El resultado obligó a
distinguir dos fallos distintos, porque una regla única los habría confundido:

| Métrica | Patrón | Veredicto |
| --- | --- | --- |
| `corners`, `corners_first_half` | 72–100% ceros en 8 ligas vs 0–4% en las sanas | ausencia **total por liga** |
| `shots`, `shots_on_target` | 12–30% ceros en esas ligas vs **0%** en las sanas | ausencia **parcial por partido** |
| `yellow_cards`, `yellow_cards_first_half` | 3–66% ceros, sin separación entre ligas | ceros **legítimos** |
| `red_cards` | 76–100% ceros, media global 0.12 | ceros **legítimos** |

Un umbral ingenuo de "muchos ceros = dato ausente" habría suprimido el mercado
de tarjetas rojas, que es cero el 89% de las veces por razones normales del
fútbol. Por eso el criterio de ausencia sólo se aplica a métricas cuyo cero es
implausible en un partido profesional.

Entregado:

- `src/metric_coverage.py`: veredicto por liga (ausencia de métrica) y por
  observación (`shots == 0` delata que el bloque de estadísticas del proveedor
  no llegó, invalidando córners y tiros a puerta de esa misma fila).
- `scripts/run_metric_coverage_map.py` + artefacto
  `artifacts/metric_coverage/coverage_map.json`, construido desde el
  walk-forward real de Fase 84A. Ocho ligas quedan marcadas sin córners:
  `chi.1`, `eng.3`, `eng.4`, `eng.5`, `eng.fa`, `esp.2`, `esp.w.1`,
  `fifa.friendly.w`. `esp.super_cup` queda `insufficient_evidence` (n=6): el
  guard no afirma ausencia sin muestra.
- `ArtifactTeamCountMarketProvider._drop_uncovered`: retira mercados y
  escaleras de la métrica no cubierta, en todos los periodos, dejando intactas
  las demás métricas de esa misma liga.
- **Degradación abierta deliberada**: sin artefacto, o ante liga desconocida,
  no se suprime nada. Suprimir exige evidencia positiva de que el dato falta.
- Ese fail-open crea un riesgo conocido: si el mapa no viaja en la imagen
  Docker, el fallo vuelve en silencio -exactamente lo que ocurrió con
  `eligibility.json` de Fase 122-. Cubierto con `COPY` en el Dockerfile y una
  prueba de regresión sobre el manifiesto.
- 15 pruebas nuevas de cobertura y guard, más 1 de imagen Docker. Suite
  completa: 730 aprobadas / 8 omitidas.

### Reestimación de dispersión y modelos, excluyendo datos contaminados (2026-08-12)

`scripts/repair_team_count_coverage_bias.py` reajusta los siete modelos de
conteo de Fase 84A -Poisson regularizado + dispersión NB, mismo pipeline,
mismos features causales, mismo split walk-forward- excluyendo del
entrenamiento y la puntuación las filas contaminadas: ligas `absent` para
córners (ausencia sistémica) y observaciones puntuales con `shots == 0` para
cualquier métrica del mismo bloque de estadísticas (córners, tiros, tiros a
puerta), incluso en ligas con cobertura sana. Las tarjetas nunca se filtran.

Filas de entrenamiento excluidas por métrica: córners y córners 1ª mitad
4,737 (de un total mayor, ligas enteras); tiros y tiros a puerta 1,281
(observaciones puntuales); tarjetas 0.

**Dispersión de córners: `0.966` (contaminada) → `0.376` (limpia).** El efecto
no es sutil: para un equipo con 8.39 córners esperados en `esp.1` (liga sana,
sin cambio en la media, sólo en la dispersión), P(over 4.5) pasa de 57.5% a
71.3%. La dispersión contaminada hacía la distribución artificialmente ancha
-mezclar "siempre cero" con "media 8" infla la varianza compartida-, así que
**todas las ligas sanas venían recibiendo probabilidades menos seguras de lo
que sus propios datos justifican**, no sólo las ligas rotas.

Hallazgo adicional del reajuste: con datos limpios, tres líneas que antes no
superaban el gate ahora sí lo hacen -`corners_total_over_9_5`,
`first_half_corners_over_4_5`, `home_shots_over_10_5`-. **No se promovieron.**
El gate de Fase 84A es una comparación de punto sin intervalo de confianza;
promover exige el criterio de esta auditoría (calibración + IC bootstrap
sobre tasa base). Quedan registradas en
`audit.json:gate_passed_pending_bootstrap_audit` como candidatas para la
auditoría de escalera completa, no como mercados servidos. Los cuatro
mercados ya aprobados (`home_corners_over_4_5`, `away_corners_over_4_5`,
`away_shots_over_10_5`, `shots_on_target_total_over_7_5`) siguen pasando el
gate de forma independiente sobre datos limpios -ninguno se degradó-.

Publicado sobre el mismo artefacto que sirve producción
(`artifacts/phase_84a_team_count_markets/`), mismo contrato y hashes
regenerados; Fase 84A original queda preservada en el historial de git. 8
pruebas nuevas en `tests/test_repair_team_count_coverage_bias.py`, incluida
una que ancla un bug real de precedencia de operadores en Python encontrado
durante el desarrollo (`A if C else X | Y` no agrupa como parece; dejaba
todos los mercados "total" con muestra cero de forma silenciosa). Suite
completa: 739 aprobadas / 8 omitidas.

Limitación aceptada y no resuelta aquí: los features de historial acumulado
por equipo (Fase 74) siguen construidos sobre el corpus original, que
todavía mezcla partidos de ligas contaminadas al calcular perfiles rolling.
El efecto práctico es mínimo porque el guard de cobertura ya suprime toda
salida de córners para esas ligas, así que ningún usuario ve una predicción
basada en ese historial contaminado; el residuo teórico -coeficientes
compartidos del modelo pudiendo verse influidos por esas filas antes de
excluirlas del entrenamiento- ya no aplica, porque ahora se excluyen del
ajuste, no sólo de la salida. Purgar el historial acumulado en sí exigiría
reconstruir el corpus de Fase 74, fuera de alcance de esta reparación.

### Auditoría de la escalera completa y segunda ronda de correcciones (2026-08-12)

`src/ladder_audit.py` + `scripts/run_ladder_audit.py` auditan **350 celdas**
(6 métricas × 3 lados × cada línea entera desde 0.5 hasta el máximo). Cada
celda mide muestra, tasa observada, probabilidad media declarada, ECE por
tramos, y **dos** ventajas con IC bootstrap por partido completo:

- **acierto contra el lado mayoritario**, que en líneas extremas es casi
  imposible de batir y por sí solo declararía éxito donde no lo hay;
- **Brier contra el baseline de liga**, que responde la pregunta que importa
  para una salida adaptativa: ¿conocer los equipos concretos mejora la
  probabilidad frente a usar la media de la liga? El veredicto usa esta.

La primera pasada encontró dos defectos de especificación, ambos corregidos:

1. **Prior de suavizado mal especificado.** Fase 84A fijaba `safe_default` a
   mano: córners `4.5` con media real `7.99`, córners 1ª mitad `2.2` frente a
   `3.51`. Ese prior sesgaba el baseline hacia abajo **y** contaminaba los
   features de historial de cada equipo, que se suavizan contra él. Ahora se
   estima desde el bloque `fit`.
2. **Dispersión marginal en vez de condicional.** `phi` se estimaba sobre la
   varianza del target agrupado, que mezcla la dispersión alrededor de la
   media de cada partido con la variación de esa media entre partidos. Para un
   modelo que ya predice una media por partido, contar la segunda infla `phi`
   y empuja todas las probabilidades hacia 0.5. Medido: tiros `0.34` marginal
   frente a `0.12` condicional.

Efecto de las dos correcciones sobre las mismas 350 celdas:

| | Antes | Después |
| --- | ---: | ---: |
| Publicables (calibradas) | 254 | **310** |
| Con ventaja real del modelo | 38 | **84** |
| Miscalibradas | 96 | **40** |

Sesgo medio de calibración: córners `-0.0345 → -0.0104`, córners 1ª mitad
`-0.0251 → -0.0109`, tiros `-0.0167 → -0.0114`.

**Los córners recuperaron modelo, pero apenas.** Con el prior corregido el
peso de mezcla pasó de `0.0` a `0.1` y aparecieron 22 líneas con ventaja real
donde antes había cero. Aun así la mejora de deviance es del `0.16%`: la
identidad de los equipos aporta muy poco en córners frente a la media de liga,
y córners de primera mitad sigue en peso `0.0` -sin modelo por equipo-. Tiros
a puerta, en cambio, subió a peso `1.0` (modelo completo).

**Causa identificada de las 40 miscalibradas restantes**: se concentran en
mercados `total` (córners total 13, tiros total 12). La fórmula que combina
las dos orientaciones asume independencia entre local y visitante, pero en un
partido de ritmo alto ambos suben a la vez. Asumir independencia subestima la
varianza del total. Es un tercer defecto real, todavía sin corregir.

### Tercera corrección: correlación local-visitante (2026-08-12)

La hipótesis inicial era que local y visitante se mueven **juntos** en un
partido de ritmo alto. Los datos la refutaron: la correlación residual de
córners es **negativa (`-0.29`)**, igual que la de tiros (`-0.17`). Tiene
sentido futbolístico -córners y tiros son casi de suma cero: el equipo que
domina el territorio deja al rival con menos-. Las tarjetas sí son positivas
(`+0.19`): un partido áspero reparte para ambos lados.

Asumir independencia, por tanto, **sobreestimaba** la varianza del total en
córners y tiros, ensanchando la distribución y subestimando las
probabilidades. `combined_dispersion` (`src/team_count_markets.py`) añade el
término de covarianza `2·ρ·σ_H·σ_A`; `correlation = 0.0` reproduce la fórmula
anterior exactamente, de modo que un artefacto sin el campo degrada al
comportamiento previo.

Detalle que importó: la correlación necesaria es la **residual**, no la
bruta. La bruta mezcla la covariación de las medias entre partidos con la
covariación real alrededor de ellas, y sólo la segunda entra en la varianza
condicional del total. En tiros la bruta da `+0.27` y la residual `-0.17`
-signo opuesto-. Las residuales estimadas sobre `fit`+`selection` coinciden
con las medidas de forma independiente en confirmación (córners `-0.29` vs
`-0.31`, tiros `-0.17` vs `-0.15`), lo que sugiere una estimación estable.

### Estado acumulado de la auditoría

| | Inicial | Tras priors + dispersión | Tras correlación |
| --- | ---: | ---: | ---: |
| Publicables | 254 | 310 | **324** |
| Con ventaja real | 38 | 84 | **94** |
| Miscalibradas | 96 | 40 | **26** |

Las 26 restantes se concentran en `shots total` (11 líneas altas, 13.5–23.5),
córners `home`/`away` en la zona media, y `shots home` 11.5–13.5. Ya no hay
un patrón único que las explique; la siguiente iteración necesitaría
diagnóstico línea a línea en vez de una corrección estructural.

### Cuarta corrección: alias de tiros y dependencia circular en el mapa de cobertura (2026-08-12)

Al investigar las 26 líneas miscalibradas restantes, la hipótesis inicial de
tendencia temporal resultó frágil al extrapolar (proyección con signo
contrario al esperado para córners). Controlar por liga -en vez de por
calendario- reveló la causa real: en seis ligas (`eng.3`, `eng.4`, `eng.5`,
`esp.2`, `esp.w.1`, `chi.1`) **`shots` es exactamente igual a
`shots_on_target` en 98-100% de las observaciones**, frente a 0.4-0.5% en
ligas sanas -donde ocurre por azar en un partido con pocos tiros, todos a
puerta-. El proveedor nunca envía tiros totales para esas ligas; el pipeline
copió ahí el valor de tiros a puerta. Esas filas aportaban una media de ~1.3
tiros en vez de ~10-13, arrastrando también el ajuste de las ligas sanas.

`src/metric_coverage.py` incorpora esta segunda forma de ausencia por liga
-alias, no cero- con el mismo umbral y la misma filosofía que ya usaba para
córners. `shots_on_target` en esas ligas **no** se suprime: es el dato real,
sólo `shots` (total) está contaminado.

**Efecto colateral descubierto y corregido en el mismo commit**: el mapa de
cobertura se generaba desde `team_predictions.json`, la salida YA filtrada
del script de reparación -una dependencia circular real-. Una vez marcada
`absent` una métrica, esa métrica desaparecía de las filas de esa liga en la
salida, así que una segunda pasada del mapa de cobertura ya no encontraba la
señal que la primera sí había encontrado. `scripts/run_metric_coverage_map.py`
ahora lee directamente el corpus crudo de Fase 74, sin pasar por ninguna
salida ya filtrada. Efecto: pasó de 33 a 39 ligas evaluables -con suficiente
muestra ahora en el corpus completo- y detectó dos ligas más sin córners
(`conmebol.libertadores`, `eng.league_cup`) que antes no tenían muestra
suficiente en el split de confirmación por sí solo.

Efecto de esta ronda sobre las 350 celdas:

| | Anterior | Con alias + corpus crudo |
| --- | ---: | ---: |
| Publicables | 324 | **336** |
| Con ventaja real | 94 | **101** |
| Miscalibradas | 26 | **14** |

El prior de tiros saltó de `9.229` a `11.619` al excluir las filas con alias
-mucho más cerca de la media real (~9.9)-. Tiros mejoró de forma marcada: de
3 a 21 líneas con ventaja real en el mercado `total`, de 12 a 21 en `home`, de
16 a 27 en `away`.

**Lo que queda (14 celdas) ya no comparte una causa estructural única.** Los
gaps de calibración son pequeños (0.02-0.054, casi todos justo en el umbral)
y el sesgo medio de córners **cambió de signo** entre esta ronda y la
anterior (-0.067 → +0.227): el prior estimado desde `fit` no coincide
exactamente con la realidad de `confirmation` porque son bloques
cronológicos distintos, y ese desajuste no tiene una dirección fija -a veces
sobra, a veces falta-. Es consistente con el límite estructural de un prior
estático en un split walk-forward de tres bloques, no con un defecto de
especificación nuevo. Corregirlo exigiría un prior que decae con el tiempo en
vez de una constante fijada una sola vez, un cambio de arquitectura mayor
frente al retorno ya decreciente de seguir iterando aquí.

12 pruebas nuevas (alias, dependencia circular, regresión de Cambridge
United-Barnet actualizada a la cobertura real). Suite completa: 762
aprobadas / 8 omitidas.

Pendiente para continuar: exponer en la Mini App las 336 líneas publicables
etiquetadas por origen de fiabilidad (`model_edge` frente a
`base_rate_driven`), suprimiendo las 14 miscalibradas.

## Etapa 3 — exposición en la Mini App (2026-08-12)

Antes de tocar el frontend apareció un hallazgo que reordenó el trabajo: la
rejilla que la Mini App ya mostraba (`bounded_market_grid_view`, "Rejilla
adaptativa por periodo") **no sale del modelo auditado**. Sale de Markov
(Fase 88), que no se tocó ni se auditó en esta ronda. Fase 84A -el modelo NB
reparado y auditado- sólo alimentaba antes las líneas fijas aprobadas
(`user_market_view`) y la escalera de tiros a puerta. Mezclar ambas fuentes
sin distinguir el origen habría sido la misma clase de certeza inventada que
esta auditoría existe para evitar, así que se optó por una vista nueva y
separada en vez de ensanchar la rejilla existente.

- `src/ladder_reliability_view.py`: consulta en runtime el veredicto de
  `ladder_reliability.json` por (métrica, lado, línea). Degrada **cerrado**,
  al revés que `MetricCoverage`: sin evidencia de que una línea es fiable, no
  se publica -la asimetría es deliberada, documentada y probada-.
- `src/team_count_market_runtime.py` añade `_audited_market_ladder_view`,
  que reconstruye la escalera completa de las seis métricas de Fase 84A
  (córners, córners 1ª mitad, tiros, tiros a puerta, tarjetas y tarjetas 1ª
  mitad) reutilizando `_ladder`/`_combined_phi` ya existentes -sin
  duplicarlos-, filtra por el veredicto de fiabilidad y etiqueta cada línea
  superviviente. Respeta la supresión de cobertura ya vigente: una liga sin
  córners reales tampoco los muestra aquí.
- Verificado contra el runtime real, no una reimplementación: `esp.1` (liga
  sana) expone 18 grupos y 235 líneas etiquetadas; `esp.2` (sin córners ni
  tiros) expone sólo tiros a puerta y tarjetas.
- Mini App: `audited-ladder.tsx` -componente nuevo, separado de la rejilla
  existente- renderiza la escalera auditada con vista compacta por defecto
  (las líneas más cercanas a P(over)=50%, donde una línea distingue mejor) y
  expansión a la escalera completa por grupo. Cada línea declara si su
  fiabilidad viene del modelo o de la media de la liga, reutilizando el
  mismo patrón visual ya validado en el menú de mayor probabilidad (Fase
  122). La lógica de selección/orden vive en `lib/audited-ladder.ts` como
  funciones puras, siguiendo la convención del proyecto de testear lógica
  con Vitest y dejar el renderizado a Playwright.
- 24 pruebas nuevas: 7 de integración del runtime real (incluida la
  degradación cerrada sin artefacto y la ausencia del periodo `second_half`,
  que pertenece a Markov, no a esta vista), 10 de la consulta de fiabilidad,
  11 de la lógica pura del frontend, 1 prueba E2E de Playwright que verifica
  las etiquetas de fiabilidad y la interacción de expandir. Typecheck, build
  Next y las 39 pruebas E2E existentes sin regresiones. Suite Python completa:
  779 aprobadas / 8 omitidas.

Con esto el objetivo queda cumplido en su alcance verificable: los modelos de
conteo de equipo están auditados y reparados, y la Mini App expone
exactamente lo que se midió -ni más, ni con una confianza que no se ganó-.
Quedan abiertas, y documentadas, dos líneas de trabajo futuro: las 14 celdas
sin causa estructural común (límite del prior estático walk-forward) y la
posible auditoría del modelo Markov de Fase 88, que sigue sirviendo la
rejilla original sin la misma verificación.

## Qué NO promete este objetivo

- No promete que todas las líneas 0.5–12.5 resulten aptas. Es probable que las
  colas extremas queden como informativas de alta certeza y sin ventaja, y
  eso se declarará así en la interfaz en vez de disfrazarse.
- No promete ROI, valor esperado ni recomendación de apuesta. Sigue fuera de
  alcance por decisión permanente del proyecto.
