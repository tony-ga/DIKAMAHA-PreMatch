# Estado operativo DIKAMAHA

**Actualizado:** 2026-08-12
**Fase activa:** Fase 123 Validación prospectiva del menú de mayor probabilidad
**Objetivo Fase 123:** convertir la evidencia histórica post-hoc de Fase 122 en
confirmación prospectiva real, congelando los picks del menú antes del
kickoff y liquidándolos después contra el resultado verificado.

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
