# Estado operativo DIKAMAHA

**Actualizado:** 2026-08-19

## Fase 133 — DIKAMAHA deja de vivir sólo dentro de Telegram

La Mini App se sirve ahora también desde un dominio propio, con el mismo diseño
y las mismas funciones, **sin una segunda copia del código**. El hallazgo que
gobierna la fase es que la Mini App de la Fase 115 nunca fue un cliente ligero:
es una aplicación Next.js completa con BFF propio, de modo que el 95% del
producto ya era web. Sólo cuatro puntos dependían de la plataforma
-autenticación, cobro, `BackButton`/tema y compartir- y son los únicos que
distinguen contexto.

La decisión que evita el problema difícil es la identidad (`DEC-219`): el
Telegram Login Widget devuelve **el mismo `telegram_user_id`** que `initData`,
que es la clave primaria de `miniapp_users` y la clave foránea de la
suscripción, la cuota diaria, los favoritos y las alertas. Quien ya usa la Mini
App entra en la web con su cuenta, su plan y su historial intactos: cero
migración de datos, cero tabla de identidades, cero flujo de vinculación.

El cobro web va con Stripe (`DEC-220`), apagado por `MINIAPP_STRIPE_ENABLED`.
Su idempotencia es estructural -`stripe_events` es la puerta, igual que
`star_payments` para Stars- y la regla de exclusión mutua impide que dos
pasarelas escriban sobre el único `plan_expires_at` del usuario y alguien pague
dos veces el mismo mes. Se implementó **sin añadir el SDK de Stripe**: tres
llamadas `fetch` y una verificación HMAC con `node:crypto`, en línea con el
runtime mínimo de la Fase 108.

Estado de pruebas: 244 Vitest en verde -los 23 archivos previos **sin
modificar**- más 21 nuevas del validador del widget, la pasarela y la detección
de contexto. En Playwright, 62 pasan y las 5 del proyecto `web` nuevo también.
Quedan 6 fallos E2E que **son previos a esta fase**: se comprobó forzando el
contexto a `telegram` -es decir, con el comportamiento anterior- y fallan igual.
Cinco muestran el muro Premium porque su stub no devuelve plan, y el sexto
espera 6 destinos en la barra cuando hay 7 desde la Fase 126.

Una fixture sí hubo que tocar: `responsive-fit.spec.ts` estubaba
`initData: ''`, que es justo la señal con la que se distingue el WebView de un
navegador. Su aserción no cambia -el bloqueo de escala sigue exigiéndose en
Telegram-; lo que cambia es que el stub ahora manda un `initData` como el que
manda un cliente real.

Falta lo que no se puede obtener sin desplegar: `/setdomain` en BotFather,
un acceso real desde el dominio, y un checkout de prueba con su webhook. El
orden está en `docs/runbooks/railway_public_web_app.md`.

## Las tres decisiones abiertas, llevadas a código y medidas (DEC-216 a DEC-218)

Se implementaron y desplegaron las tres decisiones que quedaban en estado
`propuesta`. **Una se activa, dos quedan implementadas pero desactivadas por
evidencia.** El código de las tres está en producción; ninguna cambia todavía
las probabilidades servidas.

**DEC-218 — activada.** El guardarraíl deja de ser prosa:
`src/confounding_check_v1.py` ejecuta bootstrap por grupo,
leave-one-group-out, control estratificado por fuerza y fragilidad del
intervalo. Su prueba principal reproduce con los 12 datos reales el caso de
formaciones que motivó la decisión -efecto `+2.4286`, IC95% que no cruza
cero, pero `influence_ratio 0.506` al excluir un solo grupo- y lo etiqueta
`"confundido"` de forma automática. Ya tuvo su primer uso real dentro de la
calibración de DEC-216.

**DEC-216 — cerrada con determinación: la forma vigente es la correcta.** La
forma temporal de `_score_factors` es ahora configurable con salida byte a
byte idéntica por defecto. El primer candidato (`ramp_v2` ajustado a ratios
de presión) fue **rechazado por el gate** contra PostgreSQL de producción
-7,400 partidos, 34 ligas, lectura verificada sin escrituras-: todos los
gates técnicos de DEC-155 pasan, pero el 1X2 log-loss degrada de forma
confirmada en validation (`-0.000943`, IC95% `[-0.001860, -0.000046]`).

En vez de dejar la causa como hipótesis, se midió (Fase 116D), y el
resultado **cierra la decisión**. `_score_factors` multiplica intensidad de
gol, no de presión, así que el objetivo correcto es el ratio de tasa de gol.
Al medirlo aparece un segundo defecto más grave: el ratio crudo es `0.913`
-quien pierde marca menos- pero eso es **selección, no comportamiento**,
porque quien va ganando suele ser el equipo mejor. Controlando por fuerza
propia y del rival, el ratio pasa a `1.097` e **invierte el signo**. Es
justamente el caso que DEC-218 existe para detectar, aplicado a la
calibración que lo motivó.

Con la cantidad correcta y sin la confusión, **la forma lineal vigente cae
dentro del IC95% en las cinco ventanas**, y en el minuto 67.5 es casi exacta
(1.227 declarado contra 1.231 medido). La rampa ajustada a ese objetivo
degenera (`curvature≈0`, el optimizador colapsa contra el borde) y pierde en
`selection` frente a la lineal (error `2.67` vs `1.14`).

Conclusión: **la hipótesis que abrió DEC-216 era un artefacto de dos errores
compuestos** -medir presión en vez de gol, y no controlar la confusión por
fuerza en ninguno de los dos-. El gate la rechazó por la razón correcta, y la
configuración servida (`linear_v1`) es la que la evidencia respalda. Es un
cierre por determinación, no por agotamiento.

**DEC-217 — ACTIVADA, tras rechazar el mapeo equivocado y encontrar el bueno.**
El recorrido tuvo dos fases y la segunda cambia el resultado.

Primero se rechazó proyectar `save` a `shot_on_target`: no es una parada de
portero -12.72 por partido contra 5.31 tiros a puerta, **7.06 jugadores
distintos por partido** lo registran, los textos crudos incluyen centrales y
a Rashford, delantero, y el 53.3% coincide con un `shot_blocked` ya contado-.
Ese mapeo se retiró. Al cerrarlo quedó apuntada una continuación: el
candidato defendible no era proyectarlo sino **darle peso propio**.

Esa continuación se construyó y se midió. La pregunta previa -¿aporta `save`
información que el motor no tenga ya?- se respondió con dos regresiones de
Poisson fuera de muestra sobre 69,498 observaciones de 9,405 partidos:
**delta de deviance `+0.000987`, IC95% `[+0.000679, +0.001278]`, no cruza
cero**. Y con un coeficiente **negativo** frente a los positivos del resto:
quien acumula paradas está defendiendo. Es presión **recibida**, algo que el
motor no tenía en ninguna forma porque todos sus pesos describen presión
ejercida.

El gate histórico sobre 7,400 partidos y 34 ligas da las cuatro medidas
positivas, **ninguna degradando**, con mejora confirmada del objetivo
compuesto en validation (`+0.000244`, IC95% `[+0.000018, +0.000454]`).
Materialmente distinto del caso de DEC-216, cuya rampa sí degradaba. Bajo el
gate vinculante de DEC-155 queda `ready_for_activation`, y se activó:
`enable_defensive_save_signal=True`, `save` como tipo propio con peso `-0.60`
en la cadena y `-0.45` en el motor, fijados a priori desde la magnitud del
coeficiente para no gastar los bloques en tuning.

El efecto es pequeño y así se reporta: confirmado en validation, no
confirmado en confirmation, positivo en las cuatro medidas. Poner el flag en
`False` reproduce el comportamiento anterior, y una prueba lo exige.

**DEC-217 (registro anterior del mapeo rechazado) —** El usuario autorizó
migración, backfill y recalibración. El primer paso -localizar la tabla a
migrar- reveló que **nada de eso hacía falta**: los `save` ya están en
`prospective_staging_v2.events` (81,872 eventos en 6,434 de 9,786 partidos,
con columna `event_type_raw`); lo que los excluía era el `WHERE` de la
consulta del replay, no el esquema. Mi afirmación anterior de que "la base
histórica no contiene ningún `save`" era falsa: la deduje de que el replay no
los devolvía, sin mirar la tabla.

Con los datos reales delante, el candidato no es sólo inmedible sino
**sustantivamente incorrecto**. `save` no significa parada de portero: hay
12.72 por partido contra 5.31 `shot_on_target` -imposible parar más tiros de
los que se hicieron a puerta-, los registran **7.06 jugadores distintos por
partido** (los textos crudos incluyen a Militão, Cubarsí y Huijsen, centrales,
y a Rashford, delantero), y el **53.3%** coincide con un `shot_blocked` que el
modelo ya cuenta. Proyectarlo habría dado 12.37 tiros a puerta por partido
contra un rango realista de 7-11.

Esto **invalida el resultado de Fase 116E**, que había dado las seis
comprobaciones en verde: corrió sobre los 15 partidos del cache, donde los
`save` salen a 7.3/partido, y esa muestra no era representativa. Es la lección
que deja el episodio: verificar un mecanismo sobre 15 partidos no sustituye a
comprobarlo sobre el corpus entero.

El código de proyección se retiró; `src/markov_live_v1.py` vuelve a ser
byte-idéntico a su estado previo. Dejar un mapeo falso detrás de un flag
habría invitado a que alguien lo activara después apoyándose en el texto
anterior de la decisión.

**DEC-217 (registro anterior, superado) — capacidad desplegada, activación bloqueada por datos.** Primero
hubo que **corregir la premisa, que era falsa**: los tipos que la decisión
nombraba no existen en el feed de ESPN, y el `CHECK` de `events_timeline` no
era el bloqueador -el motor live no lee esa tabla-. Reenfocada a `save`, la
auditoría encontró señal real: ESPN emite 7.3 saves/partido contra 2.9
`shot_on_target`, así que el motor venía subcontando tiros a puerta. La
proyección exige invertir el equipo (ESPN atribuye el save al portero) y
deduplicar (43% coexisten con un tiro ya reportado; ventana ±5s por meseta).

Bloqueo descubierto al ejecutar el gate: **la base histórica no contiene
ningún `save`** -el CHECK los filtró en la ingesta-, así que el candidato es
inmedible contra el replay. Y los pesos del motor se calibraron sobre ese
mismo corpus sin saves; activarlo elevaría `shot_on_target` de 2.9 a ~7.6 por
partido sin recalibrar. Precondición para desbloquear, en orden: persistir
los auxiliares, recalibrar `EVENT_WEIGHTS`/hazard/CTMC, y sólo entonces
gatear.

**Un defecto silencioso corregido antes de desplegar.** La proyección de
`save` comprobaba `event_type == "save"`, pero el follower emite
`event_type="auxiliary"` con `event_type_raw="save"`. Habría quedado activa
sin hacer nada, sin excepción ni registro -el peor modo de fallo posible-.
Hay una prueba con la forma exacta que construye el follower.

Gates: 946 pruebas en verde contra una línea base de 911, con los mismos 18
fallos conocidos de contención de CPU antes y después -cero regresiones-. El
gate contra producción verificó `read_only: True`, `counts_identical: True`
y `postgresql_writes: 0`.

## De una investigación externa (hudl/open-data) a tres decisiones sobre el propio proyecto (DEC-216 a DEC-218)

El usuario pidió investigar `github.com/hudl/open-data` (datos StatsBomb)
exclusivamente 2025+, sin tocar producción, y luego convertir los hallazgos
en decisiones de acción. Se recorrieron las 80 combinaciones competición/
temporada del repositorio filtrando por `match_date` real: **31 partidos en
total tienen fecha 2025+, todos la UEFA Women's Euro 2025 completa** -no una
liga, un torneo-. Se analizaron los 31, con eventos StatsBomb de detalle
(105,658 eventos), y por separado sus alineaciones/formaciones. Es fútbol
femenino internacional, no las ligas de clubes masculinas que cubre
DIKAMAHA -los hallazgos ahí no son directamente aplicables sin verificarse
sobre datos propios-.

**El hallazgo con más peso real, verificado sobre el corpus PROPIO de
DIKAMAHA, no sobre hudl (DEC-216, Fase 133 propuesta).** hudl sugirió que
"ir perdiendo aumenta intensidad ofensiva" tiene forma no-lineal -casi nula
antes del minuto 60- pero con 31 partidos no se pudo confirmar. Probado
sobre los 9,465 partidos de Fase 74: presión media ganando-vs-perdiendo es
indistinguible de cero en primera mitad (IC95% `[-0.1535, +0.0185]`,
n=4,586) y **fuertemente confirmada en segunda** (`-0.3586` IC95%
`[-0.4377, -0.2797]`, n=7,782) -el IC95% más ajustado de todo este bloque
de trabajo-. Sugiere que la forma lineal-desde-kickoff de `_score_factors`
en `live_probability_engine_v1.py` (motor oficial desplegado) podría estar
mal calibrada en FORMA. **No se tocó el motor**: es un diagnóstico
motivador, no una promoción -exige su propio split selección/confirmación
y el runner histórico oficial contra Postgres antes de cualquier cambio-.

**Dos hallazgos quedaron como candidatos futuros documentados, sin código
(DEC-217, DEC-218).** ESPN ya clasifica tipos de evento finos (duelo,
intercepción, desposesión) en su taxonomía cruda, pero la tabla de
producción `events_timeline` los descarta por una restricción de esquema
-nunca se persisten-; queda como candidato `live-only` concreto para
enriquecer el motor en vivo, bloqueado hasta confirmar con una muestra real
y clasificar el campo por `references/espn-bot-data-enrichment.md`. Y un
guardarraíl metodológico para Fase 84B: un análisis de formaciones que
parecía mostrar una relación real resultó ser una confusión de fuerza
relativa esperada -se documenta para no repetir el error cuando haya datos
de alineación propios-.

**Nada tocó producción.** Todo el análisis de hudl corrió en un directorio
de trabajo temporal fuera del repositorio; el diagnóstico sobre el corpus
propio de DIKAMAHA (Fase 133) es de solo lectura sobre un artefacto ya
materializado. Tres entradas de `decision_log.md`, dos filas de roadmap
-una fase nueva propuesta (133) y dos notas de candidatos futuros-, sin
cambios de código servido ni pruebas nuevas que correr.

## Por qué el favorito visitante falla más: la forma exacta del sesgo, y por qué la corrección todavía no confirma (DEC-215)

Continuación directa de `DEC-212`, a pedido explícito del usuario de seguir
investigando hasta encontrar algo concluyente. `DEC-212` había medido que ni
la temperatura ni el peso de mezcla segmentados por localía del favorito
distinguen del baseline -pero eso no explicaba *por qué*-.

**El hallazgo.** Sobre un corpus independiente y más grande (3,538 partidos
con favorito, `artifacts/walkforward_predictions/baseline.jsonl`, distinto
del corpus de 1,000 partidos de `DEC-211`), se descompuso la fiabilidad de
1X2 por clase en vez de mirar solo el log-loss agregado. El favorito
visitante tiene un sesgo real y confirmado -IC95% no cruza cero- en dos de
tres clases: declara `48.28%` de ganar y gana `44.37%` (+3.91pp de exceso);
declara `26.25%` de perder y pierde `31.26%` (-5.00pp de defecto). La tasa de
empate es *idéntica* entre favorito local y visitante (24.4% ambos) -la
fragilidad extra no es "empata más", es "pierde en lugar de ganar"-. El
favorito local no tiene ningún sesgo detectable en ninguna de las tres
clases. Robusto a excluir la liga que más contribuye (`mex.1`, sólo 9% del
subgrupo): el sesgo de "pierde" se mantiene casi igual.

**Por qué la temperatura no podía encontrarlo.** Un solo parámetro simétrico
no puede achicar específicamente "gana" y agrandar específicamente "pierde"
dejando "empata" fijo -eso exige mover clases en direcciones distintas, no
escalar las tres igual-. El resultado indistinguible de `DEC-212` queda
explicado, no contradicho.

**El candidato con la forma correcta, y por qué tampoco confirma todavía.**
Dos sesgos aditivos en log-espacio, ajustados solo para el subgrupo
favorito-visitante. Sin regularizar: dirección correcta pero inestable -24%
de los partidos de confirmación voltean quién es favorito, IC95% cruza
cero-, la firma clásica de sobreajuste con muestra chica (477 partidos en
selección). Regularizado con penalización L2 elegida por validación cruzada
-nunca toca confirmación-: para favorito local la CV elige la penalización
máxima del grid, contrayendo el sesgo casi a cero -confirma que ahí no hay
nada que corregir, coherente con `DEC-212`-; para favorito visitante elige
una penalización moderada, los volteos de argmax bajan a 3.95%, pero el
log-loss sigue indistinguible con esta muestra.

**Conclusión honesta.** El sesgo es real, robusto y tiene forma identificada
-no ausencia de señal-, pero la muestra de favoritos visitantes (477-582
partidos según el corte) no alcanza para confirmar una corrección con el
mismo rigor que el resto del proyecto exige. Es `insufficient_coverage`, no
"rechazado" -misma distinción que ya usa Fase 92-. Repetir cuando el corpus
de favoritos visitantes crezca -por ejemplo extendiendo el walk-forward al
corpus de 9,465 partidos que hoy no se usa para esto-. Aviso de seguridad
pendiente si algún día se confirma: a diferencia de la temperatura, esta
corrección no garantiza preservar el argmax.

Gates: sin cambios de código servido; dos scripts nuevos de evaluación de
candidatos (`scripts/evaluate_favorite_venue_bias_correction.py`,
`scripts/evaluate_favorite_venue_bias_correction_regularized.py`), ambos de
solo lectura sobre artefactos ya materializados.

## De una investigación de fallos de predicción a cuatro candidatos medidos, ninguno promocionado (DEC-211 a DEC-214)

El usuario pidió transformar en decisiones de acción puntuales una
investigación previa de fallos de predicción sobre 1,000 partidos reales
(split `confirmation`, 21 ligas, bootstrap por partido). Cada hallazgo
accionable se convirtió en un candidato medible con su propio gate, siguiendo
exactamente los patrones ya validados por `DEC-200/201/202`: parámetro
elegido en selección, medido en confirmación, partido completo como unidad
IID. **Los cuatro candidatos quedaron medidos y ninguno se conecta** -el
resultado en sí es la entrega, no una promoción-.

**Temperatura y peso de mezcla de 1X2 por localía del favorito (DEC-212,
Fases 127-128).** El favorito visitante falla más que el local (57.7% vs
46.9%, IC95% `[-17.04, -4.30]`, `DEC-211`). `scripts/evaluate_favorite_venue_
temperature.py` y `scripts/evaluate_favorite_venue_blend_weight.py`
segmentaron T y el peso por {favorito local, favorito visitante} con
contracción jerárquica hacia los valores globales adoptados (T=1.198935,
peso=0.642848). Ambos quedan indistinguibles de la composición ya servida
-log-loss `-0.000014` IC95% `[-0.000085, +0.000054]` para T; `-0.000210`
IC95% `[-0.000428, +0.000001]` para el peso-. Lectura: la ventaja de localía
ya está capturada por el prior estructural (Dixon-Coles); no queda un sesgo
de calibración residual por localía del favorito que la recalibración pueda
corregir. El problema medido en `DEC-211` es real, pero no vive en ninguna de
las dos piezas de recalibración actuales.

**¿Es real la asimetría de localía en la reacción in-play, o solo ausente en
el motor? (DEC-214, Fase 129).** Un diagnóstico previo de solo lectura contra
`LiveProbabilityEngineV1` había mostrado que el motor es perfectamente
simétrico -diferencia = 0.0 exacta- entre gol de favorito local vs visitante
primero, con inputs sintéticos. `scripts/analyze_favorite_venue_inplay_
swing.py` midió el swing empírico real sobre el corpus de 1,000 partidos: es
igual de grande para favorito local (n=654, swing primer gol `+0.5486`) y
favorito visitante (n=338, swing `+0.5619`); la asimetría entre ambos cruza
cero tanto para "quién anota primero" (`[-0.1295, +0.1081]`) como para
"estado al descanso" (`[-0.1701, +0.1288]`). **La simetría del motor no es un
defecto.** Fase 130 (término de reacción asimétrico en el motor) queda
cerrada sin diseñarse -esta evidencia quita el motivo, no sólo el acceso a
Postgres que habría hecho falta para confirmarla-.

**Córners condicionados por faltas propias esperadas (DEC-213, Fase 131).**
El fallo de córners correlaciona con exceso de faltas propias (`home_corners_
over_4_5`: +1.17, IC95% `[0.57, 1.77]`; `away_corners_over_4_5`: +0.95, IC95%
`[0.39, 1.50]`), pero el modelo de córners de Fase 84A nunca usó faltas como
covariable. `scripts/build_fault_conditioned_corner_candidate.py` añadió un
bloque de perfil causal de faltas -mismo suavizado que las 11 métricas ya
declaradas- sólo al target `corners`, reconstruyendo el modelo servido con el
mismo código para una comparación exacta. El conteo bruto mejora
marginalmente (deviance `3.0291→3.0231`, MAE `3.1082→3.0868`, estabilidad por
liga `72%→76%`), pero no se traduce en mejor probabilidad de línea:
`home_corners_over_4_5` queda indistinguible y `away_corners_over_4_5`
**degrada de forma confirmada** en log-loss (IC95% `[-0.002943, -0.000046]`).
El `_gate()` de Fase 84A no pasa; Fase 132 (sincronización runtime) no se
abre.

**Documentación sin fase activa.** Tres hipótesis adicionales -roja no
explica fallos de Over/Under 2.5, estado al descanso no explica fallos de
córners, "cambio de régimen tras un gol" en Markov v4 Fase 80V- ya contaban
con bootstrap por partido e IC95% que cruza cero o pertenecen a una familia
ya archivada (`DEC-170`); se registraron en `DEC-211` como evidencia negativa
sellada, sin abrir fase, para que no se reintenten sin datos nuevos. BTTS/xG
-86.5% de los fallos de "ambos marcan" ocurren con el equipo fallido
generando tiros a puerta, no por ausencia de juego ofensivo- queda anotado en
`docs/00_roadmap_actual.md` como candidato futuro `blocked_by_data`, mismo
criterio que Fase 84B: falta un campo de calidad de tiro (xG) que el corpus
no tiene hoy.

**Ningún artefacto servido cambió.** T global 1.198935, peso 0.642848 y el
modelo de córners de Fase 84A siguen siendo exactamente los mismos; ningún
router, `APPROVED_MARKETS` ni `LiveProbabilityEngineV1` se modificó.

**Gates.** 51 pruebas existentes de `test_temperature_calibration.py`,
`test_team_count_markets.py`, `test_team_count_market_runtime.py` y
`test_repair_team_count_coverage_bias.py` sin regresiones -ningún archivo
servido se tocó, sólo se añadieron scripts nuevos de evaluación de
candidatos-.

## Más gráficas de volumen en "Mayor probabilidad" y refresco periódico (DEC-210)

Ver `DEC-210`. Tras DEC-209 el usuario no veía el diagrama de fiabilidad y
pidió más información visual, con refresco diario. Confirmado contra Postgres
de producción: no es un bug -el diagrama está vacío porque el bucket con más
muestra prospectiva de Fase 123 tiene 6 picks liquidados, contra el mínimo de
20 que exige `prospective_reliability`-. Mientras esa muestra crece (más rápido
ahora gracias a DEC-206), se agregaron dos gráficas que sí funcionan hoy con
poca muestra, derivadas en el cliente de `high_probability.picks` sin tocar el
backend: volumen y tasa cruda por mercado (`HighProbabilityMarketChart`, mismo
criterio honesto que `ShadowRateChart` para lo no confirmado) y volumen diario
liquidado (`HighProbabilityDailyChart`, sin tasa). Además, `DailyTrackRecord` y
`TrackRecord` ganan `refetchInterval` (2 y 5 minutos) para que las gráficas de
Aciertos se actualicen solas sin que el usuario recargue la pestaña.

Gates: 4 pruebas nuevas en `track-record-charts.test.ts`, Playwright extendida
para verificar las tres gráficas de "Mayor probabilidad" juntas. Typecheck, 22
Playwright de `navigation.spec.ts` y 215 Vitest sin regresiones. Despliegue a
producción pendiente.

## Diagrama de fiabilidad en Aciertos, expone un cálculo de Fase 123 que se tiraba (DEC-209)

Ver `DEC-209`. El usuario pidió cubrir con gráficos las cifras "4/11" sueltas
de Aciertos, con el corpus de matemáticas como supervisor.
`verificar_afirmacion` confirmó que un diagrama de fiabilidad -declarada vs.
observada, diagonal de referencia- es la visualización estándar de
calibración. Revisando el backend, `prospective_reliability()` (Fase 123) ya
calculaba exactamente esos datos pero **nunca se exponía por ningún
endpoint**.

`/v1/track-record` gana `high_probability_reliability` (mismo `window` que
`high_probability`, sólo en la ventana acumulada, no en el resumen diario). La
Mini App monta un `ScatterChart` con barra de error del IC95% de Wilson y una
diagonal de referencia dentro de "Mayor probabilidad", filtrando tramos sin
`sufficient_sample`. Además, una `ProportionBar` de conteo puro reemplaza el
texto suelto en el resumen de hoy, en el bloque de muestra insuficiente y en
el resumen de "Mayor probabilidad".

Gates: 2 pruebas nuevas del endpoint, 3 de `reliabilitySeries`, 1 Playwright
que renderiza el gráfico real y confirma que no truena en runtime. Suite
Python completa 926/934 -excluyendo el fallo preexistente y no relacionado de
`test_match_level_corpus.py`-, typecheck y 22 Playwright de
`navigation.spec.ts` sin regresiones. Despliegue a producción pendiente.

## Constructor de Picks: una sola probabilidad para varios mercados (DEC-208)

Ver `DEC-208`. Menú independiente `/constructor` en la Mini App. Desde
cualquier predicción pre-match, cada mercado gana un botón **+** / **−**:
1X2, Más de 2.5, Ambos marcan y todas las líneas de córners, tiros y tarjetas,
de la escalera auditada y de la rejilla adaptativa. El menú devuelve una única
probabilidad de que ocurran todos a la vez, y admite mercados de partidos
distintos.

**Cómo se combina, de menor a mayor supuesto.** Dos líneas de la misma
variable -mismo equipo, métrica y periodo- no son dos eventos, son uno: se
resuelven de forma exacta sobre la propia escalera ("más de 4.5" y "más de 6.5"
córners valen "más de 6.5", no su producto). Los mercados de gol del mismo
partido se resuelven sumando la masa de la matriz de marcadores sobre las
celdas que cumplen todas las condiciones: exacto, y "gana el local" con "gana
el visitante" da cero sin ninguna regla especial. Entre variables distintas del
mismo partido, y entre partidos distintos, se multiplica; el menú lo dice en
pantalla.

**Por qué la matriz se ajusta a las marginales publicadas.** El 1X2 pasa por
calibración de temperatura (`DEC-199`) y Ambos marcan viene de un modelo propio
(Fase 106), así que la matriz reconstruida no reproduce por sí sola lo que el
usuario acaba de leer. Se ajusta por escalado iterativo proporcional a las tres
marginales antes de sumar celdas, de modo que **una selección única devuelve
exactamente el porcentaje publicado**. Es el criterio de éxito que pidió el
usuario, y es lo primero que verifican las pruebas.

**Sin backend nuevo.** El cálculo vive entero en la Mini App y sólo lee campos
que `/v1/predict/upcoming` ya publica -`lambda_home`, `lambda_away` y
`audit.tau_dc`, que se expuso justamente para poder reconstruir la conjunta.
Ningún contrato cambia. Las selecciones viven en `localStorage`: no se
congelan ni se liquidan, así que no entran en el historial de aciertos.

Gates: 42 pruebas nuevas en `miniapp/tests/pick-builder.test.ts`, con los
valores de referencia de las conjuntas de gol calculados aparte y no derivados
del propio módulo; 200 Vitest/1 omitida sin regresiones; `tsc --noEmit` limpio
y `next build` resolviendo `/constructor`.

**Revisión en el navegador.** Se levantó la Mini App contra un stub local del
motor -no contra producción- y con `MINIAPP_BILLING_ENABLED=false`, que hace
que ni la titularidad ni el cupo consulten PostgreSQL: la revisión no tocó la
base ni escribió nada en ella. Los tres regímenes se comprobaron en la interfaz
real, no sólo en pruebas: cuatro mercados de un partido dieron `6.93%`, que es
exactamente 0.288888848 × 0.24 -conjunta de gol por matriz, más escalera
recortada en la misma variable-; "gana local" con "gana visitante" dio `0%`
declarándolo imposible; y el mismo mercado en dos partidos distintos dio
`24.0%` = 0.47 × 0.51. A 375 px no hay desbordamiento horizontal, las siete
entradas de navegación caben sin truncarse, ninguna fila de escalera desborda
su tarjeta y la barra flotante queda 8 px por encima de la barra inferior sin
taparla.

Dos defectos encontrados y corregidos en esa revisión: las cadenas visibles del
constructor salían sin acentos ("Mas de 2.5", "manda la linea mas alta")
mientras el resto de la aplicación sí los lleva; y el botón "+" medía 26×28 px
-por debajo de un objetivo táctil razonable siendo la interacción principal del
feature-, ahora 34×44 px mínimos.

Limitación: la captura de pantalla del panel de vista previa dejó de componer
fotogramas a mitad de la sesión, así que sólo hay una imagen -la pantalla de
predicción con los "+" del 1X2- y el resto se verificó por texto del DOM y
geometría medida, no por revisión de píxeles. Despliegue pendiente.

## Rediseño de la tarjeta compartible: matriz por equipo y banda de probabilidad (DEC-207)

Ver `DEC-207`. La tarjeta de `DEC-195` no cabía en pantalla y su contenido no
servía. Ahora publica los dos equipos con escudo, sólo el escenario principal
del 1X2, ambos marcan, y **una tabla por equipo** con córners, tiros y tarjetas
en filas y primera mitad / segunda mitad / completo en columnas.

**La regla que hace útil cada celda.** Se recorre la escalera entera, se
consideran las dos direcciones de cada línea, y se publica la de mayor
probabilidad dentro de `[0.55, 0.75]`. Es la misma regla que ya usa
`_recommendations` en el backend, con el techo bajado de 0.80 a 0.75. La banda
elimina las obviedades sin una lista de casos prohibidos: "Más de 0.5 córners"
ronda el 99% y queda fuera por sí solo. Una celda sin candidato en la banda se
deja vacía; la fila no se elimina, porque la tabla es una rejilla fija.

**Por qué esto sí cierra el dilema de DEC-195.** Allí se concluyó que una línea
over/under no puede ser informativa y decidida a la vez. Es cierto sólo si la
línea está fijada de antemano: teniendo la escalera completa, la banda
selecciona la línea en vez de al revés.

**Escudos.** Se descargan al congelar y se guardan como data URI en el payload,
vía el proxy `/v1/media/image` que ya valida host, tamaño y firma PNG. Servir
la imagen no sale a la red. Sin escudo se pinta el monograma de iniciales.

**Versión de formato.** `SHARE_CARD_VERSION` sube a 2; una tarjeta v1 no se
sirve (404) y se reconstruye en su sitio, con su mismo token, la próxima vez
que alguien comparta ese partido.

## Aciertos congelaba sólo 3 partidos al día, acoplado al canal en modo lite (DEC-206)

Ver `DEC-206`. Reporte del usuario: "Aciertos" muestra sólo 3 partidos.
Confirmado con Postgres de producción -exactamente 3 filas por cada lote de
congelación en `channel_predictions`, 7 lotes seguidos, sin excepción-: el
canal de Telegram corre en modo `lite` (`LITE_FIXTURE_LIMIT=3`, Fase 101 v1.1),
y ese mismo tope, pensado como cuota de mensajes de Telegram, también recortaba
qué se congelaba para liquidación. Como Aciertos, "Mayor probabilidad" y el
track record sólo leen `channel_predictions`, heredaban el mismo límite de 3.

Desacoplado por pedido explícito del usuario: el canal sigue en `lite`
-mismo volumen de mensajes-, pero `_daily`/`_same_day_catch_up` en
`telegram_channel_publisher.py` ahora congelan siempre el universo completo de
fixtures predecibles del día; `lite` sólo actúa en el nuevo `_select_publish`,
que decide cuáles de las predicciones ya congeladas reciben tarjeta/mercados en
el canal. La Mini App sube su ventana de `window=60` a `window=200` -el tope
real del backend- para no volver a quedarse corta ahora que se congela más por
día. Limitación aceptada, sin medir: el congelado completo multiplica las
llamadas a `/v1/predict/upcoming` frente a las ~3/día de antes.

Gates: test de `lite` reescrito para exigir congelado completo con publicación
recortada a 3; suite Python 949/950 -1 fallo preexistente y no relacionado en
`test_match_level_corpus.py`, reproducido en aislamiento antes del cambio-;
typecheck y 133 Vitest de la Mini App sin regresiones. Despliegue a producción
pendiente.

## Cierre de la ronda de candidatos: qué se promovió y qué quedó negado

Resultado final de medir todos los candidatos que el corpus disponible permite
evaluar. Ver `DEC-197`, `DEC-200`, `DEC-201`, `DEC-202`.

**Promovido y en producción:** peso de mezcla `0.8 → 0.642848` y recalibración
de 1X2 con temperatura `1.198935`. Composición: log-loss `+0.012928`, IC95%
`[+0.008054, +0.017857]`.

**Negado con evidencia bien alimentada -no por falta de muestra-:**

- **Ruido de proceso de Kalman** (`DEC-197`): con 1,845 partidos el log-loss
  crece monótonamente con la tasa y `0.02` degrada de forma confirmada
  (`[-0.018261, -0.002547]`). La primera medición, sobre 46 partidos, no podía
  distinguir; ésta sí, y dice que no.
- **Temperatura por liga** (`DEC-201`): indistinguible y peor que la global.
- **Peso de mezcla por liga** (`DEC-202`): sin contracción **degrada**
  (`[-0.012018, -0.000724]`); con contracción jerárquica recupera la paridad
  pero no mejora.

Los dos últimos comparten lectura: **la contracción jerárquica de R5 hizo su
trabajo** -impidió enviar a producción un sobreajuste que la habría empeorado-
aunque no encontrara señal adicional. El parámetro por grupo no se paga en
ninguna de las dos dimensiones probadas.

**Hallazgo sustantivo.** `DEC-200` dio a Kalman **más** peso (de `0.2` a
`0.357`) y `DEC-197` muestra que empeora al añadirle olvido temporal. Kalman
aporta a esta cadena como **segundo estimador estructural**, no como rastreador
de forma reciente — lo que refuerza corregir la documentación que lo llama
"estado temporal".

**Contracción jerárquica de conteos** (`DEC-203`): medida y rechazada como
mejora. Bate a la media de liga en córners, tiros y faltas, pero **no bate al
`shrinkage` fijo vigente** -degrada en córners y tiros a puerta-. El `k` constante
del proyecto está bien elegido para esta distribución de muestras.

**Marchenko-Pastur** (`DEC-203`): **estructura real encontrada**. Diez variables
de conteo, 1,895 partidos, banda de ruido `[0.8600, 1.1506]`: **tres autovalores
caen fuera** y concentran el **72.5%** de la varianza. La correlación entre
conteos es real y multidimensional, mientras `combined_dispersion` la modela con
un escalar por métrica entre local y visitante — hay correlación **entre métricas
distintas** que ese escalar no puede representar. No es promoción: encontrar
estructura no demuestra que explotarla mejore. Es la primera evidencia de que el
modelo de dependencia está subespecificado, y define un candidato concreto.

**Sin medir, por datos y no por análisis.** Contracción por árbitro o portero,
Cox para lesiones, minimax de formaciones, teoría de récords y el diagnóstico de
martingala requieren campos de ESPN que el corpus de Fase 74 no contiene. Su
medición depende de una ingesta previa, no de más trabajo sobre estos datos.

## Dos candidatos promovidos: peso de mezcla y calibración de 1X2

Ver `DEC-200` y `DEC-201`. Primer cambio en las probabilidades servidas desde
Fase 42, con evidencia que no cruza cero.

**El cuello de botella era la muestra, no los modelos.** `DEC-197` y `DEC-199`
quedaron inconclusos midiendo sobre 46 partidos de una liga: con esa muestra el
error estándar de log-loss (~0.076) es un orden de magnitud mayor que cualquier
delta plausible, así que ningún candidato podía demostrar nada.
`scripts/build_match_level_corpus.py` reconstruye el corpus causal de Fase 74 a
nivel partido -**9,465 partidos, 39 ligas**, 0 rechazos- conservando el `split`
que ese corpus ya tenía congelado, de modo que ningún candidato pueda elegir
dónde se mide. El bloque de confirmación pasa de 46 a **1,845 partidos**.

**Peso de mezcla: `0.8` → `0.642848` (`DEC-200`).** Fase 42 lo había congelado
sin ajustarlo sobre datos. Reestimado en selección y confirmado aparte:
1X2 log-loss `+0.008789` IC95% `[+0.004675, +0.012798]`; Brier `+0.005674`
IC95% `[+0.002846, +0.008372]`. **Efecto colateral medido antes de conectar**,
porque el peso altera las lambdas de las que también salen los otros mercados:
Over 2.5 mejora (`+0.004337`, IC `[+0.000056, +0.008488]`) y BTTS no se degrada
(indistinguible). La cola temporal de Kalman pesa más de lo que Fase 42 supuso.

**1X2 recalibrado con temperatura `1.198935` (`DEC-201`).** Se reajustó sobre el
blend ya reponderado, no sobre el anterior: adoptar la `T` medida con el peso
viejo habría calibrado un modelo distinto del servido. La composición supera a
cualquiera de los dos por separado: log-loss `+0.012928` IC95%
`[+0.008054, +0.017857]`. La fiabilidad pasa de `+0.0329` de sobreconfianza
-declara `0.5164`, acierta `0.4835`- a `-0.0101`. La variante **por liga fue
rechazada**: cruza cero y es peor que la global, así que el sesgo de calibración
resulta estable entre ligas y el parámetro extra no se paga. Eso corrige la
lectura de `DEC-199`, que sobre 46 partidos había concluido lo contrario.

**Dos defectos del arnés, corregidos antes de aceptar ninguna cifra.** La
historia se cortaba por **posición en la lista** en vez de por tiempo, colando
partidos con kickoff simultáneo al objetivo -la misma fuga que `DEC-113` cerró
en entrenamiento, reapareciendo en evaluación-. Lo detectó el guard
`history_not_strictly_before_cutoff` de la propia cadena: 1,647 fallos que
pasaron a 0. Y dos procesos escribieron el mismo JSONL entrelazando líneas, que
no falla al escribir sino al leer; el generador se niega ahora a sobrescribir.

**Gates.** 14 pruebas nuevas (6 de la cadena calibrada, 8 del arnés). El guard
`test_every_docker_artifact_survives_dockerignore` atrapó que el artefacto nuevo
no viajaba en la imagen -el fallo que perdió `eligibility.json` dos veces-, y
está corregido en `Dockerfile` y `.dockerignore`. Suite completa: 912 aprobadas;
los 17 fallos restantes son el conjunto conocido de contención de CPU y **pasan
96/96 en aislamiento** con el código de producción ya cambiado.

## Auditoría de composición matemática y contrato `model_composition v1`

Ver `DEC-196` a `DEC-199` y `docs/arquitectura_matematica_v1.md`. Auditoría
externa de los modelos y de **las conexiones entre ellos**, contrastada contra el
corpus `rag-matematicas` con libro y página por afirmación. Complementa la Fase
113, que verificó fórmula, causalidad y validez numérica por revisión de código;
ésta añade la pregunta que aquella no hacía: si dos piezas correctas pueden
encadenarse. **Ningún modelo se modificó y ninguna probabilidad servida cambió.**

**El paso de predicción de Kalman: implementado, con la tasa en cero
(`DEC-197`).** `KalmanV2Filter._update_batch` implementaba correctamente la
actualización -ganancia por pseudo-inversa, forma de Joseph, proyección
suma-cero-, pero no había ningún paso que sumara la covarianza de ruido de
proceso `Q` entre observaciones: los tres campos `process_noise_*` sólo se
validaban. Con `F=I` implícito y `Q=0` la covarianza sólo puede decrecer y el
filtro converge a la estimación de un parámetro casi estático.

El corpus decidió la forma de la corrección: *"si el estado latente evoluciona
como un proceso de difusión en tiempo continuo, la covarianza del ruido de
proceso acumulada entre dos observaciones depende de la duración del intervalo;
usar una covarianza constante por observación equivale a suponer que todos los
intervalos tienen la misma duración"* -SUPPORTED, Murphy p.1042-. Un calendario
de fútbol tiene intervalos de 3, 7, 15 y 60 días, así que **`Q` escala con `Δt`**
y los tres campos pasan a ser tasas por día, con tope de 120 días para que un
parón de verano no equivalga a no saber nada del equipo. El paso vive **dentro**
de `_update_batch`, de modo que el orden que exige R1 no depende de que cada
llamador lo recuerde. Ornstein-Uhlenbeck se descartó por coste de calibración
-añade un parámetro de reversión no estimable con una sola liga- y queda
documentado como continuación.

**La tasa queda en cero, y eso lo decidió la evidencia.** Barrido walk-forward
sobre 381 partidos con partición cronológica: en **selección** hay un óptimo
interior limpio -log-loss baja monótonamente de `1.062236` (tasa 0) a `1.051676`
(tasa 0.02) y vuelve a subir en 0.05-, pero en **confirmación se invierte**
(`0.850504` frente a `0.842441`). El bootstrap pareado con el partido como unidad
-10,000 remuestreos, 46 partidos- da IC95% `[-0.032335, +0.015097]` en log-loss y
`[-0.024213, +0.009566]` en Brier: **ambos cruzan cero**. El óptimo de selección
era ruido. Con las tasas en cero el paso de predicción es la identidad exacta, así
que **ninguna probabilidad servida cambia**. El hallazgo de comportamiento sigue
vigente y la documentación que llama "estado temporal" a la pieza sigue siendo
inexacta. Sellado en `artifacts/dec_197_kalman_process_noise/`.

**Markov y Hawkes no componen (`DEC-198`).** `DEC-092` congela que Markov
redistribuye las lambdas sin alterar su masa; `hawkes_v1.predict_snapshot` suma
un término de excitación no negativo, que es la definición correcta de un proceso
autoexcitado. Cada pieza es correcta por separado y no pueden serlo encadenadas.
Hoy no hay contradicción activa -Hawkes está fuera del router- pero la
incompatibilidad no es visible leyendo ninguno de los dos módulos, sólo al mirar
la composición. Queda como precondición de cualquier reconexión futura.

**Contrato nuevo.** `docs/specs/model_composition_v1.md` congela ocho reglas de
composición con libro y página -dónde va una recalibración, en qué bloque se
aprenden unos pesos de mezcla, exposición como offset y no como divisor, la
unidad IID, el filtrado de ruido antes del uso- y una tabla de seis capas donde
cada frontera corresponde a una regla. Es criterio de revisión, no de runtime.
De ahí se sigue una distinción que era fácil violar: `τ` de Dixon-Coles es
**generativo**, no una recalibración, y no puede moverse a la capa de
calibración.

**Escalado de temperatura para 1X2 (`DEC-199`): medido sobre datos reales y
rechazado para conectar.** `DEC-162` midió que 1X2 no alcanza fiabilidad en
ningún tramo y, a diferencia de los mercados binarios, no tiene recalibración
posterior. `src/temperature_calibration.py` añade la pieza: un único parámetro
`T` que **no altera cuál resultado es el más probable** -`x^(1/T)` es monótona
creciente-, sólo la confianza declarada.

Ajustado sobre los mismos 381 partidos y la misma partición: en selección
`T = 1.6801` y la log-verosimilitud baja de `1.062236` a `1.038647`; **en
confirmación empeora** -log-loss `0.842441 → 0.916426`, Brier
`0.486911 → 0.537283`-. El diagnóstico de fiabilidad explica por qué y es más
útil que el número: en confirmación el modelo declara `0.5271` de confianza media
en su argmax y acierta `0.5870`, o sea está **infra**confiado (brecha `-0.0598`);
una `T` ajustada para corregir sobreconfianza aplana todavía más algo que ya iba
corto, y la brecha se abre a `-0.1363`. **El sesgo de calibración cambia de signo
entre los dos bloques cronológicos**, que es evidencia empírica de que un
parámetro de recalibración describe el sesgo de su población y no transfiere sin
verificarlo. La pieza sigue implementada, correcta y sin conectar -ahora con
evidencia detrás de esa separación, no sólo cautela-. Sellado en
`artifacts/dec_199_temperature_calibration/`.

**Gates.** 18 pruebas nuevas: 9 en `tests/test_model_composition_v1.py` -que
`Δt=7` inyecte exactamente `7/3` de lo que inyecta `Δt=3`, el tope de 120 días,
que el orden predicción→actualización coincida con la composición explícita, que
las tasas en cero reproduzcan el filtro anterior de forma exacta, y que la
intensidad Hawkes exceda estrictamente a la de Markov- y 9 en
`tests/test_temperature_calibration.py`.

**915 pruebas, todas aprobadas en aislamiento.** La corrida completa devuelve 17
fallos en `test_catalog_caching.py`, `test_dikamaha_service.py`,
`test_phase_118_track_record.py` y `test_phase_122_high_probability.py`.
Verificado que son contención de CPU y no regresión, con tres mediciones: la
corrida limpia al inicio de esta sesión -antes de estos cambios y antes de
levantar Docker- dio **912 aprobadas / 0 fallos**; los cuatro archivos pasan
aislados (58/58 y 38/38); y el conjunto de fallos varía entre corridas. Son todos
tests de temporización, concurrencia y caché, y el contenedor de Postgres compite
por CPU. Es el mismo patrón que `DEC-188` documentó, medido de nuevo en vez de
darlo por conocido.

**Tres autocorrecciones registradas.** El corpus rechazó tres afirmaciones de la
propia auditoría y, al investigar por qué en vez de reformularlas, aparecieron
errores reales -la transformación arcoseno era innecesaria porque el proyecto ya
usa GLMs; la "correlación espuria" entre posesiones es una identidad algebraica,
no un artefacto composicional-. Quedan visibles en
`docs/arquitectura_matematica_v1.md`.

La skill `dikamaha-math-supervision` conserva el material de referencia y la
técnica de formulación que hace que una verificación contra el corpus sirva.

## Tarjeta pre-match compartible por link (DEC-195)

Ver `DEC-195`. Una predicción se puede compartir fuera de la Mini App como
imagen con marca de agua, no como texto.

**Cómo funciona.** En el detalle de un partido, "Compartir tarjeta" llama a
`POST /api/share` (con sesión y CSRF), que congela la predicción en
`shared_prediction_cards` y devuelve un token. El link `/s/<token>` es
**público**: lo abre cualquiera, sin cuenta ni Telegram. Su vista previa en
WhatsApp y Telegram es ya el PNG de la tarjeta, servido por
`/s/<token>/image`.

**Qué lleva.** 1X2, Más de 2.5 y Ambos marcan como probabilidades; y —por
primera mitad, segunda mitad y partido completo— córners, tiros y tarjetas del
lado `total` como **media esperada con su rango central del 60%**, no como
línea over/under. Marca de agua "DIKAMAHA" en diagonal sobre todo el lienzo,
más el pie legal de siempre.

**Por qué no son líneas over/under.** Una línea única no puede ser informativa
y decidida a la vez: cerca del centro de la distribución es ~50% por
definición, y lejos del centro es ~certeza. Además la rejilla topa sus líneas
en 9.5 y los tiros la superan en cualquier periodo, así que la tarjeta habría
repetido "Tiros · Más de 8.5" en las tres mitades con 77%, 87% y 100% — que no
dice nada del partido, sólo que dura más que una mitad. La media se lee de
`distributional_market_view` (PMF sin tope) y el intervalo son los cuantiles
20% y 80%, la misma definición que `_central_interval`.

**Dos cosas a tener presentes.** Es la primera superficie de la Mini App sin
autenticación: `TelegramAuth` y `AppShell` se saltan bajo `/s/`, decidido en
`lib/public-routes.ts`. Y la tarjeta es inmutable —una por partido, congelada
en el primer compartir— por decisión explícita: el link difunde lo que el
modelo dijo antes del kickoff y no cambia después.

`scripts/render-share-card.ts` escribe un PNG de muestra sin servidor ni base
de datos. Es la única forma de revisar el diseño: Satori no recorta lo que se
desborda, pinta encima y no da error.

## La ventana "Aciertos" mostraba sólo una parte del día (DEC-194)

Ver `DEC-194`. Tres defectos independientes, cualquiera de ellos suficiente
por sí solo para recortar la ventana.

**Los mercados de equipo nunca se liquidaron.** `_snapshot_lines`
(`src/telegram_channel_publisher.py`) buscaba `bounded_market_grid_view` en
dos formas de snapshot que ninguna ruta de producción produce; la real la
guarda bajo `experimental_team_markets`. Resultado:
`prediction_settlements.shadow_verdicts` estaba vacío en **todos** los
partidos desde que existe Fase 118 — córners, tiros y tarjetas, en las tres
divisiones de periodo, jamás llegaron a "Aciertos". Sin rastro en logs: una
rejilla no encontrada se ve igual que una ausente. Las cuatro pruebas del
módulo pasaban porque alimentaban a mano una de las formas irreales.
`_snapshot_grid` lee ahora las tres, sin migrar ninguna fila sellada.

**Los partidos descubiertos tarde no se congelaban nunca.** `_daily` decide
el conjunto del día a las 09:00 de la víspera y lo cierra para siempre. Un
fixture que ESPN publica después, una liga que falló en ese único barrido o
un 422 puntual de `/v1/predict/upcoming` dejaban al partido fuera de
`channel_predictions` de forma permanente, y sin predicción congelada
`_results` no lo recorre. `_same_day_catch_up` congela cada media hora lo
que falte del día en curso, siempre antes del kickoff (lo ya empezado se
cuenta en `same_day_late` y se descarta).

**La ventana diaria pedía el día equivocado media tarde.**
`DailyTrackRecord` calculaba "hoy" en UTC mientras
`/v1/track-record/daily` agrupa por fecha local de México, así que a partir
de las 18:00 consultaba mañana — justo cuando se liquidan los partidos de
la tarde-noche. `channelDateParam` usa ya `America/Mexico_City`.

## Reparto justo por liga y validación de fixture_id en alertas (DEC-193)

Ver `DEC-193`. Cierra los dos hallazgos que `DEC-192` dejó documentados sin
corregir, con la opción recomendada elegida por el usuario para ambos.

**Reparto por liga.** `allocate_fixtures_fairly()` (nueva en
`src/espn_fixture_resolver.py`) reemplaza el `sorted(...)[:limit]` puro de
`/v1/upcoming` y `/v1/live` por un reparto en ronda: como mucho un fixture
por liga antes de tomar un segundo de cualquiera. Un torneo con muchos
kickoffs simultáneos ya no puede agotar el cupo por sí solo. Ambos
endpoints declaran `truncated`/`leagues_with_hidden_fixtures` cuando el
cupo no alcanzó, y la miniapp lo muestra con un aviso discreto en
`/upcoming` y `/live`.

**Validación de alertas.** El formulario de alta en `/subscriptions`
consulta `/api/upcoming` y `/api/live` -filtrados por la liga que ya es
obligatoria- antes de guardar, y rechaza con mensaje claro un `fixture_id`
que no aparece en ninguno. Degrada seguro: si la validación falla, deja
pasar el alta en vez de bloquearla.

**Gates.** 6 pruebas nuevas para `allocate_fixtures_fairly`, 7 Playwright
nuevas (3 de truncamiento, 4 de validación de alertas). Suite Python 890
aprobadas / 8 omitidas / 0 fallos reales; typecheck, 65 Vitest y 62
Playwright sin regresiones.

## Auditoría extensiva de producción — acceso real a Postgres, "errores bomba"

Ver `DEC-190`, `DEC-191`, `DEC-192`. Esta sesión obtuvo por primera vez acceso
de lectura al PostgreSQL real de producción (proxy público de Railway, con
autorización explícita del usuario) y, a partir de los datos reales, encontró
el defecto dominante detrás de que "Mayor probabilidad" y "Resultados de hoy"
casi nunca liquiden mercados de equipo.

**El hallazgo más grave (`DEC-190`).** `resolve_team_market` y
`_shadow_verdicts` buscan la clave `"full_match"` en el diccionario de
periodos que trae `explorer_statistics`, pero esa fuente sólo expone
`first_half`/`second_half`/`total` -nunca `full_match`-. Confirmado con datos
reales: **0 de los 25** picks liquidados históricamente son de partido
completo -todos son de mitad-, mientras que `full_match` es **611 de 876
(70%) de todo el universo congelado**. Las pruebas existentes no lo
detectaban porque construían su propio `statistics` de prueba con la forma
incorrecta, reproduciendo el defecto en el fixture en vez de la forma real.
Corregido con `observed_team_count()`, un único punto de traducción
compartido en `settlement_store.py`.

**Auditoría de "bomba" en los cinco procesos de larga duración.** Dos
agentes de exploración barrieron los procesos desplegados en producción y
encontraron el mismo patrón exacto del bug de `_results()` (DEC-189) en seis
lugares más, tres de severidad alta idéntica: el offset de long-polling de
**ambos bots de Telegram** se quedaba clavado para siempre ante un update
"veneno" -el hallazgo más grave de este bloque, puede bloquear el bot
completo incluso tras reiniciar-; la publicación de tarjetas/mercados del
canal; la asignación de escudos por liga; el resumen diario; el ciclo de
liquidación de Fase 123; y el worker de alertas de la miniapp. Los seis se
corrigieron con el mismo patrón ya validado: aislar cada item con su propio
`try/except`, loguear con identidad, seguir con el siguiente.

**Índice faltante (`DEC-192`).** `high_probability_pick_freezes` no tenía
ningún índice más allá de su llave primaria, a diferencia de su tabla
hermana. `EXPLAIN` contra producción confirmó *seq scan* + *sort* completos
en cada corrida de `unsettled()` -cada 30 minutos-, sobre una tabla que
crece ~290 filas/día sin límite. Migración 015 preparada, sin aplicar
todavía contra producción.

**Hallazgos documentados, sin corregir -decisión de producto pendiente-.**
`CATALOG_MAX_LIMIT=20` es un tope global (no por liga) compartido por 8+
consumidores; un solo día con muchos kickoffs simultáneos en una liga agota
el cupo y ningún partido de las otras 62 aparece, sin ninguna señal en el
contrato de que hay más partidos de los mostrados. El worker de alertas sólo
vigila fixtures ya en vivo (tope 20/liga) mientras el formulario de alta
acepta cualquier `fixture_id` sin validarlo contra ese universo.

**Gates.** Suite Python 871 aprobadas / 8 omitidas / 0 fallos en aislamiento
(mismo conjunto de fallas por contención de CPU ya documentado, reproducido
y descartado de nuevo); typecheck, 65 Vitest y 55 Playwright sin
regresiones. La migración 015 se aplicó contra producción con confirmación
explícita del usuario -`EXPLAIN` confirma el cambio de plan a *Index Scan*-;
el despliegue de los cambios de código sigue pendiente.


## Diagnóstico de los 43 picks estancados en `still_pending` (auditoría nocturna)

Ver `DEC-189`. Continuación de la limitación abierta que dejó `DEC-184`: sin
acceso a PostgreSQL de producción, se auditó el código a fondo para acotar el
mecanismo con la evidencia disponible -la forma exacta del síntoma reportado.

**Hallazgo principal.** `_results()` (`src/telegram_channel_publisher.py`)
itera las predicciones congeladas **ordenadas por kickoff, la más antigua
primero**, y llamaba a `_settled_result(row)` sin ningún `try/except`. Una
excepción sin capturar en esa llamada abortaba el bucle **completo**,
incluidas todas las filas más nuevas detrás de la que falló -y el ciclo
seguía completando con `channel_cycle_completed` en cero, porque la
excepción interrumpía `_results` a medio bucle, antes de que `run_cycle`
llegara a `return counts`-. Si la fila que fallaba era persistente, quedaría
bloqueando a las mismas filas nuevas cada ciclo: la firma exacta de "43
picks estancados, sin variar, sin ningún log de error" que reportó DEC-184.
Corregido: cada fila se aísla con su propio `try/except`, registrado como
`channel_settlement_row_failed`, y el bucle sigue con la siguiente.

**Hallazgo secundario.** `_settled_result` ubicaba el partido por fecha de
calendario (`_final_fixture`), y no dejaba ningún rastro cuando esa búsqueda
fallaba -a diferencia del rechazo de reconciliación, que sí logueaba-. Un
partido que el proveedor archiva bajo otra fecha (aplazamiento, reindexado)
quedaría invisible para siempre a esa búsqueda. Se añadió un respaldo
indexado por `match_id` (`explorer_statistics`, inmune a esa fragilidad) que
se intenta después de `STALE_FIXTURE_LOOKUP_GRACE` (12h post-kickoff) y deja
constancia en el log incluso si tampoco resuelve nada.

**Limitación que se mantiene.** No se pudo confirmar con evidencia de
producción cuál de los dos mecanismos era la causa real de los 43 picks
específicos, ni si siguen estancados hoy -sigue exigiendo lectura directa de
PostgreSQL con credenciales propias del usuario-. Ambos mecanismos son reales
y están confirmados por código y por prueba; la próxima vez que ocurra algo
similar, los logs nuevos deberían bastar para diagnosticarlo sin acceso a la
base.

**Gates.** 3 pruebas nuevas en `tests/test_phase_101_telegram_channel_
publisher.py` (aislamiento por fila, respaldo antes/después de la gracia,
respaldo sin finalidad confirmada) y 3 en `tests/test_espn_user_explorer.py`
para `_summary_status`. Suite Python completa 855 aprobadas / 8 omitidas / 0
fallos en aislamiento -mismas fallas de contención de CPU que DEC-188 ya
documentó, reproducidas y descartadas de nuevo-.


## Cuatro defectos reportados: in-live, córners, segundo tiempo y línea imposible

Ver `DEC-185`, `DEC-186`, `DEC-187` y `DEC-188`. Reporte del usuario con
cuatro síntomas que resultaron tener causas distintas, tres de ellas
introducidas por los despliegues de las últimas 24 horas.

**Diagnóstico previo.** Antes de tocar nada se ejecutó
`scripts/diagnose_prematch_market_views.py` (nuevo, sólo lectura) contra el
runtime real. Descartó la hipótesis de que la "Rejilla adaptativa por
periodo" estuviera rota: en `esp.1`/`eng.1`/`ita.1` publicaba sus 21 filas
con córners y los tres periodos, y el Markov de Fase 88 estaba disponible.
Lo que faltaba era todo de la **escalera auditada**, que traía 12 filas sin
córners y sin segundo tiempo.

**Córners y segundo tiempo (`DEC-188`).** La causa de los dos era la misma y
no era la que DEC-183 registró. `_select_alpha_clean` ajustaba el modelo y
elegía `alpha` con las filas limpias de la reparación de DEC-173, pero
pasaba la lista **sin filtrar** a `_select_count_weight`: las 5,082 filas de
ligas donde el proveedor nunca entregó córners empujaban el peso óptimo de
mezcla a `0.0`, y DEC-183 leyó ese cero como evidencia de que córners no
tenía señal por equipo, retirándolo de la escalera en todas las ligas. Con
la selección corregida el peso es `0.9` en los tres periodos y tiros sube de
`0.1` a `1.0`. Se descartaron con medición dos alternativas antes de llegar
ahí: ampliar el corpus al snapshot activo sólo aporta +9.2% de partidos
útiles para córners, y el peso por liga -que parecía prometedor, 0.8-1.0 en
las 9 ligas con muestra- no mejora nada contra el split de confirmación,
porque esa "señal por liga" era la señal global que la fuga escondía.
En paralelo, `CountMetricSpec.first_half_only` pasa a `period` con tres
valores y `METRIC_LADDERS` gana las entradas de segunda mitad, así que ese
periodo por fin es auditable: `scripts/run_ladder_audit.py` regenerado da 512
celdas publicables (antes 264) y **181 con ventaja real del modelo** (antes
101). La escalera auditada de `esp.1` pasa de 12 a 30 filas, con córners y
segundo tiempo.

**Mínimo un mercado por probabilidad (`DEC-187`).** El objetivo del usuario
chocaba con DEC-182, que había retirado esa garantía para evitar obviedades.
Se reconcilian con dos niveles: la banda `[0.60, 0.85]` manda, y sólo si un
grupo queda vacío se publica la línea más cercana dentro de una cota dura
`[0.55, 0.90]`, etiquetada como fuera de banda. El caso que motivó DEC-182
-"menos de 0.5 córners, 96%"- sigue rechazado por el techo. El reparto de
mercados del menú pasa de tarjetas 48% / tiros 27% / tiros a puerta 25% /
**córners 0%** a tarjetas 30.2% / tiros 30.2% / **córners 29.6%** / tiros a
puerta 9.9%, con cobertura por grupo entre 94.6% y 99.9%.

**Línea imposible en Aciertos (`DEC-186`).** Dos rutas, las dos vivas. La
rejilla no tenía compuerta positiva de cobertura y `_status` cortocircuitaba
en `insufficient_evidence` antes de mirar los ceros, así que `uru.1` (8 de 8
equipos-partido sin un córner) y `esp.super_cup` (12 de 12) publicaban
córners inventados; corregido con el límite inferior de Wilson, que concluye
ausencia con muestra chica pero unánime sin castigar a
`concacaf.nations.league`, que tiene córners reales y poca muestra. Además
`_centered_lines` anclaba la selección en la constante `VISIBLE_LINE_MIN =
1.5` cuando la intensidad rondaba cero -de ahí el "menos de 1.5" literal-, y
ahora una guarda descarta esos grupos. La segunda ruta era `pick_view`, que
republicaba las filas congeladas antes de DEC-182 con la dirección del
histórico invertida; `is_publishable` deja de publicarlas sin borrarlas, que
es lo que DEC-182 estableció.

**Pantalla in-live (`DEC-185`).** Cinco defectos independientes, ninguno en
la inferencia: el total de tiros del proveedor se destruía al recalcularlo
cuando ESPN no manda el desglose; la curva de presión seguía derivándose
sólo de `events` -la limitación que DEC-176 dejó abierta- y decía "todavía no
hay acciones suficientes" en competiciones donde nunca las habrá; el
indicador de confianza leía una clave que el motor no emite e imprimía
siempre "calculada"; la tabla de periodos pintaba nueve guiones en el camino
de fallback; y un cero real era indistinguible de un dato no publicado. El
backend ahora declara `pressure_granularity` y `unavailable_metrics`, y la
interfaz explica cada caso en vez de fingir un dato.

**Gates.** Suite Python 867 aprobadas / 8 omitidas / **0 fallos**, typecheck,
build Next, 65 Vitest y 55 Playwright, todo en verde. Se corrigió además una
bomba de tiempo preexistente: `test_a_league_without_corner_coverage_
publishes_no_corner_ladder` fijaba `kickoff_ts` al 2026-08-13 y empezó a
fallar por el paso del reloj. **Corrección de registro:** DEC-183 y DEC-184
declararon "~15 fallas preexistentes de orden" en
`test_catalog_caching.py`/`test_phase_122_high_probability.py`; la línea base
medida sobre HEAD limpio en esta sesión da 849 aprobadas y una sola falla (la
bomba de tiempo). Esas ~15 son sensibles a contención de CPU -aparecen si se
corre la suite mientras otra tarea pesada ocupa la máquina y desaparecen al
correrla sola-, no preexistentes; no conviene volver a darlas por conocidas
sin medirlas.

**Limitación abierta heredada.** Los 43 picks estancados en `still_pending`
que `DEC-184` no pudo explicar siguen sin diagnosticar: exige lectura directa
de PostgreSQL de producción, fuera del alcance de esta sesión.

## Integración de "Mayor probabilidad" en la ventana de Aciertos (auditoría nocturna)

Ver `DEC-184`. Reporte del usuario: "Aciertos" no publicó nada hoy pese a
haber partidos jugados y predicciones congeladas. Causa raíz: `/v1/track-
record` y `/v1/track-record/daily` sólo leían `prediction_settlements`
(Fase 118), poblada exclusivamente por el ciclo propio del canal
(`_daily`/`_freeze_all`/`_results`); el menú "Mayor probabilidad" congela y
liquida sus picks en un ciclo aparte (`high_probability_pick_freezes`/
`high_probability_pick_settlements`, Fase 122/123) que DEC-177 dejó
deliberadamente sin conectar a Aciertos "como ampliación posible y separada
si se pide explícitamente" -este pedido es exactamente ese caso-. Logs de
producción del propio 2026-08-13 muestran `channel_cycle_completed` en cero
en todos sus contadores durante toda la ventana observada (~5 min por ciclo,
ocho redeploys) y `phase123_cycle_completed` con 43 picks estancados en
`still_pending`/0 liquidados sin variar; sin acceso SQL directo (`DATABASE_
URL` redactado por la conexión Railway usada) no se pudo confirmar si el
ciclo del canal estuvo genuinamente ocioso o si `_settled_result` falla en
silencio -queda como limitación abierta en `DEC-184`, no resuelta aquí-.
Reparado el problema pedido explícitamente: `src/high_probability_settlement.
py::pick_view` (+ `settlements_for`/`frozen_for` en el repositorio) publica
los picks de Fase 123 -pendientes incluidos, nunca se ocultan por resultado,
DEC-158/161- reutilizando exactamente el `market`/`direction`/`metric`/
`team_side`/`period`/`line` que el menú ya congeló, sin recalcular nada.
`/v1/track-record` y `/v1/track-record/daily` ganan la clave aditiva
`high_probability`; la Mini App suma la tarjeta "Mayor probabilidad" a
`DailyTrackRecord`/`TrackRecord`, y `DailyTrackRecord` ya no queda en blanco
cuando el canal no liquidó nada ese día -el síntoma reportado- porque ahora
también revisa ese segundo bloque. Sin migración de esquema: reutiliza las
tablas de Fase 123 ya creadas. Gates: suite Python completa sin regresiones
nuevas (mismas ~15 fallas de `test_catalog_caching.py`/`test_phase_122_high_
probability.py`, order-dependent, reproducidas idénticas en HEAD limpio antes
de este cambio), Vitest y Playwright nuevos, typecheck y build Next sin
regresiones.

## Córners sin variabilidad por equipo en "Mayor probabilidad" (auditoría nocturna)

Ver `DEC-183`. Reporte del usuario: el menú muestra prácticamente las mismas
probabilidades y líneas en todos los partidos, como si tratara a todos por
igual. Comparado contra tres enfrentamientos reales de `esp.1` (Real Madrid-
Leganés, Leganés-Valladolid, Atlético-Alavés), `home_corners` esperado
(`9.072`), `away_corners` (`7.277`), `total_corners` (`16.349`) y
`home_corners_first_half` (`3.971`) salían **idénticos** en los tres,
mientras tiros y tarjetas sí variaban por equipo. Causa: `_expected` mezcla
`weight * modelo + (1 - weight) * baseline`, `baseline` depende sólo de
(liga, localía) -nunca del equipo-, y el artefacto vigente de Fase 84A tiene
`model_weights["corners"] == 0.0` y `["corners_first_half"] == 0.0` -córners
son 6 de los 18 grupos de la escalera, un tercio del menú-. Confirmado con
`selection.json` que ese peso cero es la elección correcta y ya auditada
-el deviance de córners empeora de forma monótona en cuanto se usa el
modelo-, consecuencia de la reparación de sesgo de cobertura de DEC-173, no
un bug de código: forzar un peso distinto habría revertido esa reparación
válida (se probó por error al ejecutar `run_phase_84a_team_count_markets.py`
sin el paso de reparación; el artefacto se restauró con `git checkout` antes
de continuar). Reparado en el punto correcto: `_audited_market_ladder_view`
omite cualquier métrica con `model_weights <= 0.0`, igual que ya omite
métricas sin cobertura (DEC-182) o ausentes. Efecto: córners y córners 1ª
mitad dejan de aparecer en la escalera/"Mayor probabilidad" en cualquier
liga hasta que una fase de modelado futura entrene una versión con señal
real por equipo; el mercado fijo `home_corners_over_4_5`/
`away_corners_over_4_5` de `user_market_view` no se toca. Tiros y tarjetas
siguen publicándose y quedan confirmados variando por equipo. Gates: suite
Python completa sin regresiones (1 prueba nueva, 1 ajustada).

## Auditoría y reparación de "Mayor probabilidad" (reporte de usuario)

Ver `DEC-182`. Un reporte concreto -"córners de ambos equipos, menos de 0.5,
96%" en Tobol–Partizan, imposible- destapó **cuatro defectos encadenados**,
todos reproducidos contra el fixture real de producción:

1. **Dirección del histórico invertida.** `observed_rate_historical` es
   siempre la tasa del `over`; se publicaba tal cual también para picks
   `under`. El 96% era la frecuencia de que hubiera **más** de 0.5 córners;
   la real del under era 3.83%. Afectaba a todos los picks `under`, y el
   menú ordena por esa cifra, así que los peores subían al tope.
2. **24 de 63 ligas servidas sin veredicto de cobertura.** El mapa se
   construía del corpus de Fase 74 (39 ligas, cero filas de las 14 de Fase
   120). Con `MetricCoverage` degradando abierto, esas ligas publicaban
   mercados sobre datos que el proveedor nunca entregó.
3. **La banda no cubría la cifra publicada** y el `fallback_outside_band` de
   DEC-179 dejaba pasar justamente las obviedades que debía evitar.
4. **La escalera heredaba fiabilidad global sin cobertura local**: sus
   veredictos no tienen dimensión de liga.

Reparado: tasa e intervalo por dirección publicada; mapa de cobertura
regenerado desde el snapshot activo (39 → 56 ligas, 21 con métricas
ausentes; **cero desacuerdos** con el mapa validado en las 39 comunes, así
que el cambio es aditivo); banda aplicada a las dos cifras y mercado omitido
cuando ninguna línea califica; y `is_covered` como precondición positiva de
la escalera. Efecto: el caso reportado pasa de 18 a 4 picks, todos de
tarjetas -la única métrica con cobertura real ahí-, mientras `esp.1` conserva
15 con líneas informativas y ninguna obviedad. Verificados los tres
consumidores aguas abajo (canal Telegram, ventana de aciertos y detalle
pre-match). Gates: 821 Python/8 omitidas, typecheck, build Next y Playwright
sin regresiones.

## Investigación — lentitud del catálogo live y progreso real

Ver `DEC-181`. Medido contra ESPN real: un barrido en frío de "Partidos en
vivo" (63 ligas x 3 días D-1/D/D+1 = 189 combinaciones) tarda **33.4 s** con
12 conexiones concurrentes; subir a 32 no ayuda (32.3 s, ESPN throttlea por
concurrencia, no es un cuello de botella del proceso). La causa evitable real
era el TTL de la caché (15 s) por debajo del ciclo de refresco de la Mini App
(20 s): casi cada refresco pagaba de nuevo el barrido completo. Subido a 25
s. Se añadió progreso real del barrido -`LiveScanProgress` en memoria,
`GET /v1/live/progress`, sondeado cada 400 ms- que muestra "N de 189
combinaciones liga/fecha revisadas" con una barra cuyo ancho es el avance
real, no una animación indeterminada. La ventana D-1/D/D+1 no se tocó: es la
protección de Fase 115 contra catálogo vacío cerca de medianoche UTC. Gates:
6 pruebas nuevas de progreso, 1 de endpoint, 2 Playwright, suite completa sin
regresiones.

**Fase activa:** Fase 123 Validación prospectiva del menú de mayor probabilidad
**Objetivo Fase 123:** convertir la evidencia histórica post-hoc de Fase 122 en
confirmación prospectiva real, congelando los picks del menú antes del
kickoff y liquidándolos después contra el resultado verificado.

## Corrección — Mayor probabilidad agrupa por partido, no por pick global

Ver `DEC-180`. El diseño de DEC-179 generaba bien hasta 18 picks por partido,
pero `GET /v1/high-probability` seguía ordenando todos los picks de todos
los partidos por tasa observada y cortando en `limit`: con muchos partidos
escaneados, uno o dos con líneas fuertes desplazaban del todo los mercados de
los demás. Ahora `limit` acota **partidos** (orden cronológico), y cada
partido incluido aporta todos sus mercados. La Mini App se reestructuró para
mostrar una tarjeta por partido con sus mercados agrupados por periodo, en
vez de una tarjeta por pick suelto. Gates: 813 Python/8 omitidos, typecheck y
7 Playwright de `high-probability.spec.ts` sin regresiones.

## Mayor probabilidad alimentada por la escalera auditada

Ver `DEC-179` y `docs/objetivo_auditoria_modelos_v1.md` (Etapa 4). Los
mercados de equipo de "Mayor probabilidad" (Fase 122/123) ya no salen de las
nueve líneas fijas de `MARKET_METADATA` + `eligibility.json`: salen de
`audited_market_ladder_view`, con `src/ladder_pick_selection.py` eligiendo
por cada uno de sus hasta 18 grupos la línea menos extrema dentro de una
banda de confianza `[0.60, 0.85]` -evita tanto el volado como lo obvio-, con
reserva garantizada si ninguna línea cae en la banda. Nunca falta al menos
una estadística por mercado cubierto. 1X2/Over 2.5/Ambos marcan siguen
exactamente igual, gobernados por el gate de Fase 122; DEC-162 ya midió que
ninguno lo supera. Las dos fuentes degradan por separado: un gate de gol
caído ya no vacía los mercados de equipo. De paso se corrigió un bug
preexistente (`"half"` en vez de `"first_half"` en `_audited_market_ladder_
view`) que dejaba la Escalera Auditada de DEC-178 sin mostrar nunca córners
ni tarjetas de primera mitad. Sin migración de esquema: `HighProbabilityPick
Freeze` ya tenía columnas independientes para metric/team_side/period/line.
Gates: suite Python completa 812 aprobadas/8 omitidas, typecheck, build Next
y Playwright (incluida `high-probability.spec.ts`) sin regresiones.

## Corrección de presentación — un solo bloque de mercados de equipo en pre-match

Ver `DEC-178`. El detalle pre-match de la Mini App (`prediction-detail.tsx`)
mostraba tres bloques sobre el mismo dato -córners/tiros/tarjetas por equipo-
con niveles de evidencia distintos: "Mercados de equipo" (líneas fijas de
Fase 84A/88/89), "Rejilla adaptativa por periodo" (Markov de Fase 88, sin
auditar) y "Escalera auditada" (Fase 84A reparado, calibrado y con doble
ventaja bootstrap medida celda por celda, ver
`docs/objetivo_auditoria_modelos_v1.md`). Se retiran los dos primeros de esta
pantalla y queda sólo la escalera auditada, la única con calibración y
fiabilidad verificadas contra histórico real. Sin cambios de backend ni de
contrato: `user_market_view` y `bounded_market_grid_view` siguen sirviéndose
intactos para "Resultados de hoy" (DEC-177) y el menú de Fase 123. Gates:
typecheck, build Next, 45 Vitest y 41 Playwright aprobados (2 pruebas de
`navigation.spec.ts` migradas a lo que sigue existiendo).

## Fase 123 — Validación prospectiva del menú de mayor probabilidad

Implementada. Ver `DEC-171`. Paso 3 del plan de cierre del proyecto.

- `src/high_probability_settlement.py` añade dos tablas nuevas
  (`high_probability_pick_freezes`, `high_probability_pick_settlements`) y
  `scripts/run_phase_123_high_probability_prospective.py` corre el ciclo de
  congelar/liquidar, mismo patrón operativo que Fase 101 (`--once`,
  `--dry-run`, `DATABASE_URL` con respaldo SQLite local, degradación segura
  sin base de datos configurada);
- congela cada pick de `GET /v1/high-probability` con su hash y su fixture
  antes del kickoff (`freeze_from_pick`, idempotente por `pick_key`), y
  rechaza congelar cualquier fixture cuyo kickoff ya haya pasado al momento
  del ciclo, aunque la API lo hubiera devuelto;
- liquida sólo cuando `prediction_settlements` (Fase 118) ya tiene fila para
  ese `fixture_key` -estado final, marcador reconciliado y `kickoff + 3h` ya
  certificados por esa fila, sin repetir la espera-; 1X2/Over 2.5/BTTS salen
  directo de `official_verdicts`;
- los nueve mercados de equipo de `MARKET_METADATA` NO se liquidan contra
  `shadow_verdicts`: esa liquidación corre sobre la rejilla dinámica de Fase
  102 con líneas centradas en P(over)≈50% por partido, mientras el menú usa
  la línea fija de Fase 84A/88/89, sin garantía de que ambas vistas liquiden
  la misma línea exacta. Se liquidan en su lugar contra `explorer_statistics`
  directo con la línea fija del propio pick, reutilizando `team_market_hit`
  -extraída de `_shadow_verdicts` a `src/settlement_store.py` sin cambiar su
  resultado, cubierto por los 21 tests existentes de Fase 118 sin
  regresiones-;
- `prospective_reliability` agrega por (mercado, tramo de confianza) con el
  mismo umbral mínimo de muestra e intervalo de Wilson que el track record
  oficial; no decide gate ni promoción, sólo publica la cifra comparable
  contra la tasa declarada por el gate v2 post-hoc de Fase 122.

Estado: `railway_deployed_first_cycle_fixed`. Desplegado dentro del proceso
que ya corre `TelegramChannelService` en el contenedor de `DIKAMAHA-PreMatch`
-decisión del usuario de no crear un servicio Railway nuevo-, con su propio
ciclo cada `HIGH_PROBABILITY_PROSPECTIVE_POLL_SECONDS` (1800s por defecto),
aislado en un `try/except` amplio para que un fallo nunca tumbe el canal ni
la API. Gates: 12 pruebas en `tests/test_phase_123_high_probability_prospective.py`,
5 en `tests/test_phase_123_channel_publisher_integration.py`, suite completa
714 Python aprobadas/8 omitidas sin regresiones.

El primer ciclo real en producción falló con `PredictionGatewayError`. Ver
`DEC-172`: la causa no era Fase 123, sino que `/v1/high-probability` nunca se
agregó a la lista de rutas con timeout extendido (7x) en `_call_with_timeout`
pese a barrer el mismo catálogo multi-liga que `/v1/live`/`/v1/upcoming` -el
servidor se cortaba a sí mismo con 504 antes de terminar el barrido,
probablemente afectando también a usuarios reales de `/mayor-probabilidad`
en la Mini App, no sólo a la cohorte prospectiva-. Corregido con una línea
en `src/dikamaha_service.py:1510-1511` y anclado con
`tests/test_high_probability_timeout_allowlist.py` (2 pruebas nuevas).

Limitación aceptada: si un fixture de un pick del menú nunca llega a
liquidarse en `prediction_settlements` -por ejemplo, si el publicador del
canal no lo escaneó ese día-, ese pick queda pendiente indefinidamente sin
tope de espera, igual que Fase 121 acepta para el resumen diario.

Verificación pendiente: confirmar en logs de producción que el próximo ciclo
real (hasta 30 minutos tras este despliegue) complete con
`phase123_cycle_completed` en vez de `phase123_cycle_failed`.

## Fase 122 — Menú de mayor probabilidad

Implementada. Ver `DEC-162`.

La pregunta que resuelve no es qué mercado acierta más, sino en qué mercado y a
qué nivel de confianza declarada el acierto observado justifica exponer el pick.
Son distintas: `home_corners_over_4_5` acierta 76.1% con una tasa base de 72.0%.

- backtest `scripts/run_phase_122_confidence_reliability.py` sobre los 1,270
  partidos de Fase 110, 12 mercados, 22 ligas y 15,240 decisiones, con
  probabilidades servidas (BTTS recalculado con el calibrador sellado de Fase
  106 y `home_corners_second_half_over_2_5` con su fallback de liga);
- **el gate congelado antes de puntuar rechazó las 21 celdas evaluables**. Dos
  de sus cinco criterios penalizaban la infraconfianza igual que la
  sobreconfianza y rechazaban un tramo que declara 68.3% y entrega 89.3%;
- un gate v2, re-especificado de forma explícita y documentada como post-hoc,
  aprueba 10 celdas y 9 sobreviven la confirmación contra los 270 partidos de
  la cohorte que Fases 105/119 nunca publicaron;
- comparador pareado contra la estrategia de tasa base con McNemar exacto,
  IC95% bootstrap de 10,000 remuestreos y control Benjamini-Hochberg a q=0.05
  sobre 21 hipótesis;
- **sólo 3 de las 9 celdas reflejan discriminación del modelo**
  (`model_edge`); las otras 6 aciertan porque la tasa base del mercado ya es
  alta (`base_rate_driven`), y la interfaz lo declara;
- **1X2, Más de 2.5 y Ambos marcan no clasifican en ningún tramo**. 1X2 llega a
  declarar confianza 1.000, pero en 0.65–0.75 promete 69.4% y entrega 51.0%,
  con 25% de ligas sin degradar. Ambos marcan nunca supera 0.561 de confianza
  porque el shrinkage de Fase 106 lo contrae hacia 0.50 por diseño;
- `src/high_probability_view.py` lee el artefacto sellado con verificación de
  versión y cotas, publica la tasa observada del tramo en vez de la
  probabilidad del modelo, ordena por esa cifra y aplica el `ExposurePolicy`
  existente para que tres líneas del mismo equipo y métrica no ocupen el menú;
- degradación segura real: artefacto ausente, corrupto o de versión distinta
  devuelve lista vacía, nunca un pick inventado ni una heurística;
- `GET /v1/high-probability?date=&limit=&leagues=` barre hasta 30 fixtures,
  aísla los que no tienen historial causal y los cuenta sin abortar;
- Mini App `/mayor-probabilidad` como sexta entrada de navegación, con estado
  vacío honesto cuando ningún pick del día supera el gate.

Estado: `railway_deployed`. La evidencia es histórica y no prospectiva; no hay
cuotas, ROI, CLV, Kelly ni stakes, y ningún modelo queda promovido. Gates: 662
pruebas Python aprobadas/8 omitidas (25 nuevas), 21 Vitest, 23 Playwright (6
nuevas), typecheck y build Next aprobados.

Despliegue mediante PR #35, commit de merge `438b1db`. Los cinco servicios
Railway reportan `SUCCESS`; la API sirve
`dikamaha_local_service_v2.0_high_probability` y `/mayor-probabilidad` responde
HTTP 200 en la Mini App.

El despliegue destapó un defecto de topología preexistente: `telegram-miniapp`,
`dikamaha-premium-telegram-bot` y `telegram-alert-worker` vigilaban la rama
`agent/model-integrity-audit`, contenida en `main` pero nueve commits por
detrás, de modo que llevaban sin recibir el hotfix `a7833a4` ni Fase 121. Los
tres quedaron repuntados a `main`, igual que la API, y redesplegados. Con ello
los cambios de Mini App de Fase 121 llegaron por fin a producción.

Antes del despliegue se corrigió un fallo que habría dejado el menú
**permanentemente vacío y en silencio**: `eligibility.json` no entraba en Git ni
en la imagen Docker, y el fail-open lo habría ocultado. Es el mismo fallo que
motivó el hotfix `a7833a4` para el snapshot de Fase 160. Ahora el artefacto se
sella con `hashes.json`, se verifica en runtime con tolerancia LF/CRLF y se
comprobó dentro del contenedor Linux que la vista carga las nueve celdas.

Verificación pendiente: el smoke autenticado de `/v1/high-probability` en
producción no se pudo ejecutar porque la conexión Railway sólo expone nombres de
variables, no el valor de `DIKAMAHA_API_KEY`. La ruta está confirmada dentro de
una imagen idéntica, pero no se ha observado su respuesta real en producción.

## Corrección operativa — tres defectos de producción preexistentes

Los logs del despliegue de Fase 122 destaparon tres defectos que impedían que el
historial verificado de Fases 118/121 acumulara nada. Ninguno fue introducido por
Fase 122; los tres son anteriores.

1. **`requirements.docker.txt` declaraba SQLAlchemy sin driver DBAPI.** Dentro del
   contenedor, `build_repository("postgresql://...")` fallaba con
   `ModuleNotFoundError: No module named 'psycopg2'`. Como `_settlement_store()`
   captura la excepción para que la API no caiga, el efecto era
   `phase118_settlement_store_unavailable` en cada arranque y `/v1/track-record`
   degradado a `unavailable` de forma permanente y silenciosa. Se añadió
   `psycopg2-binary>=2.9,<3`, el mismo pin que ya usaba
   `requirements.staging.txt`. Reproducido y verificado dentro del contenedor.

2. **El ledger SQLite vivía en disco efímero.** El Dockerfile fija
   `TELEGRAM_CHANNEL_LEDGER_PATH=/data/telegram_channel.sqlite`, pero el servicio
   no tenía volumen montado en `/data`, de modo que `channel_predictions` y
   `channel_publications` se destruían en cada redeploy. Eso permitía que el canal
   republicara y, sobre todo, impedía sellar settlements: `_seal_settlement`
   recorre `self._repository.predictions()`, así que sin ledger no hay veredictos
   que escribir en `prediction_settlements`. Era el riesgo abierto ya documentado
   en Fase 118. Se montó el volumen `dikamaha-prematch-ledger` en `/data`.

3. **El publicador era más impaciente que el servidor.** `TELEGRAM_REQUEST_TIMEOUT`
   usaba su valor por defecto de 15 s mientras la API opera con
   `inference_timeout_seconds: 30.0`, de modo que el cliente abandonaba
   `/v1/predict/upcoming` antes de que el servidor tuviera permitido responder.
   En cada redeploy el primer ciclo fallaba con `channel_cycle_failed` a los ~18 s
   del arranque, con la ruta de inferencia todavía fría. `_daily` publica el
   resumen sólo cuando congelan todos los fixtures, así que un solo timeout
   abortaba el ciclo completo; las predicciones ya congeladas sí persistían y el
   ciclo siguiente reanudaba. Se fijó `TELEGRAM_REQUEST_TIMEOUT=45` en el servicio
   de la API, por encima del límite del servidor. No se alteró la atomicidad del
   resumen.

4. **Un fixture no predecible abortaba el ciclo entero del canal.** Corregido el
   timeout, el error cambió a `dikamaha_prediction_rejected`, que destapó la
   causa de fondo: `/v1/predict/upcoming` devuelve 422 legítimo para ligas cuyo
   historial causal no alcanza el mínimo del snapshot, y la comprensión de lista
   de `_daily` propagaba esa excepción hasta `run_cycle`. Desde que Fase 120
   amplió el catálogo a 63 ligas ese caso es frecuente, de modo que un solo
   partido impedía publicar todos los demás. `_freeze_all` aísla el fallo por
   fixture, publica el resumen con los que sí congelaron y registra los omitidos
   en `daily_partial_failure`; sin ningún fixture predecible no se publica nada.
   Ver `DEC-163`.

Medición que descartó la hipótesis inicial: dentro del contenedor, la carga del
snapshot son `1.89 s` y una predicción en frío `3.41 s` (`0.12 s` en caliente),
muy por debajo del tope de 30 s del servidor. El problema nunca fue el coste de
la inferencia.

`tests/test_docker_runtime_requirements.py` añade cuatro guardas de regresión
sobre el manifiesto de la imagen: driver de PostgreSQL presente, dependencias de
arranque declaradas, artefacto de Fase 122 copiado y ledger apuntando a `/data`.
La suite normal no detectaba nada de esto porque en local sí existe el driver.
`tests/test_phase_101_partial_fixture_failure.py` cubre el aislamiento por
fixture, el log auditable, el caso sin ningún fixture predecible y el replay.

Gates tras la corrección: 675 pruebas Python aprobadas/8 omitidas.

## Incidencia de producción — caída de API y bot por el volumen del ledger

El punto 2 de arriba se implementó montando un volumen Railway en `/data` y
**provocó una caída de dos servicios**. Queda documentado como error propio.

Causa: el contenedor corre como el usuario `app` (UID 100). El Dockerfile hace
`chown` de `/data` en build, pero el montaje del volumen ocurre en runtime y
tapa ese directorio con uno nuevo propiedad de root. `create_all` del ledger
falló con `sqlite3.OperationalError: unable to open database file`.

Amplificación: el publicador construía su ledger **antes** del `try` del bucle,
de modo que la excepción mató el proceso hijo; el supervisor termina el
contenedor cuando ese hijo muere, así que cayó también la API. El bot premium
cayó a continuación porque su readiness de arranque contra la API dejó de
responder. `DIKAMAHA-PreMatch` y `dikamaha-premium-telegram-bot` quedaron en
`CRASHED`; Mini App, worker y PostgreSQL no se vieron afectados.

Restauración: retirar el volumen no bastó, porque el cambio queda `staged` y no
se aplica hasta el despliegue siguiente, lo que produjo dos intentos fallidos
más. Lo que restauró el servicio fue apuntar
`TELEGRAM_CHANNEL_LEDGER_PATH` a `/app/data/telegram_channel.sqlite`, un
directorio propiedad de `app` que no es punto de montaje. Después se redesplegó
el bot. Los cinco servicios volvieron a `SUCCESS`.

Correcciones para que no se repita:

- el publicador construye su ledger **dentro** del bucle protegido y lo
  reintenta en el ciclo siguiente; un ledger inaccesible degrada la difusión y
  ya no puede tumbar la inferencia;
- el Dockerfile fija la ruta por defecto bajo `/app/data`, con un comentario
  que explica por qué `/data` es peligroso mientras el proceso no sea root;
- `tests/test_phase_101_publisher_resilience.py` cubre el ledger inaccesible,
  la recuperación en el ciclo siguiente, que un fallo de ciclo no reconstruya
  el publicador, y la propiedad del directorio en la imagen.

## Ledger del canal migrado a PostgreSQL

El riesgo abierto desde Fase 118 queda cerrado. Ver `DEC-164`.

`_ledger_engine` elige PostgreSQL cuando existe `DATABASE_URL` —que el servicio
ya tenía conectada— y conserva SQLite sólo como respaldo local, declarado en el
log como efímero. Un `--ledger-path` explícito sigue forzando SQLite para
auditorías, y dry-run permanece en memoria. Las tres columnas JSON usan
`JSONB().with_variant(JSON(), "sqlite")`, misma convención que
`prediction_settlements`; el esquema se crea con `create_all`, igual que Fase
118, sin migración numerada.

Se descartó montar un volumen con un entrypoint privilegiado: resolvía el
síntoma conservando la causa —un proceso no root escribiendo en un punto de
montaje ajeno— y exigía ampliar la superficie del contenedor. PostgreSQL no
depende de la propiedad de ningún directorio y es el mismo almacén donde Fase
118 escribe los settlements que este ledger alimenta.

Verificado contra un PostgreSQL 17 real, no sólo con dobles: congelado
idempotente, publicación registrada, y **los registros sobreviven a la
reconstrucción del repositorio en un proceso nuevo**, que es exactamente lo que
el redeploy destruía. Las columnas quedan como `jsonb` en el esquema real.

No hubo datos que migrar: el ledger anterior era efímero y estaba vacío tras los
redeploys. Con esto `prediction_settlements` puede acumular por primera vez, y
`/v1/track-record` deja de estar condenado a responder vacío.

Gates: 683 pruebas Python aprobadas/8 omitidas.

Limitación abierta: el gate v2 se especificó después de ver el resultado de v1.
La confirmación sobre los 270 partidos nunca publicados controla que sus
umbrales no se ajustaran a cifras ya conocidas, pero ese holdout es un
subconjunto de la misma cohorte, no una muestra independiente. Una validación
prospectiva con predicciones congeladas antes del kickoff es el siguiente paso
recomendado.

## Fase 121 — Resumen diario de aciertos

Implementada. Ver `DEC-161`.

- `SettlementRepository.on_date(fecha, tz)` agrega `SqlAlchemySettlementRepository`
  para leer un día calendario local completo, cronológico y sin filtrar por
  acierto, distinto de `recent()` (ventana por conteo) que usa
  `/v1/track-record`;
- `GET /v1/track-record/daily?date=YYYYMMDD` expone el mismo agregado por
  fecha local; `date` es obligatorio, sin valor por defecto de reloj de
  pared en el servidor, y rechaza formato inválido con `422`;
- el avisador (`TelegramChannelPublisher`) publica el resumen íntegro de
  cada día calendario local — partido a partido, con ✅/❌ por los tres
  mercados oficiales y el conteo agregado de 1X2 al inicio —, bajo la clave
  de idempotencia `track_record_daily:{fecha}`, separada de
  `track_record:{semana}` de Fase 118. **Actualizado por `DEC-167`+`DEC-168`**:
  ya no espera a las 09:00 del día siguiente para resumir el día anterior.
  Publica cuando el día está completo — settlement para cada fixture
  congelado ese día, comparación exacta de `fixture_key` — y ya pasaron 30
  minutos desde `settled_at` del último partido en confirmarse (el instante
  real que `_seal_settlement` escribe, no una estimación desde el kickoff);
  `SETTLEMENT_DELAY` (3h) sigue intacta donde protege la liquidación
  individual en `_results`. Recorre todos los días con predicciones
  congeladas en cada ciclo, no sólo "hoy", así que también recupera un día
  que un reinicio del servicio hubiera dejado sin publicar. Limitación
  aceptada: un partido cuyo marcador nunca reconcilia deja ese día sin
  publicar de forma indefinida, sin tope de espera;
- la Mini App muestra "Resultados de hoy" en `/historial`, por encima del
  historial acumulado existente, calculando la fecha de hoy en el cliente
  igual que ya hace `markets/page.tsx`;
- corregido un defecto real de zona horaria detectado por las propias
  pruebas: SQLite no normaliza a UTC un `datetime` con tzinfo al
  persistirlo, de modo que sembrar un veredicto con un `kickoff_ts` que no
  esté ya convertido a UTC lo agrupa en el día equivocado; las pruebas usan
  ahora `.astimezone(timezone.utc)` explícito, igual que el resto del
  sistema ya exige;
- corregido un defecto de superposición de rutas en Playwright: `/api/track-record**`
  interceptaba también `/api/track-record/daily` por ser un patrón más
  amplio, de modo que la Mini App recibía el agregado semanal en vez del
  diario en la prueba E2E; la ruta más específica debe registrarse después
  para ganar la coincidencia — no es un defecto de producción, sólo del
  arnés de pruebas, pero quedó documentado con una prueba dedicada.

Estado: `implemented`. Gates: 637 pruebas Python aprobadas/8 omitidas (13
nuevas), 21 Vitest, 17 Playwright (1 nueva) y typecheck/build Next aprobados.
Despliegue a Railway pendiente de aprobación del usuario.

## Fase 120 — Expansión del catálogo a 63 ligas y torneos

Implementada y activada. Ver `DEC-160`.

Causa raíz del reporte: ESPN separa la fase previa de UEFA en slugs propios
(`uefa.champions_qual`, `uefa.europa_qual`, `uefa.europa.conf_qual`) distintos
de `uefa.champions`/`uefa.europa`/`uefa.europa.conf`, y `concacaf.leagues.cup`
nunca estuvo en el catálogo. `/v1/live` y `/v1/upcoming` iteran exactamente el
catálogo, de modo que sin el slug ningún barrido podía encontrar esos partidos.
No era un fallo de la ventana D-1/D/D+1 ni del catálogo live.

- catálogo de 49 a 63 slugs, con `docs/league_catalog_v1.json` y
  `src/espn_user_explorer.py::LEAGUES` sincronizados y una prueba que falla si
  divergen;
- 14 slugs añadidos, cada uno verificado con respuesta ESPN 200 antes de
  entrar: `ned.1`, `por.1`, `tur.1`, `bel.1`, `sco.1`, `den.1`, `nor.1`,
  `per.1`, `ksa.1`, `jpn.1`, `concacaf.leagues.cup`, `uefa.champions_qual`,
  `uefa.europa_qual` y `uefa.europa.conf_qual`;
- Liga MX Femenil y K League quedan fuera por no existir en el proveedor:
  `mex.w.1` y `kor.*` responden HTTP 400 y el índice Core de 214 ligas de
  fútbol no contiene ninguna referencia coreana ni liga femenil mexicana;
- descubrimiento incremental de 3,311 referencias nuevas fusionadas sobre las
  9,775 previas: 13,086 referencias y 56 ligas, con cero ligas perdidas;
- ingesta de 2,337 partidos completos y 28,044 ventanas, más un refresco de
  317 partidos recientes para `tur.1`, `ksa.1` y `jpn.1`;
- snapshot activo `phase160_recent_topup_v1_20260811` con 12,281 partidos y
  147,372 filas; historial de rollback de seis versiones intacto;
- 117 fallos excluidos por el gate, dominados por `window_score_mismatch`: un
  partido sólo entra si su play-by-play reconcilia el marcador.

Dos defectos operativos preexistentes quedaron corregidos porque bloqueaban el
flujo:

- `run_multileague_discovery.py` reescribía `references.json` con sólo las
  ligas de la corrida, de modo que cualquier descubrimiento incremental habría
  borrado las 42 ligas ya descubiertas y roto el gate `_documented_leagues` de
  Fase 53. Ahora fusiona por clave estable, conserva respaldo y expone
  `--replace-references` para la reconstrucción deliberada;
- `run_phase_52`/`run_phase_53` leían el snapshot activo con
  `read_text(encoding="utf-8")`, pero Fase 108 lo dejó en gzip. El refresco
  incremental fallaba con `invalid start byte` desde entonces. El registro
  publica ahora `read_snapshot_rows()`, que acepta ambos formatos.

Verificación real: predicción pre-match con `selective_dc_kalman_official` en
las ligas nuevas; play-by-play, estadísticas por periodo, equipos, plantillas y
perfiles de jugador operativos; apertura y cierre de mercado visibles como
cinta financiera aislada. Predicción live oficial sobre NEC Nijmegen–Olympiacos
(`uefa.champions_qual`, 1-1 a tres minutos del final) con P(empate) `0.8969` y
marcador exacto 1-1 `0.8954`, prior causal con corte estrictamente anterior al
kickoff.

La allowlist Hawkes permanece en 17 ligas. Las 14 nuevas responden
`admitted: false` con `rho_goal=0.0` y `fallback_exact_markov_live: true`, que
es el comportamiento correcto de DEC-114: no hay evidencia de validación propia
y esta fase no autoriza ampliarla.

Estado: `implemented_and_activated`. Gates: 624 pruebas Python aprobadas/8
omitidas, 21 Vitest y typecheck Next aprobados.

## Fase 119 — Backtest de calibración de 500 partidos

Objetivo: diagnosticar sesgo de calibración en los 11 mercados pre-match
(oficiales y shadow) tal como se sirven hoy, sobre 500 partidos reales
disjuntos de Fase 105/106, corregir con shrinkage bayesiano lo que pase el
mismo gate de Fase 106, y comparar antes/después sobre el mismo conjunto.

Cerrada sin promoción de mercados. Ver `DEC-159`.

- cohorte de prueba: 500 partidos elegibles más recientes del split
  `confirmation`; cohorte de ajuste: los 500 elegibles inmediatamente
  anteriores, sin solape — el hiperparámetro de cualquier corrección se fija
  sólo con la cohorte de ajuste, nunca con la de prueba;
- diagnóstico mide lo que el sistema sirve hoy (BTTS ya calibrado por Fase
  106, Markov con el fallback de liga ya aplicado), no las salidas crudas de
  Fase 105;
- entra a corrección todo mercado binario con ECE > 0.05 y tasa positiva entre
  5% y 95% sobre los 500 de prueba; 1X2 sólo se diagnostica;
- `src/market_calibration.py` generaliza el shrinkage bayesiano de Fase 106 a
  cualquier mercado, sin duplicar `btts_probability.py` por mercado nuevo;
- el gate final reutiliza sin modificar `_bootstrap`/`_stability`/`_passed`/
  `_metrics` de Fase 106; un mercado que no pasa se reporta diagnosticado y no
  corregido, sin abortar el resto;
- conexión fail-open en `src/team_count_market_runtime.py`: cae exacto a la
  probabilidad no corregida si el artefacto falla o el hash no coincide.

Resultado real sobre 500 partidos de prueba (2025-12-14 a 2026-07-26,
21 ligas): cuatro mercados con sesgo real —
`home_corners_over_4_5` (ECE 0.180), `away_shots_over_10_5` (0.130),
`away_corners_over_4_5` (0.114), `over_2_5` (0.088, nunca antes
recalibrado)—. El shrinkage bayesiano redujo el ECE de forma sustancial en
los cuatro y mejoró log-loss/Brier en tres, pero ninguno alcanzó
`non_degradation_rate >= 0.70` en la cohorte de prueba (probados dos
criterios de selección de hiperparámetro, ambos sobre el bloque externo).
Los cuatro quedan diagnosticados y no corregidos; `PHASE119_CORRECTED_MARKETS`
queda vacío por diseño. El resto de mercados, incluido BTTS (ECE 0.034), ya
opera dentro del margen sano. Reporte visual en
`artifacts/phase_119_bias_backtest_500/dashboard.html`.

Estado: `implemented_no_market_promoted`. Gates: 616 pruebas Python
aprobadas/8 omitidas. Ver `DEC-159`.

## Fase 118 — Historial de aciertos verificable (previa)

En curso. Ver `DEC-158`.

- `prediction_settlements` en Postgres guarda por partido el marcador
  reconciliado, el `prediction_hash` publicado antes del kickoff y el veredicto
  por mercado, escrito append-only desde `_results`;
- se liquidan 1X2, Más de 2.5 y Ambos marcan como oficiales, y en bloque
  separado los mercados shadow del contrato `phase102_v4_direct_totals`
  mediante los conteos por periodo y lado de `explorer_statistics`;
- `GET /v1/track-record` devuelve la cola cronológica completa, con aciertos y
  fallos, sin ningún parámetro que permita filtrar por acierto;
- por debajo de 20 partidos liquidados no se publica porcentaje; a partir de
  ahí se acompaña siempre de intervalo de confianza del 95% y del baseline
  correspondiente;
- el servicio de la API recibe `DATABASE_URL`, que hasta ahora no tenía.

Estado: `implemented_pending_first_settlements`. El código está completo y los
gates aprueban, pero la tabla arranca vacía por diseño: sólo suma partidos cuya
predicción se congeló antes del kickoff a partir del despliegue. Gates: 592
pruebas Python aprobadas/8 omitidas, 21 Vitest, 16 Playwright, typecheck y build
Next aprobados.

Riesgo abierto: el ledger del publicador sigue en SQLite sin volumen montado en
el servicio de la API, de modo que `channel_predictions` y
`channel_publications` probablemente se pierden en cada redeploy. Mientras eso
siga así el historial no acumulará muestra de forma fiable y el canal puede
republicar. Migrar ese ledger a PostgreSQL es el siguiente paso recomendado.

## Fase 117 — Mercados live adaptativos de equipo

Implementada en modo shadow. Extiende el motor de Fase 116 por analogía, sin
reemplazar ninguna capa existente.

- `_dynamic_poisson` acumula además `lambda_remaining_corners_home/away` y
  `lambda_remaining_shots_home/away` en el mismo bucle de segmentos de cinco
  minutos, reutilizando `time_shape`, `_score_factors`, penalización por rojas
  y decaimiento del hazard;
- corners y tiros usan `ctmc_pressure_multipliers_home/away` propios y no
  aplican el multiplicador Elo;
- `lambda_remaining_shots_side = lambda_shot_event_side + lambda_side` cumple
  por construcción la semántica comercial de DEC-110;
- `_next_goal` deriva próximo gol con `competing_event_distribution` sobre las
  intensidades de gol ya oficiales, con horizonte igual al tiempo restante;
- la rejilla adaptativa publica tres líneas centradas en P(over)≈50% por lado y
  métrica, con under complementario y comparación contra ritmo base;
- todo se publica en `experimental_live_team_markets`, hermano de
  `official_live_prediction`, que permanece sin cambios;
- `_territory_strength` escala el territorio por el cociente de lambdas
  causales pre-match con exponente `0.5`, de modo que la línea es específica de
  cada equipo desde el minuto cero y no un umbral genérico;
- los checks nuevos entran en `_audit_checks`, de modo que un fallo degrada el
  snapshot completo al fallback Markov existente;
- la Mini App muestra el bloque con badge shadow y lo oculta por completo
  cuando el motor cae a fallback.

- las tasas base están calibradas contra el corpus causal de Fase 74, 9,465
  partidos y 18,930 unidades equipo-partido, cuyas medias por equipo son
  `5.4175` córners, `7.3320` tiros sin gol y `1.3411` goles; una prueba ancla
  esas medias con tolerancia `0.05`.

Comportamiento verificado con `lambda_base 1.55/1.08`: al minuto 30 sin eventos
el local proyecta `4.08` córners restantes y el visitante `3.40`; cinco eventos
de presión visitante invierten la relación a `3.63` contra `3.91` y desplazan
el próximo gol de `0.346` a `0.378`; al minuto 80 con el local abajo las líneas
se recentran solas en `1.5/2.5/3.5`.

Estado: `implemented_shadow_no_historical_gate`. Las tasas base reproducen la
media histórica, pero no existe replay walk-forward de córners ni tiros por
segmento, de modo que ninguna línea está promovida ni comunica ventaja
predictiva. Gates: 578 Python aprobadas/8 omitidas, 21 Vitest, 14 Playwright,
typecheck y build Next aprobados. Ver `DEC-157`.

## Fase 116 — Motor matemático de probabilidades in-live

Implementada y evaluada sobre la base histórica existente.

- DEC-155 y `live_probability_engine_contract_v1` congelan entrada causal,
  salida, provenance, invariantes y rollback;
- Poisson dinámico integra tasas por segmentos de cinco minutos y deriva 1X2,
  periodos, O/U 0.5–3.5, BTTS, marcador exacto y goles restantes;
- CTMC propaga tres regímenes mediante una matriz generadora válida y conserva
  masa; Hazard/Cox usa eventos observados con ventanas 5/10/20; Elo live actúa
  como ajuste latente con shrinkage;
- `hawkes_live_v2` permanece residual logarítmico acotado; `rho=0` reproduce
  exactamente el baseline analítico;
- Monte Carlo ejecuta 20,000 simulaciones asincrónicas, deterministas por
  snapshot, sin bloquear ni decidir la salida oficial;
- `/v1/predict/live` y `/v1/predict/live/fixture` publican el contrato nuevo y
  conservan los tres campos experimentales de Fase 114 como alias compatibles;
- Mini App actualiza cada 15 s, muestra salida oficial, periodos, intensidades,
  próximos eventos, marcador exacto, componentes y salud matemática; ESPN
  Predictor/Pickcenter permanece como benchmark externo aislado;
- bot y worker de alertas consumen primero `official_live_prediction`;
- replay read-only: 7,400 partidos, 34 ligas, cinco snapshots por partido,
  6,985 snapshots de confirmación y bootstrap por partido completo;
- todos los gates causales y de integridad aprobaron; hash del replay
  `674a46c58a1bfff214d040b001cab606f450a5105c992e85e94acc1589a92087`;
- el delta objetivo confirmatorio frente a Markov fue `+0.0010468`, con IC95
  `[-0.0005522, 0.0025056]`: no prueba mejora estadística, pero tampoco bloquea
  la activación inmediata fijada por DEC-155 y se registra sin afirmar ventaja;
- benchmark local del motor analítico: p95 `2.908 ms` en 300 ejecuciones,
  ampliamente inferior al gate de 250 ms;
- gates finales: 567 pruebas Python aprobadas/8 omitidas, 18 Vitest, 11
  Playwright, typecheck, build Next y builds Docker API/bot/Mini App aprobados.

Estado: `railway_deployed_official`. Corrección de estado (2026-08-12, Paso 1
del plan de cierre): la etiqueta anterior `pending_deployment` estaba
desactualizada. `GET /v1/health` en producción
(`dikamaha-prematch-production.up.railway.app`) confirma en vivo
`"live_probability_engine_enabled":true` y
`"live_probability_engine_official":true` — el flag nunca se fijó
explícitamente en ningún `railway*.toml` y el código por defecto
(`_env_bool("LIVE_PROBABILITY_ENGINE_OFFICIAL", True)`,
`src/dikamaha_service.py:412-413`) ya lo activa. El rollback sigue disponible
fijando `LIVE_PROBABILITY_ENGINE_OFFICIAL=false` explícitamente en el
servicio, pero hoy el motor compuesto de Fase 116 es el que sirve
`/v1/predict/live/fixture` en producción, no el fallback Markov.

## Fase 115 — Telegram Mini App híbrida

Implementada, desplegada y validada en Railway con acceso privado gradual.

- Next.js 16/TypeScript mobile-first con dashboard, live, próximos,
  predicciones, modelos, alertas, ajustes y Centro de datos;
- paridad funcional del bot para ligas, fechas, partidos históricos, contexto,
  play-by-play, estadísticas por periodo, equipos, plantillas y jugadores;
- tema claro/oscuro de Telegram, safe areas, navegación persistente y botón
  nativo de regreso;
- BFF con firma de `initData`, expiración de cinco minutos, rechazo de grupos,
  allowlist/modo público, cookie segura, CSRF y rate limit por usuario;
- API key ausente del bundle; cero llamadas ESPN desde navegador o worker;
- PostgreSQL versionado con 10 favoritos, 20 alertas activas, cooldown mínimo y
  límites concurrentes mediante advisory locks;
- dedupe `subscription_id + event_key` validado sobre PostgreSQL 17 real;
- worker de una réplica con polling por liga, `sendMessage`, backoff acotado y
  sin `getUpdates`;
- bot con botón `web_app`, menú global y enlaces `startapp` a fixture live o
  pre-match;
- marcador, reloj, timestamp y próximo evento live corregidos en la vista;
- catálogo live robusto a medianoche UTC mediante ventana D-1/D/D+1, con
  detalle que muestra ambos escudos, marcador, tiros, córners, tarjetas,
  faltas, acciones recientes y las tres capas predictivas;
- refresco automático cada 20 s en catálogo y 15 s en detalle, conservando el
  botón manual sólo como fallback;
- catálogo visual ampliado a los 49 slugs auditados en Fase 36; próximos usa
  14 días y live conserva D-1/D/D+1 con concurrencia acotada y caché;
- catálogo próximo ampliado a las 49 ligas y torneos y 14 días mediante una petición
  acotada por liga; búsqueda global de equipos tolerante a acentos;
- navegación principal con acceso directo a Predicciones y diagnóstico visual
  de cobertura, ligas, fallos parciales y refresco live;
- logos de equipo y retratos de jugador PNG transparentes mediante proxy
  DIKAMAHA/BFF autenticado, con fallback visual cuando no existen;
- Cambridge United–Barnet conserva 1T, 2T y partido completo;
- el detalle pre-match conserva los nombres reales del catálogo aunque el
  payload predictivo sólo contenga IDs; títulos, 1X2 y mercados por periodo ya
  no presentan etiquetas genéricas de equipo;
- el área predictiva incorpora gráfica 1X2, indicadores de concentración y
  separación, comparación de goles esperados e intensidades, tabla matemática
  y barras por equipo con pistas y colores distinguibles;
- DEC-153 añade `GET /v1/provider/predictor` como benchmark externo
  `display_only`: sólo acepta tripletes 1X2 publicados explícitamente, muestra
  ausencia normal cuando el proveedor no los ofrece;
- DEC-154 añade `GET /v1/provider/markets` y un módulo visual de pronósticos
  globales. `pickcenter` y `activeodds=true` muestran apertura, cierre y live
  como cinta financiera aislada, sin derivar SPI, probabilidad, consejo ni
  entrada a modelos;
- DEC-156 añade al detalle pre-match la rejilla adaptativa de Fase 102 desde
  `bounded_market_grid_view`, agrupada por primer tiempo, segundo tiempo y
  partido completo. Las líneas se centran en P(over)≈50% de la distribución
  causal de cada equipo, por lo que varían por partido y por periodo, e
  incluyen under complementario y delta contra baseline. El bloque congelado
  `user_market_view` permanece intacto y la rejilla no se monta cuando la vista
  llega vacía;
- el detalle live incorpora una curva firmada de presión por minuto con media
  móvil de cinco minutos y marcadores de gol; la serie es heurística visual y
  no alimenta Markov, Hawkes, combinado ni el prior pre-match;
- Fase 116 reemplaza la tarjeta live shadow por el motor oficial compuesto;
  Markov permanece fallback y Hawkes permanece residual selectivo;
- 543 pruebas Python aprobadas/8 omitidas, 16 Vitest y 8 Playwright; build Next
  aprobado y conexión real validada para readiness, modelos, ligas, fechas y
  próximos, además del transporte BFF autenticado con clave sólo servidor;
- auditoría npm sin vulnerabilidades y build/smoke Docker previos conservados.

Extensión DEC-153 desplegada mediante PR #19. Dos summaries reales confirmaron
el estado `not_published` del predictor con contexto de mercado aislado; 555
pruebas Python aprobadas/8 omitidas, 18 Vitest, 10 Playwright, typecheck y
build Next aprobaron el contrato, la API/BFF y las gráficas. Railway reportó
`SUCCESS` para API, Mini App, bot y worker. El smoke autenticado confirmó API
`dikamaha_local_service_v1.7_provider_context`, Mini App `ready`, sesión 200,
benchmark 200 sin cuotas expuestas y predicción live 200 con
`match_pressure_v1` de 90 puntos más Markov/Hawkes/combinado.

Extensión DEC-154 desplegada mediante PR #21: el predictor analítico sigue
`not_published` para `col.1/401877857`, mientras `pickcenter` entrega un
moneyline completo de apertura/cierre/live que ahora se muestra sin derivar
SPI. `activeodds=true` devolvió tres fixtures con mercado y el barrido real
encontró un partido activo al consultar los 49 slugs, con cero fallos
parciales. Gates: 559 Python/8 omitidas, 18 Vitest, 11 Playwright, typecheck y
build Next aprobados. Railway reportó `SUCCESS` en los cuatro servicios; API
`v1.8_provider_markets`, readiness, Mini App/DB/upstream y `/markets`
respondieron HTTP 200 en producción.

Corrección live DEC-152 desplegada mediante PR #17. La ventana automática D-1/D/D+1
encontró tres partidos reales sobre 18 ligas con cero fallos parciales; el
detalle Jaguares de Córdoba–Once Caldas devolvió logos PNG, marcador, 12 grupos
de estadísticas, cronología y Markov/Hawkes/combinado. Gates: 546 Python, 18
Vitest, 10 Playwright, typecheck y build Next aprobados. El frontend comprobó
un segundo fetch automático a los 10 segundos sin intervención del usuario.
Railway reportó `SUCCESS` y el smoke BFF de producción confirmó sesión, catálogo
y predicción HTTP 200, 3 activos, 18 ligas, 0 fallos, 24 acciones y ambas
imágenes disponibles.

Despliegue Railway de producción:

- PostgreSQL `0276da42-ffdd-48d2-bc7f-bd6ae7fd37e7`: `Online`;
- Mini App `dbd1077b-a34e-4eaf-9385-50ec633aefa7`: `Online` en
  `https://telegram-miniapp-production-cbab.up.railway.app`;
- worker `6d6d036b-109c-46d8-98a5-0e8b0f8bdbcb`: `Online`, una réplica y
  `MINIAPP_ALERTS_ENABLED=false`;
- bot premium `440d330b-1018-4fd9-a6ef-792cd9d671cc`: commit de Fase 115
  activo, URL configurada y `setChatMenuButton` aceptado por Telegram;
- `/api/health`: `ready`, PostgreSQL conectado; sesión vacía rechazada con
  HTTP 400 y `telegram_init_data_missing`.

Estado: `railway_deployed_private_bot_parity_ready`. El commit `95946d7` está
activo en `telegram-miniapp`; `/api/health` respondió `ready` con PostgreSQL,
`/explore` respondió HTTP 200 y `/api/readiness` sin sesión falló cerrado con
HTTP 401. `MINIAPP_ENABLED=true` sólo para la allowlist privada. Falta el smoke
interactivo de un usuario desde Telegram y registrar el short name en BotFather
para enlaces `startapp`. El worker reconstruido quedó `Online` y confirmó
`enabled: false`; las alertas permanecen desactivadas. Markov, Hawkes y
combinado siguen separados y shadow.

Corrección visual DEC-150 desplegada mediante PR #8, #9 y #11. La Mini App
activa sirve el commit `ede2f39`; la API activa incorpora los merges
`a8abb8d` y `62eb8e7`. Smoke BFF real: sesión 200, próximos 200/18 ligas,
búsqueda Barnet 200, live 200/18 ligas/0 fallos, PNG 200 y predicción 200 con
los tres periodos. Todos los servicios Railway permanecen `Online`.

Extensión analítica e identidad nominal desplegada mediante PR #13. El commit
`8aa3aca` conserva los nombres del catálogo en títulos, 1X2 y mercados,
recupera identidad para enlaces anteriores y añade gráfica 1X2, xG, lambda,
tabla e indicadores derivados. Railway reportó `SUCCESS` para Mini App, bot y
worker; el merge `525aab2` dejó `/api/health`, `/predictions` y `/v1/health`
en HTTP 200. Los workflows de GitHub no iniciaron por bloqueo de facturación,
no por un fallo de código; la suite local completa permanece aprobada.

Corrección de disponibilidad de catálogos preparada: los logs Railway aislaron
que el publicador usaba `127.0.0.1:8000` aunque Railway asignaba `PORT=8080`.
El supervisor propaga ahora la URL administrada al worker; el BFF reintenta GET
transitorios, registra fallos sin secretos y `/api/health` exige PostgreSQL más
el catálogo DIKAMAHA. Próximos, históricos, equipos y live muestran un aviso
con reintento si ligas/fechas no responden, en vez de selectores silenciosamente
vacíos. Gates locales: 544 Python/8 omitidas, 17 Vitest, 9 Playwright,
typecheck y build Next aprobados.

Incidencia de producción DEC-151 aislada por Network Logs: el login Telegram
respondía 200 y todos los catálogos posteriores 401 por cookie `SameSite=Lax`
en el contexto embebido. El hotfix de cookie segura particionada y confirmación
post-login aprobó 18 Vitest, 10 Playwright, typecheck y build Next. No toca
datos ni modelos; queda pendiente el smoke posterior al despliegue.

## Fase 114 — Markov Live y Hawkes residual

Implementada y validada históricamente en `shadow`. La ruta nueva
congela el prior pre-match, actualiza un filtro Markov con marcador, reloj y
eventos observados, y después permite que Hawkes module los hazards sólo como
residual acotado en escala logarítmica.

- polling fresco de scoreboard/event/plays/situation, sin caché live;
- persistencia raw-first append-only y errores aislados por fixture;
- eventos futuros, identidad inválida y score/PBP incoherente rechazados;
- mercados de goles restantes y próximo evento normalizados;
- `rho=0` y ausencia de Hawkes reproducen Markov Live exactamente;
- Hawkes subcrítico, radio espectral `0.31428571428571433`, con
  `rho_goal=1.0` y `rho_next_event=0.0` seleccionados fuera de confirmación;
- replay determinista sellado para Markov y la combinación;
- API aditiva: bloques Markov, residual y combinado separados;
- API de producto: catálogo de fixtures activos y predicción por identidad;
- prior `reconstructed_causal_prematch_prior`, con cutoff histórico estricto,
  hash y exclusión del partido objetivo;
- Telegram expone `Partidos en vivo` y `Modelos en operación` sin llamar ESPN;
- runner gradual multiliga sobre el catálogo habilitado;
- gate histórico read-only: 9,649 partidos reconciliados de regulación;
- 7,400 partidos elegibles/34 ligas tras warm-up causal;
- bloques de 4,417/1,586/1,397 partidos sin kickoffs compartidos;
- Markov delta objetivo `-0.002259`, IC95%
  `[-0.002858, -0.001635]`, 84.375% de ligas no degradadas;
- Hawkes global delta agregado `-0.000648`, IC95%
  `[-0.001026, -0.000272]`, pero sólo 59.375% de ligas no degradadas;
- proveedor ESPN corregido: fallback Site 200 y Core event/plays 200;
- política Hawkes elegida sólo en validación: 17 ligas con al menos 30 partidos;
- Hawkes selectivo delta objetivo `-0.000398`, IC95%
  `[-0.000650, -0.000135]`, y 84.375% de ligas no degradadas;
- fuera de la allowlist y para próximo evento se aplica fallback Markov exacto;
- clocks ESPN de descuento `90'+N'` ya no quedan truncados en 90 minutos.

Estado: `historically_validated_and_product_integrated_shadow`. Markov
Live supera el gate histórico; Hawkes global conserva su diagnóstico
heterogéneo, pero la política selectiva supera el gate robusto sin alterar
próximo evento. La integración de producto conserva las tres capas separadas,
el router oficial intacto y todos los modelos live sin promoción.

## Fase 113 — integridad completa de modelos

Validada con salidas selectivas. La auditoría corrigió la orientación de baja
anotación de Dixon-Coles, eliminó actualizaciones entre partidos del mismo
kickoff y endureció splits, métricas, PMF, artefactos y fallbacks.

- 1,405 kickoffs simultáneos auditados y 3,884 exposiciones históricas
  intra-kickoff eliminadas;
- fronteras compartidas de Fase 104: `27 → 0`;
- 45 partidos con equipos sin historia previa excluidos del gate;
- ocho familias de artefactos con todos los hashes verificados;
- runtime oficial normalizado, causal y fail-closed;
- 1X2 y over 2.5 continúan oficiales; BTTS usa Fase 106;
- ocho mercados de equipo permanecen en shadow;
- Fase 105 regenerada: 1,000 partidos, 11,000 decisiones, accuracy `59.14%`,
  log-loss `0.713722` y Brier normalizado `0.246900`;
- la imagen Railway detectó una incompatibilidad CRLF/LF en manifiestos
  sellados y exigía además evidencia no ejecutable excluida del contenedor;
- el verificador portable conserva hashes estrictos para cada componente
  runtime requerido y tolera únicamente la representación CRLF/LF en texto;
- Cambridge United–Barnet (`401880614`) produce dentro de la imagen mínima
  8 mercados, 21 grupos y probabilidades para 1T, 2T y partido completo;
- BTTS deja de caer al fallback por el mismo defecto de empaquetado;
- 522 pruebas integrales aprobadas, 8 integraciones opcionales omitidas.

Estado: `validated_selective_hotfix_ready_for_deployment`. No hay validación de cuotas, ROI, CLV, Kelly,
stakes ni combinadas; cualquier reporte fuera del contrato versionado queda
excluido de promoción.

## Fase 109 — bot premium Telegram en Railway

Validada para despliegue. El bot privado consume por HTTPS la API ya
desplegada y reutiliza exactamente el presentador del canal para la tarjeta
principal y el dashboard de mercados. Conserva navegación por liga/fecha,
play-by-play, estadísticas y perfiles de jugadores.

- interruptor `private|public`, con `private` seguro por defecto;
- allowlist obligatoria y fail-closed únicamente en modo privado;
- modo público limitado a chats privados y rate limit por usuario;
- `/whoami` disponible para solicitar alta;
- rechazo no autorizado sin ejecutar inferencia;
- servicio Railway independiente, sin dominio ni volumen;
- una sola réplica de long polling;
- imagen de 57.9 MB, usuario `app`, sin modelos, snapshots ni artefactos;
- logs JSON, readiness remoto previo al polling y cierre SIGTERM limpio;
- contrato móvil compartido: prosa ≤72 columnas, tablas ≤40, botones ≤32;
- estadísticas reducidas de 46 a 38 columnas sin perder métricas;
- nombres, contexto, eventos y botones dinámicos compactados semánticamente;
- `/help` público con fallback a texto plano si Telegram rechaza HTML;
- `/start` y `/help` ocultan configuración, tokens y controles internos;
- menú y comando live con Markov, Hawkes residual y combinado separados;
- inventario visible de modelos oficiales y shadow realmente operativos;
- imagen bot sin artefactos; imagen API con política Hawkes de 17 ligas;
- 529 pruebas integrales aprobadas y 8 omitidas.
- la regresión Cambridge United–Barnet confirma tarjeta y dashboard de 2,941
  caracteres con 1T, 2T y total cuando la API carga los artefactos sellados.

Estado: `validated_for_deployment_with_live_models`. El interruptor no administra cobros,
renovaciones ni vencimientos; sólo controla el perímetro técnico de acceso.

## Fase 108 — higiene del repositorio

Validada. Se retiraron al menos 7.72 GiB de entornos, cachés y salidas
regenerables. La evidencia histórica permanece local y excluida de Git. El
snapshot activo bajó de 122.0 MB a 3.1 MB mediante gzip con el mismo hash
lógico. El contexto Docker mide 5.04 MB, la imagen 181.5 MB y el smoke real
ejecutó `selective_dc_kalman_official`. Suite: 442 aprobadas/8 omitidas.

## Fase 107 — Railway y pilotos reales

La unidad API + publicador Telegram quedó validada para desplegarse en Railway
con una sola réplica y volumen persistente `/data`.

- salida Telegram conserva sólo información útil para la predicción;
- autenticación obligatoria fuera de health/readiness;
- logs JSON sin cuerpos ni secretos;
- timeout, rate limit, límite de concurrencia y `Retry-After`;
- caché TTL y single-flight para ráfagas del mismo fixture;
- 100 solicitudes: 16 `200`, 84 `503` controladas, cero timeouts;
- p95 `2.892 s`;
- imagen ejecutada como usuario `app`;
- predicción real desde el contenedor aprobada;
- cierre SIGTERM con código 0;
- 48 pruebas dirigidas y 441 pruebas de regresión integral aprobadas
  (8 integraciones opcionales omitidas).

Estado: `validated`. Siguiente paso: crear el servicio y volumen Railway,
cargar secretos y ejecutar smoke contra la URL asignada antes de abrir el
piloto.

## Fase 106 — reparación probabilística selectiva

La sobreconfianza BTTS detectada en Fase 105 quedó corregida mediante una tasa
causal por liga, contraída hacia `0.50` con shrinkage `500`. Se descartó una
calibración Platt porque invertía el ranking de la señal. La línea Markov
`home_corners_second_half_over_2_5`, que degradaba log-loss y Brier, usa
exactamente su baseline.

- 800 predicciones prequential posteriores a 200 partidos de warm-up;
- log-loss BTTS: `0.874028 → 0.691966`;
- Brier BTTS: `0.302916 → 0.249410`;
- ECE BTTS: `0.185686 → 0.016445`;
- IC95% de mejora: `[0.129208, 0.237257]`;
- estabilidad: 19/21 ligas no degradadas (`90.48%`);
- replay global de 1,000 partidos: accuracy `60.29%`, log-loss `0.692561`,
  Brier `0.266393`;
- 38 pruebas dirigidas aprobadas.

Estado: `selective_integrated`. La evidencia sigue siendo histórica; no
demuestra ventaja económica contra cuotas.

## Fase 104 — cadena oficial de goles

La tarjeta oficial ya ejecuta una cadena real Dixon-Coles/Kalman. El gate
walk-forward evaluó 500 partidos de 31 ligas con 10,000 bootstraps pareados
por mercado.

- 1X2: aprobado; log-loss `1.044743` frente a `1.179101`, IC95%
  `[0.074377, 0.197009]` y estabilidad `80.65%`.
- Over 2.5: aprobado; log-loss `0.732155` frente a `0.970582`, IC95%
  `[0.127871, 0.359432]` y estabilidad `80.65%`.
- Ambos marcan: no supera el gate estructural por estabilidad `67.74%`; usa
  la reparación causal validada de Fase 106.
- 45 cold starts posteriores al corte quedaron excluidos de la comparación.
- Markov de goles continúa shadow y declara `markov_used=false`.
- El router revierte automáticamente al baseline si la cadena no puede
  ajustarse causalmente.
- 45 pruebas dirigidas aprobaron.

Estado: `selective_official`.

## Fase 105 — auditoría histórica completa de 1,000 partidos

Se ejecutó una prueba con el modelo actualmente desplegado: 1,000 partidos,
21 ligas y 11,000 decisiones contra PBP reconciliado.

- accuracy global: `59.14%`;
- confianza media: `61.11%`;
- log-loss global: `0.713722`;
- Brier normalizado por evento: `0.246900`;
- el Brier crudo mixto se suprime porque 1X2 y mercados binarios no comparten
  escala;
- cadena oficial DC/Kalman: `50.65%`;
- mercados agregados Fase 84A: `62.65%`;
- mercados temporales Markov Fase 88: `61.75%`;
- BTTS baseline: `51.60%`;
- partidos 12/12: `4`;
- partidos 0/12: `0`.

Es una auditoría histórica reproducible, no confirmación prospectiva ni
evidencia de ROI.

## Fase 103 — evaluación distribucional walk-forward

Validó valor incremental histórico para 12 de 18 candidatos Markov elegidos
sin consultar confirmación. La evaluación cubrió 9,646 partidos, 218 líneas,
1,892 partidos de selección y 1,895 de confirmación. El manifiesto quedó
sellado antes del scoring; el gate aplicó 10,000 bootstraps por partido,
Brier, ECE y estabilidad por liga. Seis candidatos se rechazaron aunque
mejoraban en promedio porque no alcanzaron 70% de ligas no degradadas.

El resultado aún es `shadow`: no contiene cuotas ni demuestra ventaja
económica. Las 51 líneas full-match de tiros a puerta requieren una evaluación
anidada separada para no reutilizar la partición que Fase 84A empleó al elegir
hiperparámetros.

## Fase 102 — escaleras distribucionales por equipo

Validada técnicamente. Reutiliza la distribución conjunta de Fase 88 para
producir 21 PMF y 269 líneas personalizadas de corners, tiros, tiros a puerta
y tarjetas por partido. La API expone la escalera completa y los bots hasta seis escenarios
con probabilidad 55–80% y señal específica de equipo de al menos +2pp frente
al baseline liga/localía. Tres próximos partidos y 47 pruebas aprobaron.
Permanece shadow; falta evaluación walk-forward por familias de líneas.
La revisión v1.1 expone además tres líneas por cada equipo, métrica y periodo,
siempre entre 1.5 y 9.5, mostrando over y under complementarios. Telegram
publicó esta rejilla para tres partidos reales en mensajes de 3,114–3,336
caracteres.

## Fase 101 — canal Telegram automático

La difusión a `@viewtofuture` está validada y activa. Un ciclo recurrente cada
cinco minutos publica el resumen de mañana a las 09:00
`America/Mexico_City`, seguido inmediatamente por sus tarjetas individuales.
El interruptor `full|lite` permite publicar todos los partidos o sólo los tres
más próximos. Las tarjetas admiten escudos ESPN con fallback de texto. Los
resultados aparecen únicamente desde `kickoff + 3h` cuando marcador y
play-by-play reconcilian. El primer
ciclo congeló diez predicciones y confirmó un resumen; el replay produjo cero
duplicados. Telegram confirmó al bot como administrador con permiso de
publicación. Router, modelos y política económica permanecen intactos.
La operación ya no depende de Codex: Fase 101 v1.2 ejecuta un servicio
permanente del repositorio que supervisa la API read-only y el worker. El
servicio está activo en modo `lite` y su primer replay produjo cero duplicados.
Fase 101 v1.3 muestra además las nueve líneas disponibles por partido,
agrupadas por periodo y comparadas contra baseline. La simulación real publicó
27 líneas para tres partidos con etiqueta experimental explícita.
Fase 101 v1.4 reemplazó esas líneas genéricas en los avisos por seis escenarios
distribucionales variables por partido. Tres snapshots `phase102_v1` se
congelaron antes del kickoff sin sobrescribir las predicciones anteriores y
Telegram confirmó los mensajes 29–31. El replay queda protegido por una clave
versionada por fixture.
Fase 101 v1.5 mejoró la lectura móvil: cada partido ahora entrega tres tarjetas
visuales separadas (1T, 2T, total), con tablas de Más/Menos/Referencia por
equipo y mercado. Telegram confirmó nueve tarjetas reales, mensajes 35–43,
sin modificar las probabilidades ni duplicarlas en replay.
Fase 101 v1.6 consolida esas tres tarjetas en un dashboard autoidentificable
por partido cuando cabe en Telegram; conserva cada encabezado temporal y cae a
tarjetas separadas sólo si fuera necesario. La entrega lite redujo nueve a tres
mensajes de mercados; Telegram confirmó los dashboards 44–46.
Fase 102 v1.2 añadió al final del dashboard un pronóstico global de corners,
tiros, tarjetas y tiros a puerta. Los tres primeros usan convolución explícita
de las PMF de ambos equipos y tiros a puerta conserva su PMF total directa. Un
flujo completo aislado validó 3 congelaciones, 1 agenda, 3 tarjetas y 3
dashboards, seguido de replay sin publicaciones.
La auditoría de v1.3 detectó que la convolución Markov y la línea máxima 9.5
producían totales poco diferenciados. El resumen global ahora usa PMF
negativo-binomial directa a partir del histórico causal equipo/rival/liga de
Fase 84A, y comunica media, moda e intervalo central 60% en vez de una línea
saturada. Telegram confirmó la corrección en los mensajes 67–69.

## Fase 98 — explorador Telegram

El bot activo ya incorpora navegación sin IDs para datos y predicciones.

- 18 ligas navegables;
- próximos partidos en rutas global, por liga y por fecha futura;
- consulta global paralela y tolerante a fallos parciales de ESPN;
- liga→fecha→partido para play-by-play y estadísticas;
- liga→equipo→jugador para perfiles;
- búsqueda de equipos por texto y coincidencias como botones;
- mercados separados en primer tiempo, segundo tiempo y total;
- play-by-play real paginado: 1,183 plays/4 páginas y 101 eventos clave;
- estadísticas de ambos equipos con `1T + 2T = total`;
- boxscore total separado para posesión, pases y entradas;
- tablas visuales uniformes para probabilidades, mercados, estadísticas,
  perfiles y estado; play-by-play convertido en tarjetas compactas;
- nombres reales de los equipos en 1X2, mercados, conteos y estadísticas,
  sin etiquetas genéricas local/visitante;
- variantes ESPN de gol reconciliadas contra el marcador oficial; auditoría
  específica Deportivo Riestra 3–0 Boca Juniors aprobada;
- smoke real con 33 jugadores y 15 campos estadísticos de jugador;
- caché ESPN raw-first, sólo lectura y router intacto;
- 40 pruebas dirigidas y 418 pruebas de regresión integral aprobadas.

## Fase 99 — interfaz Discord

La implementación está conectada al Gateway Discord, pertenece al servidor
configurado y sincronizó sus comandos directamente en el guild.

- slash commands `/dikamaha`, `/proximos`, `/playbyplay`, `/estadisticas`,
  `/jugadores` y `/estado`;
- componentes nativos para todos los próximos, liga, fecha y partido;
- predicción y mercados separados en 1T, 2T y total;
- play-by-play histórico por liga→fecha→partido, con eventos clave, todos y
  paginación;
- estadísticas históricas por liga→fecha→partido y selector 1T/2T/total;
- equipos por liga, búsqueda modal, plantilla y perfil individual;
- respuestas ephemeral, allowlists de usuario y servidor;
- nombres reales de equipos y mercados shadow identificados;
- sin Message Content Intent ni lógica predictiva duplicada;
- dependencia aislada en `requirements.discord.txt`;
- smoke local con Discord 2.7.1 aprobado;
- seis comandos verificados por la API Discord en el guild;
- 425 pruebas aprobadas y 7 PostgreSQL opt-in omitidas;
- falta únicamente smoke manual de callbacks por el usuario autorizado.

## Fase 100 — enriquecimiento ESPN programado

El plan de objetivos separa explícitamente contexto de interfaz, candidatos
pre-match, datos live/settlement y archivo financiero. La entrega 100A quedó
validada: ficha de partido con estadio, árbitros, transmisión, fase, branding
e identidad raw-first vía API DIKAMAHA y ambos bots. 100B también quedó
validada: standings y calendario histórico por equipo como contexto visible,
con filtro estricto previo al kickoff. 100C quedó validada como contexto: los
rosters, estados activos e incidencias publicadas tienen procedencia raw y
ausencia explícita de reporte. 100D materializó 83 referencias causales, pero
está bloqueada por cero outcomes independientes; no se promovió feature.
100E permanece aislada hasta tener fixtures live/finales elegibles y 100F
archivó noticias editoriales y 83 odds sin consumidor predictivo.

Consultar [plan de enriquecimiento ESPN](plan_espn_bot_data_enrichment.md) y
[Fase 100](phases/phase_100_espn_bot_context_enrichment.md).

Primera ingesta completada el 2026-07-29: 18 ligas, 500 equipos, 83 fixtures
programados y 2,768 respuestas raw-first. Quedan disponibles snapshots de
fixture, competición, summary, árbitros, broadcast, standings, rankings,
rosters, lesiones, calendarios y noticias. La clasificación es
`promising_unconfirmed`: los contratos de presentación 100A–100C y 100F están
implementados; falta una cohorte de outcomes causalmente comparable para 100D.
Los endpoints globales de atletas y sedes quedan deliberadamente fuera: ESPN
devuelve 4,145 y 103 páginas globales, respectivamente, no catálogos de la
liga solicitada; las plantillas por equipo ya cubren el flujo de usuario. Ver
[reporte Fase 100](../artifacts/phase_100_espn_context_enrichment/final_report.md).

## Fase 97 — Telegram privado

El adaptador Telegram consume los endpoints universales existentes y no
duplica lógica del modelo.

- long polling con offset monotónico;
- `/start`, `/help`, `/whoami`, `/estado`, `/partido` y `/predict`;
- allowlist de usuarios y sólo chats privados;
- rate limit, timeout, retry exponencial y mensajes bajo 3,900 caracteres;
- fixture E2E con ocho mercados y baseline oficial correctamente etiquetado;
- token, API key y allowlist sólo desde entorno;
- replay de integración idéntico;
- cero llamadas Telegram reales durante auditoría;
- router, probabilidades y snapshots intactos;
- suite integral con PostgreSQL: 410 pruebas aprobadas.

El token y la allowlist se cargan desde `.env`; el proceso real se encuentra
activo y los secretos permanecen fuera de código, logs y artefactos.

## Fases 95–96 — preparación probabilística y de riesgo

Se ejecutaron exclusivamente tareas cubiertas por la información actual.

- Fase 95: 395 partidos/3,160 decisiones después de un warm-up atómico de 105;
- log-loss raw/calibrado: 0.662019/0.650708;
- Brier raw/calibrado: 0.234291/0.229677;
- ECE raw/calibrado: 0.047495/0.025295;
- IC95% de mejora log-loss: [0.005200, 0.017453];
- cinco líneas recomiendan calibración; tres mantienen probabilidad raw;
- Fase 96: tres pares con dependencia absoluta >=0.30;
- 10 perfectos observados frente a 9.47 esperados bajo independencia;
- política shadow: máximo tres mercados por partido y uno por componente
  positivamente correlacionado;
- router, stakes, ROI, CLV y Kelly permanecen sin cambios/no calculados.
- suite integral con PostgreSQL: 404 pruebas aprobadas.

El próximo bloqueo real es la ausencia de cuotas históricas timestamped y
alineadas con cada línea. No se simularán.

## Fase 94 — validación semi-oficial

La espera de 520 fixtures de Fase 90 dejó de ser un bloqueo operativo. La
validación histórica regenerada cubre 500 partidos, ocho mercados y 4,000
liquidaciones contra play-by-play. Las cifras anteriores de nueve mercados
quedan reemplazadas por Fase 113.

- accuracy total: 62.31% (baseline 59.78%);
- log-loss total: 0.660556 (baseline 0.677244);
- IC95% pareado de mejora completamente positivo;
- Markov temporal: 60.60% (baseline 58.90%);
- 27 ligas utilizables; 12 feeds con taxonomía incompleta rechazados mediante
  una regla congelada sólo con datos anteriores.
- replay determinista y suite integral PostgreSQL: 400 pruebas aprobadas.

El acoplamiento puede avanzar con etiqueta `semi_official_historical`; esto no
se presenta como evidencia prospectiva independiente.

## Programa activo Markov v4

- Plan canónico: `docs/plan_markov_prematch_v4.md`.
- Decisión: `DEC-077`, congelada para ejecución.
- Fase 72: `ready_for_next_phase`; 12/12 recursos obligatorios cubiertos,
  persistencia/replay raw-first y 295 pruebas de regresión aprobadas.
- Fase inmediata: 73, snapshots `T-168h`, `T-72h`, `T-24h`, `T-6h` y `T-90m`.
- Primera ronda Fase 73: 5 fixtures/ligas, 60 filas causales, 36 en `T-24h`
  y 24 en `T-72h`; 60/60 duplicados evitados en replay.
- Gate Fase 73: `insufficient_coverage` esperado hasta que cada fixture tenga
  al menos dos buckets reales.
- Fase 74 rematerializada: `ready_for_phase_75`; 9,465 partidos, 39 ligas y 624,690
  microventanas causales en resoluciones 5/10/15.
- Fase 74 excluyó 9 discrepancias y 4 timelines ausentes con identidad;
  las tres particiones temporales no se solapan.
- Fase 75: `ready_for_next_phase`; 9,465 partidos y 56,790 targets conjuntos
  `neither/home_only/away_only/both`.
- Baseline seleccionado sólo en `selection`: tabular same-data, log-loss
  confirmatorio `0.992701`, Brier `0.540081`, ECE `0.007085`.
- Replay doble idéntico por hash; diferencia métrica máxima `0.0`.
- Trabajo autorizado: descubrimiento latente de Fase 76.
- Suite completa tras Fase 75: 309 passed, 7 skipped por integración
  PostgreSQL explícita.
- Primera formulación de Fase 76: `rejected_for_revision`. El candidato direccional de 8 estados
  alcanzó spread `0.062649` en selección, pero sólo `0.029279` en confirmación.
- La duración explícita sí mejoró NLL confirmatorio en `0.035683`; la
  estabilidad mínima de estados fue `NMI 0.569401`, bajo el gate `0.70`.
- Confirmación ya observada durante revisión: no puede reutilizarse para
  seleccionar otra variante; este bloqueo histórico fue reemplazado por 76R.
- Suite completa tras Fase 76: 312 passed, 7 skipped por integración
  PostgreSQL explícita.
- Reauditoría Fase 76: la GMM falló por conteos cero-inflados, estados
  conjuntos y outcomes transitorios usados como emisiones.
- `predictive_latent_state_v2`: 6 estados balanceados, spread `0.053971`,
  NMI `0.779114`, duración `+0.115708`, 30/30 ligas estables y p de
  permutación `0.004975` en desarrollo/selección.
- Diagnóstico sobre la cohorte ya observada: spread `0.056352`; no cuenta como
  confirmación independiente.
- Replay predictivo exacto por hash. Esta ruta v2 quedó reemplazada por 76R.
- Suite completa tras reauditoría: 315 passed, 7 skipped por integración
  PostgreSQL explícita.
- Cohorte independiente iniciada con cutoff `2026-07-26T18:00:00Z`: 19
  partidos, 5 ligas, 25,851 eventos y 76 payloads raw.
- Corregida paginación ESPN >300 plays: reconciliación pasó de 1/19 a 19/19;
  replay de ingesta idempotente.
- Evaluación congelada actual: spread descriptivo `0.099888`, duración
  `-0.025180`; clasificación `insufficient_coverage`.
- Gate Fase 76: 200 partidos/10 ligas; disponible 19/5. El colector acumula
  automáticamente la cohorte posterior al cutoff.
- Reauditoría de la base completa: 10,251 partidos/42 ligas nominales. Tras
  excluir los 9,444 IDs usados por Fase 74 quedaron 777 completos/14 ligas,
  pero sólo 376/9 reconciliaron play-by-play. ESPN devolvió timelines vacíos
  al reconsultar los otros 401. En el holdout limpio: spread `0.034152`,
  duración `+0.193679`, estabilidad `3/4`; Fase 76 no supera todos los gates.
- Suite completa tras el gate sellado: 319 passed, 7 skipped por integración
  PostgreSQL explícita.
- Revisión robusta v3: en selección cumple spread `0.051049`, NMI `0.737042`,
  estabilidad `29/30`, ocupación `23.029%`, duración `+0.116564` y permutación
  `p=0.004975`. En el holdout sellado mejora v2, pero queda en spread
  `0.042423`, estabilidad `5/6` y 376 partidos/9 ligas; sigue rechazada.
- Conector ESPN 1.2: fallback raw-first de Core `/plays` vacío a
  Site `/summary.commentary`, con identidad de equipos tomada del mismo header.
- El holdout de Fase 76 queda clausurado para nuevas decisiones de modelado.
- Suite completa tras v3 y fallback ESPN: 322 passed, 7 skipped por
  integraciones PostgreSQL explícitas.
- Confirmación prospectiva v3 sellada desde
  `2026-07-28T06:44:20.320524Z`, hash de parámetros `4dd56513…8256fb2`.
  Primera captura: 0 partidos/0 ligas, `metrics_sealed=true`,
  `outcomes_read=false`; comportamiento esperado inmediatamente tras el lock.
- La automatización diaria fue actualizada para ejecutar colección y gate
  ciego; la evaluación antigua ya no se ejecuta mientras falte cobertura.
- Suite completa tras lock y gate prospectivo: 324 passed, 7 skipped por
  integraciones PostgreSQL explícitas.
- Reauditoría de ingesta Fase 76: el parser ya acepta `team.id` directo y la
  reconciliación derivada recupera cinco partidos sin alterar raw PostgreSQL.
  El holdout pasa a 381 partidos/10 ligas; cubre 200/10, pero v3 mantiene
  spread `0.042241 < 0.05`, por lo que Fase 76 sigue rechazada.
- Rematerialización global corregida: 10,221 partidos completos/42 ligas,
  9,775 utilizables, 117,300 ventanas, 438 timelines ausentes y sólo 8
  discrepancias de marcador conservadas como exclusiones auditables.
- Suite completa con las integraciones PostgreSQL habilitadas: 344 passed.
- Fase 74 fue rematerializada con reconciliación vigente: 9,465 partidos de
  39 ligas y 340,740 microventanas direccionales de cinco minutos.
- Fase 75 fue reproducida sobre el corpus actualizado y conserva
  `ready_for_next_phase`.
- Fase 76R reemplaza cuartiles uniformes por estados de cola aprendidos en
  train. Dos folds temporales anidados aprueban todos los gates: spread
  `0.056876/0.056224`, NMI `0.847527/0.796878`, estabilidad por liga
  `100%/96%` y duración `+0.140545/+0.145935`.
- `predictive_latent_state_v4_tail_crossfit` queda congelado para desarrollo;
  v3 y su lock prospectivo se preservan como evidencia negativa, no como ruta
  activa. Fase 77 queda autorizada; el router sigue en baseline.
- Primera ejecución de Fase 77 rechazada: el clasificador state_0 pierde
  `0.95%/1.20%` de log-loss frente al prior liga+localía y también degrada
  Brier/ECE. El prior rolling de equipo converge al baseline; el siguiente
  cambio debe separar estilo persistente pre-match de régimen in-play.
- Revisión dual aprobada: seis estados `style(2) × régimen(3)` conservan
  spread `0.064109/0.064365`, NMI `0.892442/0.891485`, ocupación mínima
  `6.34%/5.99%`, estabilidad `100%/95.45%` y mejoran state_0
  `46.75%/46.59%`. Fase 78 queda autorizada; router sin cambios.
- Migración 013 aplicada: índice causal compuesto de `events_timeline`;
  elimina el timeout observado en la auditoría ordenada sin modificar datos.
- Fase 78 aprobada con transición condicionada por régimen rival: mejora
  `1.95%/2.16%`, masa contextual `54.77%/58.98%`, estabilidad por liga
  `93.33%/90.91%` y error máximo de duración `5.21%/7.05%`.
- Fase 79 aprobada: simulación dual reproducible de 18 ventanas, conservación
  máxima `6.661e-16`, suma 1X2 exacta, estilo invariante, cero lecturas
  post-cutoff y fallback core distinto del reparto plano. Fase 80 autorizada;
  router sin cambios.
- Suite completa posterior a Fase 79, incluidas integraciones PostgreSQL:
  `348 passed`, una advertencia de deprecación ajena al modelo.
- Fase 80 cerró `rejected_for_revision`: tras corregir carrier temporal,
  dependencia conjunta y shrinkage, la mejor variante (`15m`, fuerza `0.1`)
  obtuvo mejora log-loss `-0.000002`, Brier `+0.000002` e IC95%
  `[-0.000019, 0.000016]`; sólo 44.83% de ligas fueron no negativas.
  La marginalización pre-match lava la señal de transición. Fase 81 bloqueada;
  router sin cambios.
- Fase 80R evaluó likelihood conjunto contra un comparador secuencial directo.
  Selection eligió `no_transition`; en confirmation Markov `0.989798` perdió
  contra directo `0.989387`, IC95% `[-0.001266, 0.000422]` y 48.28% de ligas
  no negativas. La cadena latente queda rechazada para promoción. Se autoriza
  sólo Fase 80S técnica para mercados de trayectoria en shadow.
- Fase 80S validada técnicamente: primer gol por intervalo, número de ventanas
  activas, ventanas consecutivas, clustering y dominancia temporal de segunda
  mitad. Replay idéntico, error de probabilidad `0`, conservación `6.661e-16`,
  etiqueta `experimental_shadow_not_promoted` y router intacto. No desbloquea
  Fase 81.
- Regresión completa posterior a 80R/80S: `349 passed`, `7 skipped` por
  integraciones PostgreSQL explícitas y una advertencia externa deprecada.
- Suite integral con PostgreSQL habilitado: `356 passed`; permanece sólo la
  advertencia externa de TestClient.
- Fase 80T descartó arquetipos discretos: mejora `-0.000344` frente al Markov
  directo, IC95% cruzando cero y 34.48% de ligas no negativas.
- Fase 80U no homogénea es la mejor cadena encontrada: supera al Markov directo
  `+0.001797`, pero frente al continuo same-data sólo logra `+0.000431`,
  Brier `+0.000119`, IC95% `[-0.000161, 0.001051]` y 55.17% de ligas.
  Queda shadow; no promoción.
- DEC-100 clausura `fit/selection/confirmation` para más tuning. Fase 81 sigue
  bloqueada y el baseline permanece como única salida oficial.
- Fase 80V auditó los 100 partidos más recientes de confirmation sin tuning:
  100 outcomes/13 ligas, 80U `0.954427`, continuo same-data `0.953792` y
  baseline `0.958411`. 80U gana 55/100 duelos contra el continuo, pero pierde
  `0.000635` en promedio. Reporte ordenado y replay idéntico por hash; es
  diagnóstico, no promoción.
- Fase 80W probó la cadena histórica más completa disponible sobre otros 100
  partidos: Dixon-Coles/Kalman, conservación, Markov y calibración temporal.
  Fiabilidad macro `54.4%` frente a `56.2%` de la regla ingenua por mayoría
  (`-1.8 pp`); calidad probabilística Brier-normalizada `73.45%`. Gol 2T fue
  el mejor mercado con `72%`, pero no supera su mayoría de `72%`. Diagnóstico
  reproducible, Hawkes fuera y router intacto.
- Fase 84A fue corregida a semántica comercial de tiros (`shots + goals`) sin
  tocar goles. Tras la revalidación quedan cuatro líneas shadow: corners
  local/visitante O4.5, tiros visitante O10.5 y tiros a puerta totales O7.5.
- Fase 85 integró las líneas aprobadas en solicitud universal y
  resolución de fixture bajo `experimental_team_markets`. Diez fixtures de
  replay conservaron idénticos todos los campos oficiales, el fallback seguro
  fue probado y el replay fue exacto.
- La paridad semántica entre entrenamiento y snapshot operativo verificó
  132,216 conteos en 18,888 observaciones equipo-partido sin diferencias.
  No hay promoción.
- Fase 86 selló 523 predicciones prospectivas de 18 ligas, cubriendo kickoffs
  del 29 de julio al 27 de agosto de 2026. Se persistieron 1,302 scoreboards
  raw-first; modelo, baseline, snapshot y timestamps quedaron congelados.
- La colección superó 500/10 con IDs únicos y replay append-only. No se
  consultaron outcomes, summaries, estadísticas ni play-by-play. Nueve
  fixtures sin historia mínima quedaron excluidos y auditados.
- Fase 87 implementó settlement raw-first con summary y plays paginados.
  Corners/tiros provienen del boxscore y tarjetas 1T de amarillas con reloj
  menor a 45:00; cualquier inconsistencia se rechaza sin imputación.
- Primera corrida Fase 87: 0 elegibles, 0 llamadas post-match, 0 outcomes,
  0 rechazos y hash de las 523 predicciones idéntico. Estado esperado antes
  del primer `kickoff + 3h`: `insufficient_coverage`.
- Fase 88 implementó Markov independientes para corners, tiros y tarjetas por
  equipo y mitad sobre 9,646 partidos/39 ligas. Las 100 predicciones causales
  contienen 12 mercados cada una y replay doble idéntico.
- La reauditoría comercial obtuvo fiabilidad `65.92%`, log-loss `0.608606` y
  Brier `0.211424`; el baseline obtuvo `66.58%`, `0.596778` y `0.204902`.
- Cuatro líneas mejoran simultáneamente log-loss y Brier: tiros visitante 2T
  O5.5, corners local 2T O2.5 y tiros local 1T/2T O5.5. Las ocho restantes
  conservan fallback; el router de goles no cambió.
- Regresión integral posterior a Fase 88, con PostgreSQL: `385 passed`;
  únicamente permanecen advertencias externas deprecadas.
- Fase 89 serializó el Markov comercial final y añadió cuatro líneas al flujo
  universal. Tras Fase 113 el sidecar expone ocho mercados: cuatro Fase 84A y
  cuatro Fase 88.
- Diez fixtures verificaron hash, cutoff causal, probabilidades válidas,
  paridad oficial y replay idéntico. Si Markov falta o el kickoff no es
  posterior al cutoff, permanecen exactamente las cuatro líneas 84A.
- Regresión integral posterior a Fase 89, con PostgreSQL: `387 passed`;
  permanecen únicamente advertencias externas deprecadas.
- Fase 90 congeló 520 predicciones nuevas antes del kickoff en 18 ligas,
  cuatro mercados por partido, un único hash de modelo v2 y cero outcomes.
  Tres fixtures ya iniciados se excluyeron; replay append-only idéntico.
- Fase 91 implementó settlement raw-first de corners/tiros por mitad, usando
  periodo explícito, goles como tiros y reconciliación contra boxscore.
  Primera corrida: 0 elegibles, 0 llamadas, 0 outcomes y 520 pendientes.
- Fase 92 dejó listo el gate individual de 10,000 bootstraps por partido,
  Brier y estabilidad por liga. Permanece `insufficient_coverage` y no puntúa
  parcialmente.
- Fase 93 añadió `user_market_view`; Fase 113 actualizó el contrato a ocho mercados para
  interfaz con probabilidad, baseline, lado, periodo, línea, fuente y estado.
  Diez fixtures conservaron paridad oficial y replay.
- Regresión integral posterior a Fases 90–93, con PostgreSQL: `397 passed`;
  permanecen sólo advertencias externas deprecadas.
- Props de jugador permanecen `blocked_by_data`: faltan identidad de evento,
  minutos, titularidad y snapshots de alineación históricamente causales.
- Regresión integral final, incluidas integraciones PostgreSQL: `379 passed`;
  permanecen advertencias externas de TestClient y carga joblib/NumPy.
- Automatización diaria activa: `cohorte-independiente-markov-fase-76`;
  recolecta, evalúa y mantiene bloqueada Fase 77 hasta cumplir cobertura.
- Suite completa tras paginación/cohorte: 316 passed, 7 skipped por integración
  PostgreSQL explícita.
- Promoción: sólo después del walk-forward de Fase 80 y la confirmación
  prospectiva de Fase 81.
- Router: baseline estructural; Markov v1–v3 y v4 permanecen fuera.

## Hecho

- Fase 72 validada: nuevo contrato `raw_responses` aditivo, repositorio
  SQLAlchemy, proveedor ESPN raw-first, migración 011 y replay por hash.
- Smoke real `mex.1`: equipos, roster, lesiones, calendario, standings,
  temporadas, atletas, venues, cuotas y árbitros operativos.
- El caché conserva el timestamp de descarga original; reutilizarlo no fabrica
  disponibilidad prospectiva nueva.
- Suite completa: 295 passed, 7 skipped por requerir PostgreSQL explícito.
- Fase 73 implementada: scheduler por ventanas, cutoff efectivo, idempotencia
  previa a red e índice único por fixture/bucket/request.
- Suite completa tras Fase 73: 303 passed, 7 skipped por PostgreSQL explícito.
- Fase 74 validada: PostgreSQL SELECT-only, marcadores reconciliados para todo
  partido publicado y contexto inicial estrictamente anterior a cada ventana.
- Suite completa tras Fase 74: 306 passed, 7 skipped por integración
  PostgreSQL explícita.
- `match_features v1`, Dixon-Coles y Kalman permanecen como base pre-match.
- La ingesta ESPN y la evaluación prospectiva están disponibles como infraestructura.
- Se congeló el cambio de rumbo a Markov dependiente pre-match.
- Fase 01 validada: 381 partidos, 4,572 ventanas y 29,049 eventos en `event_windows v1`.
- La materialización de ventanas es determinista y PostgreSQL permaneció en modo sólo lectura.
- Fase 02 validada: etiquetas causales estables; sensibilidad máxima de 11.83%.
- Fase 03 validada: 3,810 transiciones de 381 partidos, matrices normalizadas y splits temporales sin solapamiento.
- La validación mejoró el log-loss medio por partido frente al Markov global (`1.059263` frente a `1.066381`); la confirmación posterior también mejoró (`1.079897` frente a `1.083964`).
- Se preservó trazabilidad de backoff: contexto 562, ventana 194 y global 4 transiciones en validación.
- Fase 04 validada: simulación determinista de 5,000 trayectorias, orientación local/visitante y probabilidades 1X2 válidas.
- Las probabilidades de goles usan `historical_state_emission`; son experimentales y están bloqueadas para promoción.
- Fase 05 auditada y bloqueada: no hay suite de comparadores OOS compatible por partido completo ni cobertura confirmatoria suficiente.
- Kalman cubre 331 partidos, pero Dixon-Coles sólo 5; baseline simple, Markov global y Markov dependiente no tienen predicciones OOS serializadas.
- El bloqueo de contrato se resolvió: existe una suite OOS canónica de 264 partidos para baseline, Dixon-Coles, Dixon-Coles+Kalman, Markov global y Markov dependiente.
- Se registraron 264 renormalizaciones explícitas de 1X2 heredado, preservando proporciones y sin recalibrar modelos fuente.
- Fase 05 rechazada: Markov dependiente tuvo log-loss confirmatorio `1.047638`, peor que el baseline simple (`1.030090`) y mejor sólo marginalmente que Markov global (`1.051661`).
- El bootstrap por partido no confirmó mejora: vs baseline `[-0.069986, 0.035769]`; vs Markov global `[-0.017304, 0.026108]`.
- Fase 06 validada técnicamente: 264 priors estructurales OOS y simulación v2 determinista que conserva la intensidad total de ambos equipos en cada trayectoria.
- Markov v2 ya cuenta con 264 predicciones OOS: 66 por cada fold, 1X2 normalizado y sin targets usados como features.
- Markov v2 no se promociona: log-loss confirmatorio `0.886892`, mejor que baseline (`1.030090`) y v1 (`1.047638`), pero peor que Dixon-Coles (`0.873433`).
- El bootstrap confirmó mejoras frente al baseline, v1 y Markov global, pero no frente a Dixon-Coles (`[-0.035602, 0.008303]`).
- Fase 07 rechazada: en confirmación ninguna señal temporal tuvo bootstrap estrictamente positivo.
- Log-loss temporal Markov vs baseline: primer tiempo `0.620453` vs `0.647418`; segundo tiempo `0.541074` vs `0.568944`; ambos intervalos cruzaron cero.
- Remontadas: tampoco confirmaron mejora; sus intervalos fueron `[-0.012535, 0.013293]` y `[-0.011203, 0.014868]`.
- Fase 08 validada: las ventanas son consistentes con el marcador final en los 381 partidos; hay 255 partidos con gol en primer tiempo y 309 con gol en segundo tiempo.
- Las remontadas tienen baja prevalencia: 11 locales y 15 visitantes; sus tasas condicionales tras ir perdiendo al descanso son 13.58% y 11.11%.
- Fase 09 validada para revisión: se incorporaron como cohorte candidata 44 partidos completos de `prospective_staging_v2`, sin modificar `event_windows v1` ni los folds OOS congelados.
- Fase 09 materializó 528 ventanas candidatas y auditó 9,072 eventos fuente con conexión PostgreSQL sólo lectura.
- La cohorte combinada pasa a 425 partidos: 34 recuperaciones locales a empate o victoria y 41 visitantes; las remontadas estrictas quedan en 11 y 16.
- `temporal_targets v2` congela recuperación a empate o victoria y alcance del empate durante la segunda mitad; la remontada estricta queda como diagnóstico.
- Fase 09 no entrenó modelos ni promovió mercados.
- Fase 10 evaluó 44 partidos de confirmación con Markov ajustado sólo en los 381 partidos canónicos.
- `first_half_goal` obtuvo log-loss Markov `0.610619` frente a baseline `0.689937`, con IC bootstrap de mejora `[0.022140, 0.136298]`.
- `second_half_goal` no confirmó mejora: log-loss Markov `0.584304` frente a baseline `0.540521`, IC `[-0.142047, 0.063638]`.
- Recuperaciones y alcance del empate no pasan el soporte mínimo de oportunidades; ningún mercado fue promovido.
- Fase 11 añadió una extensión local de 241 partidos completos entre 2025-12-01 y 2026-05-24, con 48,061 eventos; no se escribió PostgreSQL.
- Fase 12 materializó 2,892 ventanas de 15 minutos; los 241 marcadores finales coinciden con los goles observados y no hay solapamiento con las cohortes previas.
- La extensión elevó el soporte a 163 partidos con gol en primera mitad, 208 en segunda, 49 oportunidades de reacción local y 85 visitante.
- Fase 13 evaluó Markov v2 entrenado sólo en los 381 partidos canónicos contra 241 partidos de confirmación y con lambdas Dixon-Coles congeladas pre-kickoff.
- Fase 13 fue `rejected_for_revision`: Markov quedó por debajo del baseline en `first_half_goal` (0.669821 vs 0.629704) y `second_half_goal` (0.479849 vs 0.408920); ninguna mejora bootstrap fue confirmada.
- La cobertura de priors y predicciones fue completa. Tres equipos no están en el catálogo interno y quedaron registrados con fallback explícito al identificador ESPN; esto no habilita promoción.
- Fase 14 introdujo priors rolling venue-aware de 90 días con shrinkage de 2 partidos; `first_half_goal` mejoró a 0.619165 en la evaluación anterior, pero `second_half_goal` siguió por debajo.
- Fases 15-16 incorporaron una temporada 2023-24 completa: 380 partidos, 4,560 ventanas y cero discrepancias de marcador.
- Fase 17 corrigió la mezcla de IDs ESPN e IDs internos en ventanas históricas y añadió priors iniciales de estado por equipo/localía; el efecto fue pequeño y no confirmó promoción.
- Fases 18-19 incorporaron los 95 partidos faltantes de agosto-octubre de 2025, con 1,140 ventanas auditadas.
- Fase 20 regenerada con 899 partidos válidos previos a la confirmación. `first_half_goal` quedó en 0.626560 frente a 0.629820; su IC de mejora `[-0.011927, 0.018561]` cruza cero. `second_half_goal` quedó en 0.451818 frente a 0.411345.
- Fase 21 implementó un selector conservador: Markov sólo se activa para `first_half_goal`; todos los demás targets usan baseline. El selector no degrada los targets en la confirmación y ningún mercado fue promovido.
- Fase 22 construyó 1,140 filas pre-match con tasas históricas de goles, tiros, tiros a puerta, corners, presión, faltas y tarjetas de primera mitad, sin modificar `match_features v1`.
- Fase 22 detectó una duplicación exacta del partido del 26/10/2025 entre el artefacto canónico y la cohorte de calibración; se excluyó el registro canónico duplicado y se regeneró Fase 20 con 855 partidos base y 899 de train final.
- Con el histórico limpio, la señal auxiliar obtuvo log-loss `0.621447` frente a baseline `0.629820` en los 241 partidos confirmatorios; su mejora media `0.008372` tiene IC bootstrap `[-0.012421, 0.028253]`, por lo que queda `promising_unconfirmed`.
- El Markov limpio quedó en `0.626560` para `first_half_goal`; Fase 21 se regeneró y mantiene Markov sólo en ese target. Ningún mercado fue promovido.
- Fase 23 recuperó `summary` para 1,140/1,140 partidos con identidad válida y titulares completos; las cuotas de apertura sólo cubren 10/241 partidos confirmatorios y quedaron excluidas.
- Fase 24 evaluó alineaciones, formación y continuidad histórica: lineup solo `0.628249` frente a baseline `0.629820`; la fusión con ritmo `0.631508`. Ninguna mejora tiene IC estrictamente positivo; la fase queda `rejected_for_revision`.
- Fase 25 congeló el catálogo shadow: Markov/baseline de Fase 21 siguen siendo oficiales; ritmo, alineaciones, fusión y cuotas quedan desactivados por defecto. No se modificó la salida oficial.
- Fase 26 conectó el catálogo al endpoint pre-match: la respuesta conserva los 14 campos oficiales, añade sólo metadatos de observación y valida el contrato al iniciar el servicio.
- Fase 26 observó una respuesta HTTP 200 reproducible; los 4 candidatos permanecieron desactivados, no se calcularon outputs experimentales y no se usaron datos del partido objetivo.
- La prueba E2E desde la imagen Docker encontró y corrigió una omisión de empaquetado: `shadow_contract.json` no se copiaba a la imagen. Tras incluirlo en `Dockerfile` y `.dockerignore`, el gate quedó `ci_e2e_approved`.
- El recorrido empaquetado validó health, readiness, métricas, OpenAPI, pre-match, replay determinista, live, rechazo anti-leakage, partido bloqueado, Hawkes oficial bloqueado, ejecución como usuario no root y limpieza del contenedor.
- Fase 27 observó 241 predicciones oficiales de Fase 21 junto con sus filas causales de Fase 22 y contexto de Fase 23; hubo 241/241 coincidencias de feature, contexto y cutoff.
- Fase 27 confirmó los 8 targets del router oficial, sin fallos de selección, sin recalcular modelos, sin entrenar y sin publicar targets o pérdidas post-match.
- Fase 27 quedó cerrada exitosamente con replay reproducible.
- Fase 28 capturó prospectivamente en sólo lectura 42 partidos completos y 6,795 snapshots desde `prospective_staging_v2`; superó el mínimo de 30 sin reutilizar históricos.
- Fase 28 confirmó orden temporal estable, cero snapshots duplicados, cero eventos futuros visibles, cero partidos huérfanos y cero escrituras PostgreSQL; el replay fue idéntico.
- Fase 28 queda válida como observación read-only, pero no como confirmación independiente.
- Fase 29 auditó la elegibilidad sin calcular métricas: los 42/42 partidos de Fase 28 aparecen en `phase_20_full_preconfirmation_retraining/calibration.json`.
- Fase 29 bloquea correctamente la evaluación de esa cohorte; no se calcularon pérdidas, bootstrap ni promoción y el router no cambió.
- El inventario SELECT-only actual de `prospective_staging_v2` contiene sólo 44 partidos, con rango 2025-10-26 a 2025-11-30; no hay una cohorte posterior válida disponible localmente.
- La consulta ESPN read-only del 2026-07-20 al 2026-07-26 devolvió cero referencias elegibles y cero escrituras; se conserva como `source_returned_no_eligible_matches`.
- La consulta ESPN ampliada del 2025-11-30 al 2026-07-26 encontró 245 partidos, pero 241 ya pertenecen a la confirmación Fase 20/21 y 4 a su calibración; el dry-run no escribió staging.
- Fase 30 conectó el conector R5 a un ejecutor operativo con ventana móvil UTC, refresco de incompletos, escritura explícita sólo en staging y bloqueo de reutilización de Fases 20/21.
- El buscador adaptativo ejecutó la ventana reciente y después las temporadas completas 2025 y 2024; encontró partidos históricos en ESPN, pero 0 candidatos fuente independientes tras cutoff/reutilización y 0 escrituras nuevas.
- Fase 31 implementó el gate SELECT-only que separa candidatos completos independientes de registros históricos o reutilizados; el staging actual no aporta candidatos.
- Fase 32 implementó la preparación causal que exige features y contexto pre-match alineados por partido y cutoff; no genera predicciones si falta una fuente.
- La auditoría del flujo detectó que los candidatos nuevos aún no tenían un paso explícito de materialización de features/contexto; no se consideró suficiente depender de artefactos históricos de Fases 22–23.
- Fase 33 materializa por candidato las features con historial estrictamente previo al kickoff y el contexto ESPN sanitizado; no usa eventos, marcador final, estadísticas post-match ni modifica el router.
- La corrida actual de Fase 33 queda en espera: 0 candidatos independientes, 0 features, 0 contextos, 0 predicciones y 0 targets.
- Fase 34 dejó listo el paquete de inferencia pre-match: reconstruye Markov v2 y el selector de Fase 21 sin leer targets ni pérdidas del candidato.
- La corrida actual de Fase 34 queda en espera: 0 candidatos preparados y 0 predicciones generadas; router y mercados permanecen sin cambios.
- Fase 35 dejó listo el scoring confirmatorio: sólo leerá scores/eventos después de las predicciones y calculará log-loss/bootstrap por partido.
- La corrida actual de Fase 35 queda en espera: 0 predicciones recibidas, 0 targets leídos y 0 pérdidas calculadas.
- Fase 36 auditó 49 slugs explícitos de la documentación ESPN durante todo 2025: 17,885 tareas liga-fecha, 9,775 referencias únicas, 41 ligas con partidos y 8 con cobertura cero; no descargó play-by-play ni escribió PostgreSQL.
- Fase 36 conserva las referencias con `league_slug` y deduplica por liga, partido ESPN y competición. El router oficial de LaLiga no cambió.
- Fase 37 ya tiene preparada la ampliación aditiva de `prospective_staging_v2.matches` con `league_slug`; no se aplicó porque este entorno no expone `DATABASE_URL`.
- Fase 37 ya aplicó correctamente la migración `league_slug` y completó el smoke multi-liga: 83 partidos normalizados de 42 ligas, cero fallos y escritura staging verificada; el inventario PostgreSQL quedó en 127 partidos y 21,014 eventos incluyendo las filas previas.
- El primer backfill completo fue detenido por el sistema al retener demasiados payloads crudos en memoria; se corrigió el ingestor para liberar cada lote después de persistirlo. Las ligas ya escritas son reutilizables por caché e idempotencia, y el backfill debe relanzarse con el ejecutor corregido.
- El backfill corregido terminó con 9,775/9,775 partidos normalizados tras reintentar 9 fallos temporales HTTP de `ger.1` y `mex.1`; PostgreSQL contiene 1,245,630 eventos. Hay 9,745 partidos completos para entrenamiento y 30 registros `post` sin marcador completo retenidos para auditoría.
- Fase 38 materializó 111,528 ventanas de 15 minutos para 9,294 partidos utilizables de 42 ligas. Excluyó 30 incompletos, 438 sin timeline y 13 con timeline parcial inconsistente; tandas de penales quedaron fuera de los goles de juego.
- Fase 38 quedó `validated_for_multileague_labeling_with_exclusions`; aún no se etiquetaron estados ni se entrenó Markov global.
- Fase 39 etiquetó 111,528 ventanas multi-liga sin `unknown`; sensibilidad máxima 0.0820.
- Fase 40 calibró 92,940 transiciones de 9,294 partidos y 41 ligas. Log-loss de validación `0.776450` y confirmación `0.765990`; usa backoff team→liga→ventana→global y no modifica el modelo oficial.
- Fase 41 simuló 5,000 trayectorias deterministas de seis ventanas para 40 ligas con priors estimados sólo en desarrollo; todas las distribuciones se normalizaron y no se usaron eventos, scores ni targets objetivo.
- `fifa.intercontinental_cup` quedó fuera de Fase 41 porque sus cinco partidos no tienen soporte en desarrollo; no se aplicó backoff a datos de confirmación para ocultar esa ausencia.
- Fase 41 no calculó goles ni mercados; Fase 42 ya aporta la fusión estructural necesaria para abrir evaluación OOS multi-liga.
- Fase 42 generó 3,713 predicciones OOS candidatas: 1,856 de validación y 1,857 de confirmación, usando un prior Dixon-Coles regularizado por liga, Kalman actualizado sólo después de cada predicción y Markov con conservación de intensidad.
- Fase 42 registró explícitamente `mle_optimized: false`; no se hizo pasar el prior regularizado por un MLE Dixon-Coles global.
- Fase 42 excluyó cinco partidos de `fifa.intercontinental_cup` sin desarrollo y usó fallback neutral en tres ligas con soporte escaso; ninguna salida se promovió.
- Fase 43 evaluó 3,713 predicciones por partido completo con 5,576 partidos de desarrollo para baselines y bootstrap agrupado por partido.
- En confirmación, la fusión perdió frente al Poisson estructural en 1X2 (`-0.052381`), Over 2.5 (`-0.145486`), BTTS (`-0.069113`), primer tiempo (`-0.027021`) y segundo tiempo (`-0.065735`); sus IC 95% no sostienen mejora.
- Fase 43 quedó `rejected_for_promotion`; el router oficial, los mercados y el baseline no se modificaron.
- Fase 44 corrigió el defecto de precisión: 1X2, Over 2.5 y BTTS ahora usan Poisson analítico cuando la masa Markov se conserva; los tres coinciden con el baseline estructural.
- Fase 45 seleccionó en validación una mezcla 25% Markov/75% estructural para primer tiempo y 30% Markov/70% estructural para segundo tiempo.
- En confirmación, primer tiempo quedó en `-0.000127` con IC `[-0.001275, 0.001030]` y segundo tiempo en `-0.001421` con IC `[-0.002902, -0.000009]`; no hay valor incremental confirmado.
- Fase 45 quedó `temporal_signal_no_incremental_value`; ningún mercado se activa ni se promueve.
- Fase 46 evaluó un prior inicial condicionado por los últimos cinco partidos de cada equipo: ritmo, presión y disciplina. Los perfiles se calcularon antes de cada fecha y sus umbrales/priors se ajustaron sólo en desarrollo.
- Fase 46 cubrió 3,713 predicciones; el prior específico de perfil se usó en 5,863 de 7,426 roles equipo-partido (`78.95%`).
- En confirmación, el candidato perdió frente al Poisson estructural en primer tiempo (`-0.028410`, IC `[-0.059542, -0.002891]`) y segundo tiempo (`-0.079817`, IC `[-0.138943, -0.029396]`).
- Fase 46 quedó `profile_candidate_evaluated_no_promotion`; no se modificó el router y se conserva como evidencia negativa.
- Fase 47 detectó que el gate anterior marcaba 1,801 partidos como independientes aunque 1,791 ya estaban en predicciones de Fase 42 y 1,794 en el corpus de ventanas multi-liga.
- Fase 47 amplió el catálogo de reutilización al corpus completo de ventanas: sólo 7 registros quedan elegibles, por debajo del mínimo de 30. La consulta fue SELECT-only, los conteos fueron idénticos y no se ejecutó evaluación.
- Fase 47 también corrigió la dependencia del flujo oficial: Fases 32–35 quedan en espera coherente cuando no hay cohorte aprobada; no se generaron features, predicciones ni pérdidas.
- Fase 48 añadió `POST /v1/predict/upcoming`: recibe liga, equipos y kickoff, construye un baseline Poisson estructural desde el snapshot multi-liga y devuelve 1X2, Over 2.5, BTTS, goles esperados, provenance y freshness.
- Fase 48 valida cutoff causal, rechaza partidos pasados, ligas desconocidas e histórico insuficiente, y mantiene Markov multi-liga fuera de la salida.
- Fase 49 añadió `src/espn_fixture_resolver.py`: resuelve un único fixture futuro desde ESPN por IDs o nombres normalizados, con ventana UTC y rechazo de ambigüedad o partido iniciado.
- Fase 49 añadió `POST /v1/predict/fixture`: conecta el fixture resuelto con la vertical universal sólo en `operational_readonly`; no persiste ni usa datos del partido objetivo.
- Fase 49 añadió `scripts/run_phase_49_snapshot_refresh.py`: el refresco es dry-run por defecto y `--write-staging` autoriza sólo `prospective_staging_v2`; no reemplaza el snapshot canónico, no entrena y no evalúa.
- Smoke real de Fase 49 sobre `esp.1` y `20260727` terminó como `refresh_no_new_source`: ESPN respondió sin partidos nuevos, no hubo escrituras y el snapshot canónico permaneció intacto.
- Fase 50 publicó y activó `phase38_multileague_v1_20260727`: 111,528 filas, 9,294 partidos, 41 ligas, hash verificado y rollback disponible; el snapshot fuente no fue sobrescrito.
- El servicio selecciona el snapshot activo o `DIKAMAHA_PREMATCH_SNAPSHOT_ID` y expone `snapshot_id` en la provenance de cada predicción universal.
- Fase 51 verificó el flujo real con Puebla–Guadalajara (`401877027`) de Liga MX: ESPN resolvió el fixture, el endpoint respondió HTTP 200 y la salida usó el snapshot activo sin persistencia ni datos del objetivo.
- Fase 51 detectó `history_freshness_warning`: el histórico activo termina en diciembre de 2025 y el fixture probado es de agosto de 2026; la predicción es causal pero requiere refresco para operación confiable.
- Fase 52 consultó 185 referencias ESPN de `mex.1`, incorporó 168 partidos completos y 2,016 ventanas post-2025, excluyó 17 discrepancias de marcador y activó `phase52_post2025_mex_v1_20260727` sin escribir PostgreSQL.
- Fase 53 procesó 42 ligas documentadas en la ventana reciente, seleccionó 24 referencias, incorporó 6 partidos completos y 72 ventanas, excluyó 18 referencias no reconciliadas o incompletas, y activó `phase53_multileague_post2025_v1_20260727` tras un `dry-run` exitoso. El flujo real volvió a pasar con HTTP 200.
- Fase 54 amplió el rango a enero-julio de 2026: descubrió 4,873 referencias, seleccionó 322, incorporó 293 partidos completos y 3,516 ventanas, excluyó 29 referencias no reconciliadas o sin timeline, y activó `phase54_multileague_post2025_v1_20260727`. El snapshot activo quedó en 117,000 filas, 9,750 partidos y 42 ligas; el flujo real volvió a pasar con HTTP 200 y sin advertencia de frescura.
- Fase 55 verificó el endpoint universal por nombres: resolvió Puebla–Guadalajara con ESPN, devolvió HTTP 200 usando `phase54_multileague_post2025_v1_20260727`, respetó cutoff causal y no persistió datos durante la request.
- Fase 56 escaneó 42 ligas y encontró 10 fixtures futuros; 9 solicitudes universales pasaron HTTP 200 y cutoff causal. Uruguay fue rechazado por historia insuficiente, sin inventar salida.
- Fase 57 implementó el refresco incremental de siete días: 101 referencias, 7 partidos completos, 84 ventanas candidatas y 12 filas netas nuevas; activó `phase57_incremental_v1_20260727` tras dry-run y revalidó Puebla–Guadalajara.
- Fase 58 incorporó la nueva faceta de valor incremental: auditó el OOS canónico, confirmó que Dixon-Coles + Kalman no domina a Dixon-Coles en todos los mercados, no activó Kalman ni Markov y dejó definida la especificación de Markov residual selectivo.
- Fase 59 auditó 117,012 filas de 9,751 partidos: la estructura de 12 ventanas por partido pasa sin fallos, pero el snapshot activo conserva la clasificación anterior y no se modifica automáticamente. La cohorte raw de 30 seleccionados normalizó 15: los 1,893 eventos válidos conservaron timestamps ordenados y 15/15 marcadores reconciliaron. Con `espn_event_taxonomy_v1.1`, 1,096 eventos quedan como auxiliares, 797 como modelables y 0 como `unclassified`; el bloqueo posterior quedó trasladado a recalibración OOS.
- Fase 60, tras Fase 61, rematerializó 10,202 partidos completos: 9,751 partidos y 117,012 filas coinciden con el snapshot activo, `unclassified=0`, y las 3,893 diferencias son exclusivamente faltas recuperadas. La auditoría de estados candidata encontró 0 cambios en 111,528 etiquetas comunes.
- Fase 61 recuperó 457/457 referencias activas ausentes con `competition_id` ESPN numérico, sin fallos de ingesta; 401 partidos de 2025 permanecen fuera por discrepancia de marcador/tanda.
- Fase 62 congeló 9 fixtures futuros independientes antes del kickoff, sin solicitar play-by-play ni observar resultados.
- Fase 63 calibró un clasificador multinomial causal para `state_0`: log-loss histórico de confirmación `0.745715` frente a `0.838251` global y `0.757864` por liga; `repliegue` carece de soporte en desarrollo, por lo que el candidato permanece sin promoción.
- Fase 63 congeló 9 predicciones candidatas de `first_half_goal` antes de los kickoffs usando state_0, transiciones y emisiones históricas; el artefacto es `frozen_candidate_not_promoted`, no observó resultados/play-by-play y no modificó el router.
- Fase 64 dejó preparado el evaluador OOS selectivo; la corrida actual queda `waiting_for_postmatch_targets` con 9 predicciones, 0 targets y 0 pérdidas. El mínimo de promoción sigue siendo 30 partidos, por lo que esta cohorte no puede promover por sí sola.
- Fase 64 ejecutó además un replay walk-forward diagnóstico de 30 partidos recientes: Markov obtuvo log-loss `0.796682` frente a `0.730142` del baseline; mejora `-0.066540`, IC bootstrap `[-0.151079, 0.017641]`. Confirma funcionamiento técnico, no valor incremental ni promoción.
- Fase 65 auditó el bloque completo posterior al desarrollo: 3,921 partidos walk-forward, Markov `0.627332` frente a baseline `0.626786`, mejora `-0.000546`, IC `[-0.001350, 0.000155]`. Los tiers `global/uniform` dominan, el tier `team` es escaso y Markov no genera lift positivo material.
- Fase 66 probó pooling jerárquico suave con 58,800 transiciones de desarrollo; seleccionó `specificity=2.0`, pero en holdout Markov obtuvo `0.641220` frente a `0.639682` del baseline, mejora `-0.001539`, IC `[-0.003164, -0.000244]`. La recalibración de transición queda rechazada para promoción.
- Fase 67 detectó desalineación state→emission: las reglas etiquetan la ventana actual mientras la emisión usa sus mismos goles; la brecha misma→siguiente va de `0.0235` a `0.0540` goles por estado.
- Fase 68 probó emisiones desplazadas y Fase 69 un residual directo de `state_0`; ambos perdieron en holdout (`0.640806` y `0.640280`) frente a `0.639682` del baseline. La siguiente corrección debe revisar `state_labeling_v2`, incorporando shots, tiros a puerta, corners, goles y disciplina.
- Fase 70 auditó `state_labeling_v2`: la distribución es más amplia, pero el spread de goles de la siguiente ventana baja de `0.132934` a `0.085693`; v2 queda rechazada como reemplazo y `state_labeling_v1` se conserva.
- Fase 71 reemplazó el diagnóstico de dos cadenas recíprocas por una cadena conjunta de ritmo, separó control direccional, excluyó goles de los labels y alineó la evidencia de `t` con el régimen de `t+1`.
- Cuatro configuraciones semánticas fueron seleccionadas sólo en validación; todas eligieron `alpha=0.0`. La mejor variante obtuvo spread de gol siguiente `0.020323`, `state_0` mejoró `0.016647` frente al prior de liga y la transición quedó `-0.000026`.
- El holdout de 1,961 partidos devolvió exactamente el baseline: log-loss `0.639682`, Brier `0.223447`, mejora `0.0`. La abstención es funcional, causal y no modifica el router.
- La prueba de fusión residual de Fase 64 seleccionó `alpha=0.0` en validación: el peso óptimo de Markov fue nulo; en el holdout la fusión quedó en log-loss `0.777966` sin mejora. Se conserva el baseline como ancla y Markov no se activa.
- La revalidación de Puebla–Guadalajara quedó HTTP 200, con histórico hasta 2026-07-18, 13 días de antigüedad y `history_freshness_warning=False`.

## Pendiente inmediato

- Mantener el router vigente y el catálogo shadow en modo `official_only`; no activar ritmo, alineaciones ni cuotas.
- No abrir otra variante de Markov con los mismos agregados de 15 minutos: ya fallaron transición rígida/suave, emisiones contemporánea/desplazada/directa y cuatro semánticas conjuntas.
- Una reapertura requiere una fuente pre-match nueva o un target direccional versionado, hipótesis registrada antes de implementación y cohorte independiente.
- La cohorte prospectiva disponible no es apta para confirmación independiente; Fases 30–35 quedan listas para incorporar, filtrar, materializar, preparar, predecir y evaluar automáticamente partidos posteriores al último bloque evaluado cuando ESPN los publique.
- Mantener mercados temporales, remontadas, v1 y v2 fuera de promoción.
- Sólo una evaluación confirmatoria independiente puede reabrir la decisión de promoción.
- Esperar los kickoffs de la cohorte independiente congelada y ejecutar la evaluación post-match de `first_half_goal` con log-loss, bootstrap por partido y estabilidad por liga.
- Cuando exista el JSON de ventanas post-match, ejecutar `scripts/run_phase_64_selective_oos_evaluation.py`; si los 9 partidos están completos, conservar la evidencia como insuficiente por soporte y no promover.
- El replay histórico no sustituye la cohorte independiente; la siguiente decisión debe basarse en la evaluación prospectiva posterior al kickoff.
- Conservar `artifacts/phase_71_state_semantic_revision_v1/` como evidencia negativa reproducible y usar su fallback exacto al baseline si el candidato se ejecuta en shadow.

## Secuencia operativa vigente

`ESPN -> Fase 36 discovery multi-liga -> Fase 37 staging global aislado -> Fase 38 ventanas 15m -> Fase 39 estados -> Fase 40 Markov -> Fase 41 simulación de estados -> Fase 42 fusión estructural -> evaluación OOS -> router`

El flujo oficial anterior (`Fase 30 -> 31 -> 33 -> 32 -> 34 -> 35`) permanece
congelado y separado del corpus global.

La secuencia de producto queda `49 -> 50 -> 51 -> 52 -> 53 -> 54 -> 55 -> 56 -> 57 -> 58 -> 59 -> 60 -> 61 -> 62 -> 63`; la línea de investigación de Markov residual no puede promocionarse hasta superar su gate OOS independiente. El snapshot es seleccionable por configuración, verificable por hash y reversible; no activa Markov ni sustituye el router oficial.

## Bloqueos conocidos

- `repliegue` sigue siendo escaso en el corpus multi-liga; requiere pooling y shrinkage conservador.
- Una liga no tiene soporte en desarrollo y permanece excluida de la simulación: `fifa.intercontinental_cup`.

## Evidencia vigente

- `docs/00_roadmap_actual.md`
- `docs/decision_log.md`
- `docs/phases/phase_41_multileague_state_simulation.md`
- `artifacts/phase_41_multileague_state_simulation_v1/final_report.md`
- `docs/phases/phase_42_multileague_structural_fusion.md`
- `artifacts/phase_42_multileague_structural_fusion_v1/final_report.md`
- `docs/phases/phase_43_multileague_oos_evaluation.md`
- `artifacts/phase_43_multileague_oos_evaluation_v1/final_report.md`
- `docs/phases/phase_44_multileague_precision_diagnosis.md`
- `artifacts/phase_44_multileague_precision_diagnosis_v1/final_report.md`
- `docs/phases/phase_45_temporal_markov_recalibration.md`
- `artifacts/phase_45_temporal_markov_recalibration_v1/final_report.md`
- `docs/phases/phase_46_profile_conditioned_markov.md`
- `artifacts/phase_46_profile_conditioned_markov_v1/final_report.md`
- `artifacts/phase_46_profile_conditioned_markov_v1/audit.json`
- `artifacts/phase_46_profile_conditioned_markov_v1/metrics.json`
- `docs/phases/phase_47_reuse_catalog_gate.md`
- `artifacts/phase_31_prospective_cohort_gate/final_report.md`
- `artifacts/phase_31_prospective_cohort_gate/gate_result.json`
- `docs/phases/phase_48_universal_prematch_flow.md`
- `artifacts/phase_48_universal_prematch_flow_v1/final_report.md`
- `artifacts/phase_48_universal_prematch_flow_v1/prediction.json`
- `artifacts/phase_48_universal_prematch_flow_v1/audit.json`
- `artifacts/phase_01_event_windows_v1/final_report.md`
- `artifacts/phase_02_state_labeling_v1/final_report.md`
- `artifacts/phase_03_markov_pre_match_v1/final_report.md`
- `artifacts/phase_03_markov_pre_match_v1/validation_report.md`
- `artifacts/phase_04_pre_match_simulation_v1/final_report.md`
- `artifacts/phase_05_evaluation_protocol_v1/final_report.md`
- `artifacts/phase_05_evaluation_protocol_v1/audit.json`
- `artifacts/phase_05_canonical_oos_predictions_v1/final_report.md`
- `artifacts/phase_05_evaluation_protocol_v1/final_report.md`
- `artifacts/phase_05_evaluation_protocol_v1/validation_report.md`
- `artifacts/phase_06_markov_v2_goal_prior/final_report.md`
- `artifacts/phase_06_markov_v2_simulation/final_report.md`
- `artifacts/phase_06_markov_v2_oos_predictions/final_report.md`
- `artifacts/phase_06_markov_v2_evaluation/final_report.md`
- `artifacts/phase_06_markov_v2_evaluation/validation_report.md`
- `artifacts/phase_07_markov_temporal_residual/final_report.md`
- `artifacts/phase_08_temporal_target_audit/final_report.md`
- `docs/specs/temporal_targets_v2.md`
- `docs/phases/phase_09_historical_target_revision.md`
- `artifacts/phase_09_historical_target_revision/final_report.md`
- `artifacts/phase_09_historical_target_revision/validation_report.md`
- `docs/phases/phase_10_temporal_target_evaluation.md`
- `artifacts/phase_10_temporal_target_evaluation/final_report.md`
- `artifacts/phase_10_temporal_target_evaluation/validation_report.md`
- `docs/phases/phase_11_historical_extension_fetch.md`
- `artifacts/phase_11_historical_extension_fetch/final_report.md`
- `docs/phases/phase_12_extension_windows_targets.md`
- `artifacts/phase_12_extension_windows_targets/final_report.md`
- `docs/phases/phase_13_temporal_target_evaluation_extension.md`
- `artifacts/phase_13_temporal_target_evaluation_extension/final_report.md`
- `artifacts/phase_14_dynamic_markov_recalibration/final_report.md`
- `artifacts/phase_17_extended_markov_retraining/final_report.md`
- `artifacts/phase_20_full_preconfirmation_retraining/final_report.md`
- `artifacts/phase_21_target_model_router/final_report.md`
- `docs/phases/phase_22_prematch_first_half_signal.md`
- `artifacts/phase_22_prematch_first_half_signal/final_report.md`
- `artifacts/phase_22_prematch_first_half_signal/audit.json`
- `docs/phases/phase_23_prematch_context_fetch.md`
- `artifacts/phase_23_prematch_context_fetch/final_report.md`
- `docs/phases/phase_24_prematch_lineup_signal.md`
- `artifacts/phase_24_prematch_lineup_signal/final_report.md`
- `artifacts/phase_24_prematch_lineup_signal/audit.json`
- `docs/phases/phase_25_shadow_model_catalog.md`
- `artifacts/phase_25_shadow_model_catalog/final_report.md`
- `artifacts/phase_25_shadow_model_catalog/shadow_contract.json`
- `docs/phases/phase_26_shadow_runtime_integration.md`
- `artifacts/phase_26_shadow_runtime_integration/final_report.md`
- `artifacts/phase_26_shadow_runtime_integration/audit.json`
- `artifacts/phase_6_6_ci_e2e/report.md`
- `artifacts/phase_6_6_ci_e2e/smoke_results.json`
- `artifacts/phase_6_6_ci_e2e/security_results.json`
- `docs/phases/phase_27_shadow_observation.md`
- `artifacts/phase_27_shadow_observation/final_report.md`
- `artifacts/phase_27_shadow_observation/audit.json`
- `docs/phases/phase_28_prospective_shadow_collection.md`
- `artifacts/phase_7_11_prospective_collection/final_report.md`
- `artifacts/phase_7_11_prospective_collection/collection_status.json`
- `artifacts/phase_7_11_prospective_collection/temporal_audit.json`
- `artifacts/phase_7_11_prospective_collection/postgres_readonly_audit.json`
- `artifacts/phase_7_11_prospective_collection/provenance_audit.json`
- `artifacts/phase_7_11_prospective_collection/manifest.json`
- `docs/phases/phase_29_confirmatory_eligibility_audit.md`
- `artifacts/phase_29_confirmatory_eligibility_audit/final_report.md`
- `artifacts/phase_29_confirmatory_eligibility_audit/eligibility_audit.json`
- `artifacts/phase_7_15_espn_connector/final_report.md`
- `artifacts/phase_7_15_espn_connector/manifest.json`
- `artifacts/phase_7_15_espn_connector_r5/final_report.md`
- `artifacts/phase_7_15_espn_connector_r5/eligible_matches.json`
- `artifacts/phase_7_15_espn_connector_r5/audit.json`
- `docs/phases/phase_30_operational_espn_sync.md`
- `docs/phases/phase_31_prospective_cohort_gate.md`
- `artifacts/phase_31_prospective_cohort_gate/final_report.md`
- `artifacts/phase_31_prospective_cohort_gate/gate_result.json`
- `docs/phases/phase_32_prematch_candidate_preparation.md`
- `artifacts/phase_32_prematch_candidate_preparation/final_report.md`
- `artifacts/phase_32_prematch_candidate_preparation/preparation_result.json`
- `docs/phases/phase_49_fixture_resolver_snapshot_refresh.md`
- `src/espn_fixture_resolver.py`
- `scripts/run_phase_49_snapshot_refresh.py`
- `artifacts/phase_49_fixture_resolver_snapshot_refresh_v1/final_report.md`
- `artifacts/phase_49_fixture_resolver_snapshot_refresh_v1/audit.json`
- `docs/phases/phase_50_versioned_snapshot_materialization.md`
- `src/prematch_snapshot_registry.py`
- `scripts/manage_prematch_snapshot.py`
- `scripts/run_phase_50_snapshot_materialization.py`
- `artifacts/phase_50_versioned_snapshot_materialization_v1/final_report.md`
- `artifacts/phase_50_versioned_snapshot_materialization_v1/audit.json`
- `docs/phases/phase_51_real_fixture_flow.md`
- `scripts/run_phase_51_real_fixture_flow.py`
- `artifacts/phase_51_real_fixture_flow_v1/final_report.md`
- `artifacts/phase_51_real_fixture_flow_v1/sanitized_result.json`
- `artifacts/phase_51_real_fixture_flow_v1/audit.json`
- `docs/phases/phase_52_post2025_snapshot_refresh.md`
- `scripts/run_phase_52_post2025_snapshot_refresh.py`
- `artifacts/phase_52_post2025_snapshot_refresh_v1/final_report.md`
- `artifacts/phase_52_post2025_snapshot_refresh_v1/audit.json`
- `docs/phases/phase_53_multileague_post2025_refresh.md`
- `scripts/run_phase_53_multileague_post2025_refresh.py`
- `artifacts/phase_53_multileague_post2025_refresh_v1/final_report.md`
- `artifacts/phase_53_multileague_post2025_refresh_v1/audit.json`
- `docs/phases/phase_54_multileague_extended_refresh.md`
- `artifacts/prematch_snapshots/phase54_multileague_post2025_v1_20260727/manifest.json`
- `docs/phases/phase_55_universal_named_fixture_flow.md`
- `scripts/run_phase_55_universal_named_fixture_flow.py`
- `artifacts/phase_55_universal_named_fixture_flow_v1/final_report.md`
- `artifacts/phase_55_universal_named_fixture_flow_v1/audit.json`
- `docs/phases/phase_56_multileague_upcoming_flow.md`
- `scripts/run_phase_56_multileague_upcoming_flow.py`
- `artifacts/phase_56_multileague_upcoming_flow_v1/final_report.md`
- `artifacts/phase_56_multileague_upcoming_flow_v1/audit.json`
- `docs/phases/phase_57_incremental_snapshot_refresh.md`
- `scripts/run_phase_57_incremental_snapshot_refresh.py`
- `artifacts/phase_57_incremental_snapshot_refresh_v1/final_report.md`
- `artifacts/phase_57_incremental_snapshot_refresh_v1/audit.json`
- `docs/phases/phase_58_rebaseline_audit.md`
- `scripts/run_phase_58_rebaseline_audit.py`
- `artifacts/phase_58_rebaseline_audit_v1/final_report.md`
- `artifacts/phase_58_rebaseline_audit_v1/audit.json`
- `docs/specs/markov_residual_selective_v1.md`
- `docs/phases/phase_59_event_quality_audit.md`
- `scripts/run_phase_59_event_quality_audit.py`
- `artifacts/phase_59_event_quality_audit_v1/final_report.md`
- `artifacts/phase_59_event_quality_audit_v1/audit.json`
- `scripts/run_phase_59_raw_timeline_audit.py`
- `artifacts/phase_59_raw_timeline_audit_v1/final_report.md`
- `artifacts/phase_59_raw_timeline_audit_v1/audit.json`
- `src/espn_event_taxonomy.py`
- `tests/test_espn_event_taxonomy.py`
- `docs/specs/espn_event_taxonomy_v1_1.md`
- `docs/phases/phase_60_taxonomy_snapshot_candidate.md`
- `scripts/run_phase_60_taxonomy_snapshot_candidate.py`
- `artifacts/phase_60_taxonomy_snapshot_candidate_v1/final_report.md`
- `artifacts/phase_60_taxonomy_snapshot_candidate_v1/audit.json`
- `docs/specs/markov_state_semantics_v3.md`
- `docs/phases/phase_71_state_semantic_revision.md`
- `src/markov_semantic_v3.py`
- `scripts/run_phase_71_state_semantic_revision.py`
- `artifacts/phase_71_state_semantic_revision_v1/final_report.md`
- `artifacts/phase_71_state_semantic_revision_v1/audit.json`
- `artifacts/phase_71_state_semantic_revision_v1/metrics.json`
- `scripts/run_phase_60_candidate_state_audit.py`
- `artifacts/phase_60_candidate_state_audit_v1/final_report.md`
- `artifacts/phase_60_candidate_state_audit_v1/audit.json`
- `scripts/run_phase_61_source_coverage_closure.py`
- `docs/phases/phase_61_source_coverage_closure.md`
- `artifacts/phase_61_source_coverage_closure_v1/final_report.md`
- `artifacts/phase_61_source_coverage_closure_v1/audit.json`
- `scripts/run_phase_62_independent_cohort_lock.py`
- `docs/phases/phase_62_independent_cohort_lock.md`
- `artifacts/phase_62_independent_cohort_lock_v1/final_report.md`
- `artifacts/phase_62_independent_cohort_lock_v1/cohort.json`
- `docs/phases/phase_63_initial_state_calibration.md`
- `scripts/run_phase_63_initial_state_calibration.py`
- `artifacts/phase_63_initial_state_calibration_v1/final_report.md`
- `artifacts/phase_63_initial_state_calibration_v1/audit.json`
- `artifacts/phase_63_initial_state_calibration_v1/state0_classifier.joblib`
- `docs/phases/phase_60_taxonomy_snapshot_candidate.md`
- `scripts/run_phase_60_taxonomy_snapshot_candidate.py`
- `artifacts/phase_60_taxonomy_snapshot_candidate_v1/final_report.md`
- `artifacts/phase_60_taxonomy_snapshot_candidate_v1/audit.json`
