# Roadmap vigente de DIKAMAHA

**Actualizado:** 2026-08-09
**Objetivo operativo:** preservar la integridad matemática y causal pre-match
de Fase 113 mientras Fase 116 opera el motor in-live oficial compuesto y Fase
115 lo presenta mediante la Mini App, sin afirmar ventaja económica.

La corrección operativa DEC-151 adapta exclusivamente la cookie HttpOnly al
contexto embebido de Telegram Web/Desktop y exige confirmar la sesión antes de
cargar catálogos. No modifica datos ni modelos.

Fase 113 reemplaza las cifras de Fases 84A, 88, 94, 95, 96, 103, 104, 105 y
106 cuando dependían de recorridos intra-kickoff, fronteras fraccionales o
métricas mixtas. La salida vigente conserva 1X2 y over 2.5 en la cadena
oficial, BTTS reparado por Fase 106 y ocho mercados de equipo en shadow.
La revisión operativa de artefactos queda validada para despliegue: la imagen
Linux verifica sólo los componentes que consume y acepta la representación
LF/CRLF sin relajar contenido, esquema ni hashes binarios.

## Objetivo congelado (archivado, DEC-170)

Generar predicciones pre-match mediante simulación de trayectorias históricas.
El núcleo nuevo era `markov_pre_match v4`: un modelo latente, direccional y
con duración, aprendido sobre microventanas causales y agregado a mercados de
15 minutos. Dixon-Coles y Kalman aportan capacidad estructural y forma reciente.

**Este objetivo queda archivado por DEC-170 (2026-08-12).** Tras ~15
iteraciones rechazadas (Fases 76-80U, cerradas por DEC-100), el proyecto
decidió cerrar el producto actual en producción en vez de seguir persiguiendo
la promoción de Markov v4. Fases 73 (recolección prospectiva), 81
(confirmación independiente), 82 (integración oficial) y 83 (validación de
apuesta) quedan sin trabajo activo ni fecha objetivo — no bloqueadas de forma
ambigua, sino archivadas como resultado de investigación documentado. Las
fases ya integradas como shadow en el producto (84A, 85, 88, 89, 90, 93 y
Markov Live/Hawkes de Fase 114) no se ven afectadas: siguen sirviendo tráfico
real bajo su etiqueta shadow/fallback exacto. Una reapertura requiere una
decisión explícita nueva con cohorte independiente, igual que ya exigía
DEC-100.

## Arquitectura objetivo

`historial causal + contexto snapshot -> Dixon-Coles/Kalman -> estados latentes y duración -> simulación Markov v4 -> mercados temporales pre-match`

Hawkes queda fuera del núcleo pre-match. En live opera exclusivamente como
residual logarítmico acotado dentro de `live_probability_engine_v1`; no publica
una probabilidad competidora y `rho=0` reproduce el baseline analítico.

La Fase 74 rematerializada cerró `ready_for_phase_75`: 9,465 partidos de 39 ligas fueron
reconstruidos directamente desde 1.32 millones de eventos con reloj original,
produciendo 624,690 microventanas causales de 5/10/15 minutos. La siguiente
fase autorizada es el baseline temporal fuerte; aún no se entrenan estados.

La Fase 75 cerró `ready_for_next_phase`: congeló cuatro clases direccionales
por intervalo y seleccionó, sin observar confirmación, un baseline tabular
same-data reproducible. Su log-loss confirmatorio es `0.992701`; esta cifra es
el nuevo comparador mínimo para el descubrimiento latente de Fase 76.

La primera ejecución de Fase 76 fue `rejected_for_revision`: los estados
direccionales mostraron semántica en selección pero el spread de riesgo cayó de
`0.062649` a `0.029279` en confirmación y el NMI mínimo fue `0.569401`.
La duración explícita sí superó al geométrico. Ese bloqueo histórico fue
reemplazado por la evidencia temporal anidada de 76R.

La reauditoría aisló el problema en la GMM sobre conteos cero-inflados y
reemplazó ese candidato por seis estados predictivos balanceados. En selección
alcanzan spread `0.053971`, NMI `0.779114`, duración `+0.115708` y estabilidad
en 30/30 ligas. La clasificación es `promising_unconfirmed`: la formulación
queda funcional, pero Fase 77 no se abre hasta obtener confirmación nueva.

La cohorte independiente ya está operativa y acumulando desde
`2026-07-26T18:00:00Z`. La primera captura contiene 19 partidos de 5 ligas.
Durante su construcción se corrigió un truncamiento sistemático de ESPN por
ignorar `pageCount`; tras reingesta, 19/19 marcadores reconcilian. El gate
permanece `insufficient_coverage` hasta 200 partidos/10 ligas para Fase 76 y
500/10 para Fase 81.

La revisión robusta v3 sustituyó escalas absolutas por contrastes, cuotas,
eficiencias y memoria causal. Cumplió todos los gates internos, pero en el
holdout sellado obtuvo spread `0.042423` sobre 376 partidos de 9 ligas. Fase 76
permanece rechazada y ese holdout queda clausurado para nuevas iteraciones.

La revisión 76R retiró v3 como ruta activa y corrigió la compresión causada por
cuartiles uniformes. Los estados de cola `10/50/90`, seleccionados dentro de
cada train, aprobaron dos folds externos: spread `0.056876/0.056224`, NMI
`0.847527/0.796878`, estabilidad por liga `100%/96%` y duración positiva.
Fase 76 queda `ready_for_next_phase`; Fase 77 está autorizada.

Fase 77 detectó que el régimen dinámico puro no era predecible al kickoff y
adoptó la factorización `style_state(2) × match_regime(3)`. En dos folds OOS
mantiene spread `0.064109/0.064365`, NMI `0.892442/0.891485` y mejora el
log-loss de state_0 `46.75%/46.59%` con Brier/ECE favorables. Fase 78 queda
autorizada.

Fase 79 cerró el simulador dual coherente: 5,000 trayectorias contextuales y
5,000 core son reproducibles, conservan las lambdas con error máximo
`6.661e-16`, mantienen estilo fijo y agregan 5→15 minutos sin lecturas
post-cutoff. Fase 80 queda autorizada; el router permanece en baseline.

Fase 80 reajustó todos los componentes dentro de cada fold y probó contexto,
duración, dirección, granularidad y fuerza residual. Tras corregir tres
artefactos mecánicos, Markov quedó indistinguible del tabular: mejora
`-0.000002`, IC95% `[-0.000019, 0.000016]`. La fase queda rechazada, Fase 81
bloqueada y la próxima revisión debe puntuar dependencia de trayectoria
completa, no el mismo marginal por ventana.

Fase 80R puntuó el likelihood de las seis ventanas y añadió un comparador
secuencial directo. La selección prefirió `no_transition`; en confirmation el
directo ganó `0.989387` frente a `0.989798`, con IC95% cruzando cero. La cadena
latente queda rechazada. Sólo se permiten mercados de trayectoria shadow en
Fase 80S; Fase 81 permanece bloqueada.

Fase 80S materializó cinco mercados pre-match de trayectoria en shadow:
intervalo del primer gol, ventanas activas, consecutividad, clustering y
segunda mitad más activa. La salida es reproducible, conserva lambdas y está
forzada a `experimental_shadow_not_promoted`; no constituye promoción.

Fases 80T/80U probaron estado persistente pre-match discreto y transición
continua no homogénea. 80U fue la mejor: gana `0.001797` al Markov directo,
pero sólo `0.000431` al continuo same-data, con IC cruzando cero. DEC-100
clausura los bloques actuales para evitar sobreajuste; la familia queda shadow
y Fase 81 bloqueada.

## Nueva faceta: valor incremental verificable de Markov

Markov v4 modelará distribución por equipo, tiempo y régimen, sin reemplazar la
capacidad estructural de Dixon-Coles/Kalman. `first_half_goal` pasa a ser un
mercado derivado; los targets primarios son direccionales por intervalo. La
promoción exige superar al mejor baseline y a un modelo tabular con exactamente
los mismos datos, además de confirmación prospectiva independiente. El plan y
los gates completos están en `docs/plan_markov_prematch_v4.md`.

## Fases vigentes

| Fase | Entregable | Estado | Gate de salida |
| --- | --- | --- | --- |
| 01 | `event_windows v1` | validada | 381 partidos, 4,572 ventanas y auditoría read-only reproducible |
| 02 | `state_labeling v1` | validada | 4,572 etiquetas causales; sensibilidad máxima de 11.83% |
| 03 | `markov_pre_match v1` | validada | Transiciones con soporte, smoothing y backoff; mejora de likelihood temporal |
| 04 | `pre_match_simulation v1` | validada | 5,000 trayectorias deterministas, orientación y provenance auditadas |
| 05 | `evaluation_protocol v1` | rechazada | Suite OOS de 264 partidos: Markov dependiente no justifica promoción |
| 06 | `markov_pre_match v2` | rechazada para promoción | Mejora baseline/v1, pero no aporta valor incremental sobre Dixon-Coles |
| 07 | `markov_temporal_residual v1` | rechazada para promoción | Ningún mercado temporal o de remontada supera su baseline con bootstrap confirmatorio |
| 08 | `temporal_target_audit v1` | validada | Targets consistentes; remontadas insuficientes para promoción |
| 09 | `historical_target_revision v2` | validada para revisión de targets | 44 partidos staging completos, 528 ventanas, sin discrepancias ni solapamiento OOS |
| 10 | `temporal_target_evaluation v2` | promising_unconfirmed, sin promoción | Sólo `first_half_goal` mejora al baseline; no hay soporte confirmatorio suficiente para mercados de reacción |
| 11 | `historical_extension_fetch` | validada para extensión | 241 partidos completos y 48,061 eventos ESPN, sin escritura PostgreSQL ni solapamiento |
| 12 | `extension_windows_targets v2` | validada para revisión | 2,892 ventanas, cero discrepancias de marcador y soporte ampliado para targets v2 |
| 13 | `temporal_target_evaluation extension` | rechazada para promoción | Markov v2 no supera el baseline en primera/segunda mitad; ninguna mejora bootstrap confirmada |
| 14 | `dynamic_markov_recalibration` | rechazada para promoción | Priors rolling mejoran primera mitad, pero Markov subestima sistemáticamente segunda mitad |
| 15-16 | `historical_backfill` | validadas para revisión | 380 partidos adicionales, 4,560 ventanas, sin discrepancias |
| 17 | `extended_markov_retraining` | rechazada para promoción | 805 partidos de train; corregida normalización provider→interno; segunda mitad sigue por debajo |
| 18-19 | `current_season_backfill` | validadas para revisión | 95 partidos faltantes de agosto-octubre 2025, 1,140 ventanas, sin discrepancias |
| 20 | `full_preconfirmation_retraining` | rechazada para promoción | 899 partidos válidos previos tras deduplicación; sólo mejora marginal de primera mitad, IC cruza cero |
| 21 | `target_model_router` | promising_unconfirmed | Markov se activa sólo en `first_half_goal`; los demás targets conservan baseline |
| 22 | `prematch_first_half_signal` | promising_unconfirmed | Ritmo histórico de eventos mejora el punto de log-loss en primera mitad, pero el IC confirmatorio cruza cero; se detectó y excluyó un duplicado temporal |
| 23 | `prematch_context_fetch` | ready_for_next_phase | 1,140/1,140 summaries con identidad válida y alineaciones; cuotas `open` sólo en 10/241 partidos confirmatorios |
| 24 | `prematch_lineup_signal` | rechazada para revisión | Alineaciones no confirman mejora; la fusión con ritmo empeora frente al baseline |
| 25 | `shadow_model_catalog` | lista para siguiente fase | Router oficial congelado; cuatro candidatos experimentales desactivados por defecto |
| 26 | `shadow_runtime_integration` | validada para observación | Flujo local y Docker aprobados; 14 campos oficiales preservados, catálogo shadow incluido en la imagen y ningún candidato ejecutado |
| 27 | `shadow_observation` | validada y cerrada | 241 predicciones oficiales alineadas con contexto/cutoff, replay reproducible, router conservado y 4 candidatos shadow sin ejecución |
| 28 | `prospective_shadow_collection` | validada para observación, no confirmatoria | 42 partidos completos y 6,795 snapshots capturados en SELECT-only; cero leakage temporal, cero escrituras y replay idéntico |
| 29 | `confirmatory_eligibility_audit` | bloqueada para evaluación | 42/42 partidos de Fase 28 se reutilizaron en calibración; el escaneo ESPN halló 245 partidos ya usados por calibración/confirmación; no se calcularon métricas ni bootstrap |
| 30 | `operational_espn_sync` | operativa, sin cohorte nueva aún | Ventana móvil y búsqueda adaptativa ESPN, staging aislado, escritura explícita y exclusión automática de IDs usados por el modelo |
| 31 | `prospective_cohort_gate` | validada, esperando cohorte independiente | Gate SELECT-only sobre staging; publica candidatos completos y no reutilizados sin ejecutar evaluación |
| 32 | `prematch_candidate_preparation` | validada, esperando candidatos | Alinea features/contexto/cutoff causal; no ejecuta router ni genera targets |
| 33 | `prematch_input_materialization` | validada, esperando candidatos | Materializa features desde historial previo y contexto ESPN permitido para cada candidato de Fase 31 |
| 34 | `prematch_prediction_package` | validada, esperando candidatos | Reconstruye Markov v2 y aplica el selector congelado para generar probabilidades pre-match sin targets |
| 35 | `confirmatory_evaluation` | validada, esperando predicciones independientes | Calcula log-loss y bootstrap post-match sin modificar el router ni promover mercados |
| 36 | `multileague_discovery` | validada para revisión | 49 slugs documentados, 17,885 tareas liga-fecha, 9,775 referencias únicas y cero escrituras; no abre aún entrenamiento global |
| 37 | `multileague_staging_ingestion` | pendiente de migración | Descargar event/summary/plays de referencias deduplicadas y persistir corpus aislado con `league_slug` |
| 38 | `multileague_event_windows` | validada con exclusiones | 9,294 partidos utilizables, 111,528 ventanas de 15 minutos y auditoría de 468 partidos excluidos |
| 39 | `multileague_state_labeling` | validada | 111,528 ventanas, cuatro estados operativos, cero unknown y sensibilidad máxima 0.0820 |
| 40 | `multileague_markov_calibration` | validada para simulación experimental | 92,940 transiciones, partición temporal 60/20/20 y contexto de liga sin mezclar IDs de partido |
| 41 | `multileague_state_simulation` | validada para fusión estructural | 40 ligas, 5,000 trayectorias deterministas por liga, seis ventanas y cero targets usados |
| 42 | `multileague_structural_fusion` | evaluada, rechazada para promoción | 3,713 predicciones candidatas, 1,856/1,857 validación-confirmación, Markov conserva la intensidad pero pierde frente al Poisson estructural |
| 43 | `multileague_oos_evaluation` | rechazada para promoción | Confirmación OOS por partido completo: la fusión pierde en los cinco mercados principales; router congelado |
| 44 | `multileague_precision_diagnosis` | validada para corrección | Se elimina ruido Monte Carlo de mercados completos; la señal temporal queda pendiente de recalibración |
| 45 | `temporal_markov_recalibration` | sin valor incremental | Pesos seleccionados en validación; confirmación no supera baseline temporal con IC positivo |
| 46 | `profile_conditioned_markov` | rechazada para promoción | El prior inicial por perfil reciente pierde frente al Poisson estructural en primer y segundo tiempo; router congelado |
| 47 | `reuse_catalog_gate` | validada para control | El gate prospectivo incorpora IDs del corpus multi-liga y bloquea la falsa independencia de 1,801 partidos reutilizados |
| 48 | `universal_prematch_flow` | validada para vertical local | El usuario puede solicitar una predicción por liga, equipos y kickoff; baseline estructural, markets y provenance causales |
| 49 | `fixture_resolver_snapshot_refresh` | lista para operación controlada | Resuelve fixtures ESPN futuros por IDs/nombres y refresca staging de forma explícita; el snapshot canónico aún no se reemplaza automáticamente |
| 50 | `versioned_snapshot_materialization` | validada para operación | Snapshot multi-liga inmutable activo, hash verificado, selección por configuración y rollback disponible |
| 51 | `real_fixture_flow` | validada con advertencia de frescura | Puebla–Guadalajara resuelto desde ESPN y predicho con HTTP 200, sin persistencia ni leakage; el histórico activo requiere actualización |
| 52 | `post2025_snapshot_refresh` | validada para `mex.1` | 168 partidos y 2,016 ventanas post-2025 añadidos; el fixture real ya no tiene advertencia de frescura |
| 53 | `multileague_post2025_refresh` | validada para operación acotada | 42 ligas procesadas, 6 partidos completos, 72 ventanas nuevas, snapshot activado con dry-run previo y rollback |
| 54 | `multileague_extended_refresh` | validada para operación | 4,873 referencias, 293 partidos completos, 3,516 ventanas nuevas y snapshot activo de 117,000 filas en 42 ligas |
| 55 | `universal_named_fixture_flow` | validada para operación | Puebla–Guadalajara resuelto por nombres vía ESPN, HTTP 200, cutoff causal y snapshot versionado verificados |
| 56 | `multileague_upcoming_flow` | validada para operación | 10 fixtures futuros localizados, 9 predicciones HTTP 200 y una exclusión correcta por historia insuficiente |
| 57 | `incremental_snapshot_refresh` | validada para operación | Ventana de siete días, 7 partidos completos, 84 ventanas candidatas, snapshot incremental activado con dry-run previo |
| 58 | `rebaseline_audit` | validada para diseño | Auditoría OOS Dixon-Coles/Kalman, sin activación; Markov residual selectivo definido para nuevo OOS |
| 59 | `event_quality_audit` | taxonomía raw v1.1 validada; gate global pendiente | 0 fallos estructurales; 1,893 eventos raw en 15 partidos, 0 `unclassified`, 15/15 marcadores reconciliados; falta rematerializar snapshot aislado |
| 60 | `taxonomy_snapshot_candidate` | taxonomía validada; pendiente recalibración por faltas recuperadas | 9,751 partidos/117,012 filas comunes; 0 desconocidos, 3,893 diferencias sólo en faltas y 0 estados cambiados |
| 61 | `source_coverage_closure` | validada | 457/457 referencias activas recuperadas, 0 fallos; 401 partidos de 2025 siguen excluidos por marcador no reconciliado |
| 62 | `independent_cohort_lock` | validada | 9 fixtures futuros congelados antes del kickoff, sin resultados ni play-by-play observados |
| 63 | `markov_residual_initial_state` | candidato congelado; no promovido | Clasificador multinomial state_0 y 9 predicciones futuras `first_half_goal` congeladas; `repliegue` no aparece en desarrollo |
| 64 | `selective_oos_promotion` | replay diagnóstico negativo; fusión residual descartada por ahora | Markov `0.796682` vs baseline `0.730142`; validación selecciona `alpha=0.0`; holdout de fusión sin mejora; no promoción |
| 65 | `markov_position_audit` | validada; emisión/estado pendiente | 3,921 partidos walk-forward: Markov `0.627332` vs baseline `0.626786`; transición global/uniforme dominante |
| 66 | `soft_transition_recalibration` | rechazada para promoción; diagnóstico abierto | Pooling suave selecciona `specificity=2`; holdout Markov `0.641220` vs baseline `0.639682`; IC negativo |
| 67 | `state_emission_audit` | validada; desalineación detectada | `state_t→goles_t` difiere de `state_t→goles_t+1` entre `0.0235` y `0.0540` goles por estado |
| 68 | `lagged_emission_candidate` | rechazada para promoción | Emisión desplazada: Markov `0.640806` vs baseline `0.639682`; IC cruza cero |
| 69 | `direct_state_emission_candidate` | rechazada para promoción | `state_0` directo: Markov `0.640280` vs baseline `0.639682`; IC cruza cero |
| 70 | `state_labeling_v2_candidate` | rechazada como reemplazo | Más variables aumentan soporte, pero el spread de riesgo siguiente cae `0.132934→0.085693`; v1 se conserva |
| 71 | `state_semantic_revision` | rechazada para promoción; fallback validado | Cadena conjunta, temporalidad y residual corregidos; cuatro taxonomías eligen `alpha=0`, spread `0.020323` y holdout idéntico al baseline |
| 72 | `markov_causal_contract` | validada | 12/12 recursos ESPN obligatorios, 16 capturas live por ejecución, raw-first/replay auditado y 295 pruebas aprobadas |
| 73 | `prematch_multicutoff_snapshots` | archivada (DEC-170); 60 snapshots en 5 fixtures/ligas al momento de la suspensión | 100% pre-kickoff; recolección prospectiva suspendida, no se sigue acumulando cohorte |
| 74 | `causal_sequence_corpus` | autorizada para implementación | Cero leakage/solapamiento, marcador reconciliado y ≥95% de secuencias completas en ligas admitidas |
| 75 | `directional_targets_strong_baselines` | programada | Targets post-match aislados y comparadores calibrados, incluido modelo tabular same-data |
| 76 | `latent_state_discovery` | ready_for_next_phase | 76R supera NMI, spread, ocupación, estabilidad y duración en dos folds OOS |
| 77 | `prematch_initial_state` | ready_for_next_phase | representación dual supera log-loss, Brier y ECE en dos folds OOS |
| 78 | `context_duration_transitions` | ready_for_next_phase | mejora 1.95%/2.16%, masa contextual >50% y duración <10% |
| 79 | `coherent_prematch_simulation` | ready_for_next_phase | Replay idéntico, conservación `6.661e-16`, probabilidades válidas, core universal y cero lecturas posteriores al cutoff |
| 80 | `nested_walkforward_ablation` | rejected_for_revision | Mejor variante `-0.000002`, IC95% cruza cero y sólo 44.83% de ligas no degradan |
| 80V | `historical_100_match_report` | diagnóstico validado, no promocionable | 100 partidos/13 ligas; 80U `0.954427` vs continuo `0.953792`, reporte causal ordenado y replay idéntico |
| 80W | `complete_system_100_match_test` | diagnóstico validado, no promocionable | Cadena DC/Kalman→Markov en 100 partidos; fiabilidad `54.4%` vs mayoría `56.2%`, replay idéntico |
| 81 | `independent_prospective_confirmation` | archivada (DEC-170), sin cohorte propia | ≥500 partidos, ≥10 ligas, cohorte sellada y confirmación bootstrap positiva |
| 82 | `official_markov_v4_integration` | archivada (DEC-170); bloqueada por Fases 80–81 | Paridad offline/online, provenance completo, rollback y activación sólo de mercados aprobados |
| 83 | `betting_value_validation` | archivada (DEC-170); bloqueada por Fase 82 | Cuotas, ROI, Kelly y drawdown sólo después de aprobar probabilidades |
| 84A | `team_count_markets` | ready_for_next_phase, shadow | 4 líneas aprobadas en 1,895 confirmation/33 ligas; goles y router intactos |
| 84B | `player_market_readiness` | archivada (DEC-170); blocked_by_data | Exige identidad, minutos, titularidad, alineación causal y atribución de eventos; no existe fuente causal disponible |
| 85 | `count_market_shadow_integration` | ready_for_prospective_shadow | 4 líneas integradas, paridad oficial exacta, fallback seguro, replay y 373 pruebas |
| 86 | `count_market_prospective_confirmation` | ready_for_next_phase | 523 predicciones/18 ligas raw-first, modelo+baseline congelados y cero outcomes leídos |
| 87 | `count_market_outcome_materialization` | activa, 0/523 | Colector raw-first validado; espera `kickoff + 3h`, predicciones intactas y scoring bloqueado |
| 88 | `team_market_markov` | parcialmente validada | Tiros comerciales corregidos; 4/12 líneas ganan log-loss y Brier, bloque completo no sustituye baseline |
| 89 | `team_market_markov_integration` | ready_for_next_phase | 4 líneas Markov + 4 agregadas: 8 shadow, fallback 84A, paridad y replay |
| 90 | `markov_market_prospective_cohort` | ready_for_next_phase | 520 predicciones pre-kickoff/18 ligas, 4 mercados, hash comercial v2 y cero outcomes |
| 91 | `markov_market_outcomes` | activa, 0/520 | Settlement por mitad raw-first listo; todavía 0 fixtures elegibles |
| 92 | `markov_market_promotion_gate` | insufficient_coverage | Gate individual con 10,000 bootstraps implementado; scoring sellado |
| 93 | `user_market_contract` | ready_for_next_phase | Vista tipada de 8 mercados, paridad oficial y replay |
| 94 | `historical_500_semiofficial` | validada como evidencia histórica | 500 partidos, 4,000 decisiones y PBP reconciliado; cifras previas reemplazadas por Fase 113 |
| 95 | `market_probability_calibration` | validada, shadow | 395 partidos/3,160 decisiones tras warm-up atómico; log-loss 0.650708 vs 0.662019 |
| 96 | `market_dependency_exposure` | validada, shadow | 500 partidos, 3 pares dependientes, 10 perfectos vs 9.47 esperados y política sin stakes |
| 97 | `telegram_shadow_interface` | ready_for_next_phase | Bot privado por allowlist, seis comandos, ocho mercados y replay E2E |
| 98 | `telegram_data_explorer` | ready_for_next_phase | 18 ligas, navegación visual y goles PBP reconciliados con marcador; 418 pruebas |
| 99 | `discord_shadow_interface` | promising_unconfirmed | Paridad Telegram, 6 comandos sincronizados y 425 pruebas; falta smoke manual de callbacks |
| 100 | `espn_bot_context_enrichment` | programada | Contexto visible, disponibilidad y candidatos causales ESPN; raw-first y sin alterar router |
| 101 | `telegram_channel_broadcast` | validated v1.4 | Servicio propio, full/lite, snapshots append-only y seis mercados distribucionales variables por partido |
| 102 | `distributional_team_market_ladders` | validated v1.1 | 21 PMF, 269 líneas y rejilla visible de tres cortes entre 1.5–9.5 con over/under |
| 103 | `distributional_market_walkforward` | 12 líneas Markov validadas; SOT pendiente | 9,646 partidos, selección 1,892, confirmación 1,895, 10,000 bootstraps y gate por liga |
| 104 | `official_goal_chain` | promoción selectiva revalidada | Dixon-Coles/Kalman corregido en 1X2 y over 2.5; BTTS Fase 106; 45 cold starts excluidos |
| 105 | `historical_1000_complete_model` | diagnóstico revalidado | 1,000 partidos, 11,000 decisiones y Brier normalizado por evento |
| 106 | `probability_repair_selective` | integrada selectivamente | BTTS causal por liga aprobado; fallback exacto de la línea Markov degradada |
| 107 | `railway_user_pilot_readiness` | validada | imagen, auth, volumen, logs, 100 solicitudes concurrentes y Telegram público aprobados |
| 108 | `repository_hygiene` | validada | runtime mínimo, snapshot gzip y exclusión de cachés/evidencia local para GitHub |
| 109 | `premium_telegram_railway` | validada v1.6 | acceso private/public, pre-match y live shadow visibles, worker independiente y contrato móvil medible |
| 113 | `model_integrity_v1` | validada con salidas selectivas; hotfix portable listo | fórmula, causalidad, PMF, métricas, hashes runtime portables, fallbacks, replays y 522 pruebas aprobadas |
| 114 | `live_markov_hawkes_v1` | validada históricamente e integrada en producto shadow | 7,400 partidos/34 ligas; API+Telegram muestran Markov, Hawkes residual y combinado; 17 ligas admitidas y fallback exacto |
| 115 | `telegram_mini_app` | paridad visual y analítica desplegada; DEC-154 en producción | Catálogo de 49 ligas/torneos, detalle live automático, predictor externo tolerante a ausencia, cinta open/close/live aislada, curva de presión, logos, acciones y Markov/Hawkes/combinado separados |
| 116 | `live_probability_engine_v1` | desplegado y oficial en producción | Poisson dinámico + CTMC + Hazard/Cox + Elo live + residual Hawkes; 7,400 partidos/34 ligas, gates causales completos, MC diagnóstico asincrónico y fallback Markov; confirmado en vivo vía `/v1/health` el 2026-08-12 |
| 116H | `defensive_save_gate` | **activada en producción** | Gate del `save` como señal defensiva sobre 7,400 partidos/34 ligas: las cuatro medidas positivas, **ninguna degrada**, y mejora confirmada del objetivo compuesto en validation (`+0.000244`, IC95% `[+0.000018, +0.000454]`). `enable_defensive_save_signal` pasa a `True`; `save` entra como tipo propio con peso negativo, nunca proyectado |
| 116G | `save_incremental_information` | validada | Responde si `save` aporta información que el motor no tenga: regresión Poisson fuera de muestra, 69,498 observaciones de 9,405 partidos, delta de deviance `+0.000987` IC95% `[+0.000679, +0.001278]`. Coeficiente **negativo** (`-0.02034`) frente a los positivos del resto: quien acumula paradas defiende. Señal de presión recibida que el motor no tenía |
| 116F | `save_semantics_audit` | **DEC-217 rechazada** | Con autorización para escribir en producción se descubrió que ni migración ni backfill hacían falta: los `save` ya están en `prospective_staging_v2.events` (81,872 en 6,434 partidos) y sólo los excluía el `WHERE` de `_event_query()`. Con los datos reales el candidato resulta **incorrecto**, no sólo inmedible: `save` no es una parada de portero -12.72/partido contra 5.31 `shot_on_target`, 7.06 jugadores distintos por partido, textos con centrales y delanteros, 53.3% coincidentes con `shot_blocked` ya modelado-. Proyectarlo daría 12.37 tiros a puerta/partido contra un rango realista 7-11. Código retirado; `markov_live_v1.py` vuelve byte-idéntico |
| 116E | `save_projection_e2e` | veredicto invalidado por 116F | Recorre la cadena real -play crudo ESPN → `classify_play` → payload del follower → `MarkovLiveV1`- sobre 15 partidos: los 110 `save` sobreviven conservando `event_type_raw`, 76 proyectados / 33 deduplicados con **cero errores de atribución**, y los tiros a puerta pasan de `2.93` a `8.00` por partido, dentro del rango realista. El mecanismo deja de ser parte del riesgo; el bloqueo es exclusivamente de datos |
| 116D | `goal_rate_calibration` | ejecutada, cierra DEC-216 | Recalibra contra la cantidad que `_score_factors` realmente modula -intensidad de gol, no presión- y controla la confusión por fuerza que invierte el signo del ratio crudo (`0.913` → `1.097`). Resultado: la forma lineal vigente **cae dentro del IC95% en las cinco ventanas** y gana en `selection` (error `1.14` vs `2.67`); la rampa ajustada degenera a escalón. La hipótesis de DEC-216 era un artefacto de dos errores compuestos; la configuración servida es la correcta |
| 116C | `score_pressure_gate` | ejecutada, activación rechazada | Gate histórico de `ramp_v2` contra `linear_v1` sobre 7,400 partidos/34 ligas en PostgreSQL de producción, modo lectura verificado (DEC-216). Todos los gates técnicos de DEC-155 pasan, pero 1X2 log-loss degrada de forma confirmada en validation (`-0.000943`, IC95% `[-0.001860,-0.000046]`) y es indistinguible en confirmation. Causa: `_score_factors` modula intensidad de gol, no presión, y las demás capas se calibraron con la forma lineal. La capacidad queda desplegada y desactivada; no reutilizar los splits para más tuning |
| 116B | `save_attribution_audit` | ejecutada, activación bloqueada | Corrige la premisa falsa de DEC-217 y reenfoca a `save`: ESPN emite 7.3/partido contra 2.9 de `shot_on_target`, con atribución al portero (exige invertir equipo) y 43% de duplicación (ventana ±5s por meseta). Bloqueo real: la base histórica no contiene ningún `save` -el CHECK los filtró en la ingesta-, así que el candidato es inmedible, y los pesos del motor se calibraron sin ellos. Precondición: persistir auxiliares, recalibrar, y sólo entonces gatear |
| 116A | `score_pressure_calibration` | validada, no promocionada | Ajusta la forma temporal sobre 68,148 filas `fit` / 22,692 `selection` sin leer `confirmation` (falla cerrado). Ratios `1.029/1.042/1.116/1.092/1.232`; rampa con `curvature=1.986`, error 25x mejor que la lineal en selection; chequeo de confusión por liga aprobado. Alimenta el gate 116C, que la rechaza |
| 136 | `parlay_prospective_store` | implementada, sin desplegar, recolectando | Store propio del Constructor de Parlays (DEC-223), separado del de Fase 123 porque **un parlay sólo se valida como conjunto**: al multiplicar, el error de calibración se compone en vez de sumarse, así que el ratio de entrega no se deriva de la calibración de cada pierna. Cuatro tablas (`parlay_leg_freezes`, `parlay_leg_settlements`, `parlay_freezes`, `parlay_settlements`, migración `016`) y un runner que congela piernas elegibles antes del kickoff, materializa combinaciones de referencia deterministas **también antes** -si se eligieran después el ratio no mediría nada- y liquida contra `prediction_settlements`. Un parlay queda pendiente mientras le falte una pierna: nunca se resuelve por mayoría. `prospective_delivery` oculta el ratio bajo 30 parlays. 18 pruebas. Ninguna ruta servida lee estas tablas |
| 135 | `parlay_eligibility_gate` | sellada, shadow, pendiente de confirmación prospectiva | Congela el gate que decide qué mercado puede ser pierna del Constructor de Parlays, derivado de Fase 134. Tres filtros en orden -ventaja con IC95% sobre cero, calibración con brecha ≤`0.02` y `n≥40` en el tramo de uso, estabilidad ≥`70%` de ligas con rango ≤`20pp`- más dos reglas estructurales: `2–5` piernas y **una por partido**, porque la correlación intra-partido es real y no está modelada (DEC-203). Sobreviven **2 de 11** mercados: `away_shots_over_10_5` y `home_corners_over_4_5`, ambos con umbral `0.60`. El 1X2 queda fuera pese a ventaja confirmada `+4.8pp` porque su confianza no es cierta; `btts` declara `0.88` y entrega `0.51`. Entrega fuera de muestra `0.94–0.97` frente a `0.77–0.87` de la regla ingenua. `src/parlay_eligibility_v1.py` aplica el gate con verificación de hash y degradación fail-closed; 18 pruebas. Ver `artifacts/phase_135_parlay_eligibility/` y DEC-222 |
| 134 | `recalibrated_1000_backtest` | ejecutada, diagnóstica, sin promoción | Reevalúa 1,000 partidos de `confirmation` (21 ligas, 2025-11-16 → 2026-07-26, 11,000 decisiones) con la cadena ya recalibrada por DEC-200/DEC-201, que Fase 105 no pudo medir por ser del 2026-08-07 -nueve días anterior al commit que introdujo la calibración- y por no aplicar nunca la temperatura al 1X2. Aislado sobre la misma matriz, el escalado de temperatura mejora log-loss de 1X2 `+0.007433` IC95% `[+0.001878, +0.013063]` y Brier normalizado `+0.001672` IC95% `[+0.000055, +0.003271]`, ambos confirmados; el acierto es idéntico (`50.5%`) porque `x^(1/T)` preserva el argmax en los 1,000. La sobreconfianza media cae de `+0.0341` a `+0.0032`, reproduciendo de forma independiente el `+0.0329` de DEC-201 sobre otra ventana. No es promoción: la ventana se solapa con el `confirmation` que DEC-201 ya usó. Ver `artifacts/phase_134_recalibrated_1000/` |
| 133 | `public_web_app` | implementada, cobro web apagado | La Mini App pasa a servirse también desde un dominio propio **sin bifurcar el código**: un contexto de ejecución (`telegram` | `web`) condiciona los cuatro únicos puntos atados a la plataforma -autenticación, cobro, `BackButton`/tema y compartir- y todo lo demás se sirve igual. El acceso web usa el Telegram Login Widget, que devuelve el **mismo `telegram_user_id`**: cero migración de datos y misma cuenta, plan e historial (DEC-219). Cobro web con Stripe tras `MINIAPP_STRIPE_ENABLED`, con webhook idempotente por `stripe_events` y exclusión mutua frente a una suscripción Stars viva (DEC-220). 26 archivos Vitest/244 pruebas y proyecto Playwright `web` de 5 pruebas; los 6 fallos E2E que quedan son previos a esta fase y están documentados |
| 132 | `confounding_check_v1` | implementada | `src/confounding_check_v1.py` convierte el guardarraíl de DEC-218 en código: bootstrap por grupo, leave-one-group-out, control estratificado por fuerza y fragilidad del intervalo, con veredictos `confundido`/`frágil` añadidos al vocabulario. Reproduce el caso real de formaciones (`+2.4286`, IC95% que no cruza cero, `influence_ratio 0.506`) y lo etiqueta `confundido` automáticamente. Consumido ya por Fase 116A |
| 131 | `fault_conditioned_corner_candidate` | evaluado, no promocionado | Faltas propias esperadas como covariable nueva del target `corners` (FULL_MATCH) de Fase 84A (DEC-213); el conteo bruto mejora marginalmente (deviance 3.0291→3.0231, MAE 3.1082→3.0868, estabilidad por liga 72%→76%) pero no se traduce en mejor probabilidad de línea: `home_corners_over_4_5` indistinguible y `away_corners_over_4_5` degrada de forma confirmada (IC95% `[-0.002943, -0.000046]`); `_gate()` no pasa, `team_count_market_runtime.py`/`APPROVED_MARKETS` sin tocar |
| 130 | `live_engine_venue_asymmetry_term` | cerrada sin evidencia motivadora | Diseño condicional a que Fase 129 confirmara asimetría real de localía en la reacción in-play del motor; Fase 129 no la confirmó, así que esta fase se cierra sin diseñarse ni requerir acceso a Postgres (DEC-214) |
| 129 | `favorite_venue_inplay_swing_analysis` | validada, negativa | El swing empírico real de "quién anota primero" y "estado al descanso" es igual de grande para favorito local y favorito visitante -IC95% de la asimetría cruza cero en ambos casos (DEC-214)-; la simetría de `LiveProbabilityEngineV1` no es un defecto |
| 127-128 | `favorite_venue_temperature_and_blend_candidates` | evaluados, no promocionados | Temperatura y peso de mezcla de 1X2 segmentados por localía del favorito, con contracción jerárquica hacia los valores globales adoptados (DEC-200/201); ambos candidatos quedan indistinguibles de la composición ya servida (DEC-212); la fragilidad del favorito visitante medida en DEC-211 no vive en la capa de recalibración |
| 126 | `pick_builder_v1` | implementada, sin liquidación | Menú `/constructor` que combina mercados ya publicados -de uno o de varios partidos- en una sola probabilidad conjunta (DEC-208): exacta sobre la matriz de marcadores para los mercados de gol, exacta sobre la propia escalera para dos líneas de la misma variable, y producto declarado entre variables y entre partidos; una selección única reproduce la probabilidad publicada, verificado con 42 pruebas nuevas contra valores de referencia calculados aparte; no congela ni liquida picks, así que no entra en el historial de aciertos |
| 125 | `star_subscription_tiers` | implementada, **cobro ENCENDIDO en producción** (confirmado por el operador el 2026-08-20; el estado anterior de este documento, "cobro apagado", estaba obsoleto) | Niveles Free/Premium con suscripción mensual Telegram Stars a 250 ⭐ (~4.90 USD); equilibrio en 15 suscriptores contra un piso medido de ~46 USD/mes (Railway ~26 + Claude Pro 20), del que el 87% del gasto variable es PreMatch y escala con el catálogo, no con los usuarios; `plan` deja de ser un no-op y la titularidad se lee por petición con caché de 60 s en vez de confiar en la cookie de 30 días; cuota gratuita de 3 predicciones diarias por partido -no por petición- atómica en una sentencia y compartida entre bot, Mini App y tarjeta; caducidad calculada al leer, sin barrido; pagos escritos por un único proceso vía endpoint interno y reconciliados contra `getStarTransactions`; tres bloqueadores silenciosos corregidos (`allowed_updates` sin `pre_checkout_query`, la guarda de `text` que descartaba los cobros, y `/api/share` prediciendo sin medir); 133 Vitest/1 omitida, 21 pytest nuevas; nada gatea hasta `MINIAPP_BILLING_ENABLED=true` |
| 123 | `high_probability_prospective_confirmation` | implementada, pendiente de primer despliegue | `src/high_probability_settlement.py` congela picks de `/v1/high-probability` con hash antes del kickoff y liquida contra `prediction_settlements` ya sellado; mercados de equipo liquidados por línea fija vía `explorer_statistics`, no por la rejilla dinámica de Fase 102; 12 pruebas nuevas, 702 Python/8 omitidas sin regresiones; cero cohorte real todavía |
| 122 | `high_probability_menu_v1` | implementada, evidencia histórica, shadow | Fiabilidad condicional al nivel de confianza sobre 1,270 partidos/12 mercados/15,240 decisiones; el gate congelado rechazó las 21 celdas y se reporta como resultado primario; un gate v2 post-hoc aprueba 10 y 9 sobreviven el holdout de 270 partidos nunca publicados; sólo 3 son `model_edge`; 1X2, Más de 2.5 y Ambos marcan no clasifican en ningún tramo; `GET /v1/high-probability` y `/mayor-probabilidad` publican la tasa observada, no la del modelo, con fail-open a menú vacío; 662 Python/25 nuevas, 21 Vitest, 23 Playwright/6 nuevas |
| 121 | `daily_track_record_v1` | implementada | `SettlementRepository.on_date` agrega por día calendario local sin filtrar por acierto; `GET /v1/track-record/daily?date=YYYYMMDD` obligatorio; el avisador publica una vez al día el resumen íntegro del día anterior (✅/❌ por partido y mercado); "Resultados de hoy" en la Mini App sobre el historial acumulado; 637 Python/13 nuevas, 21 Vitest, 17 Playwright/1 nueva |
| 120 | `league_catalog_expansion_v1` | implementada y activada | Catálogo de 49 a 63 slugs con los 14 verificados contra ESPN; causa raíz del reporte aislada en los slugs propios de clasificación UEFA y en `concacaf.leagues.cup`; 13,086 referencias/56 ligas sin perder ninguna previa, 2,654 partidos completos ingeridos y snapshot `phase160_recent_topup_v1_20260811` con 12,281 partidos; corregidos el descubrimiento destructivo y la lectura gzip del snapshot activo; allowlist Hawkes intacta en 17 con fallback Markov exacto |
| 119 | `bias_backtest_500_v1` | implementada, cero mercados promovidos | Diagnóstico real sobre 500 partidos: 4 mercados con sesgo (ECE 0.09-0.18), shrinkage bayesiano mejora ECE en los 4 pero ninguno alcanza estabilidad ≥70% por liga; mecanismo fail-open conectado y probado, sin calibradores publicados; reporte visual en `artifacts/phase_119_bias_backtest_500/dashboard.html` |
| 118 | `verifiable_track_record` | implementada, pendiente de primeras liquidaciones | Historial de aciertos verificable en Postgres, agregado por `GET /v1/track-record`, vista `/historial` y resumen semanal en canal; umbral de muestra, intervalo de confianza y baseline obligatorios |
| 117 | `live_team_markets_v1` | implementada, shadow sin gate histórico | Corners y tiros restantes adaptativos por equipo más próximo gol; `_dynamic_poisson` extendido con territorio escalado por fuerza causal, tasas base calibradas sobre 9,465 partidos de Fase 74, checks integrados al gate oficial y fallback exacto; 578 Python, 21 Vitest y 14 Playwright aprobados |

## Componentes heredados

| Componente | Estado | Uso bajo el roadmap vigente |
| --- | --- | --- |
| `match_features v1` | estable | Base estructural pre-match; no se modifica semánticamente |
| Dixon-Coles | disponible | Prior de goles y capacidad estructural |
| Kalman | disponible | Estado temporal previo al kickoff |
| `markov_v1` live | legado experimental | Mantener sólo por compatibilidad; sustituir gradualmente |
| `markov_live_v1` | validado históricamente, shadow | Baseline causal in-play; gate 7,400 partidos/34 ligas aprobado |
| Hawkes v1 | legado shadow | No ampliar; reemplazado para la investigación live |
| `hawkes_live_v2` | residual selectivo validado, shadow | `rho_goal=1` sólo en 17 ligas admitidas; próximo evento y demás ligas usan fallback Markov exacto |

## Candidatos futuros bloqueados por datos

- **BTTS / calidad de tiro (xG).** La investigación de fallos de predicción
  sobre 1,000 partidos (`DEC-211`) encontró que el 86.5% de los fallos de
  "ambos marcan" ocurren con el equipo que no anotó generando al menos un
  tiro a puerta -no por ausencia total de juego ofensivo-, lo que sugiere que
  falta una señal de calidad de tiro (xG) para distinguir "sin ocasiones" de
  "ocasiones sin convertir". El corpus de Fase 74/ESPN no contiene ese campo
  hoy. Mismo criterio que Fase 84B (`blocked_by_data`): no se mide hasta que
  exista una ingesta de xG con snapshot pre-cutoff, clasificada primero según
  `references/espn-bot-data-enrichment.md`.

- **Tipos de evento auxiliares de ESPN (duelo/intercepción/desposesión) como
  señal `live-only`.** El análisis de hudl/open-data (`DEC-217`) encontró que
  un duelo ganado o una recuperación de balón preceden un tiro con 4.3-4.5x
  más frecuencia de lo esperado, con una mediana de 1 segundo entre
  recuperar el balón y rematar. `src/espn_event_taxonomy.py` ya clasifica
  estos tipos en el feed crudo como `auxiliary` (`tackle`, `interception`,
  `dispossessed`, `aerial`, `take_on`, entre otros), pero la tabla de
  producción `events_timeline` sólo persiste 8 tipos por restricción `CHECK`
  -los auxiliares nunca llegan a guardarse-. Candidato futuro concreto para
  enriquecer la capa de hazard/CTMC del motor en vivo (hoy sólo pesa `foul`,
  `EVENT_WEIGHTS["foul"]=0.08`), estrictamente `live-only`, nunca feature
  pre-match. Bloqueado hasta confirmar con una muestra real que ESPN entrega
  estos tipos con timestamp por evento en la práctica, y clasificar el campo
  formalmente por `references/espn-bot-data-enrichment.md` antes de escribir
  código o ampliar el esquema.

- **Guardarraíl para Fase 84B: confusión de fuerza en features de
  alineación.** El análisis de alineaciones de hudl (`DEC-218`) mostró que
  "más defensores que el rival predice ganar por más goles" parecía real
  (IC95% no cruzaba cero) pero era un artefacto -los equipos más débiles del
  torneo eligen líneas de 3 defensores contra favoritos, y por eso pierden
  por más, no al revés-. Cuando Fase 84B se desbloquee, cualquier feature de
  formación/alineación debe pasar el mismo chequeo de confusión por fuerza
  relativa esperada antes de tratarse como señal causal.

## Reglas no negociables

- No usar eventos, estadísticas ni marcador del partido objetivo como features pre-match.
- La unidad de inferencia y bootstrap es el partido completo; las ventanas no son IID.
- No avanzar de fase sin los artefactos y gates definidos en `docs/phases/`.
- Toda decisión arquitectónica debe quedar en `docs/decision_log.md`.
- `docs/status.md` es el estado operativo de una sola mirada.

## Próximo hito

Desplegar la Fase 125 por pasos con el cobro apagado, en el orden del runbook `docs/runbooks/telegram_stars_subscriptions.md`: primero las migraciones, después los dos secretos compartidos -sin ellos el servicio no arranca, así que deben preceder al código que los usa-, luego el código con `MINIAPP_BILLING_ENABLED=false`, y sólo al final el interruptor. Antes de encenderlo hacen falta cuatro evidencias que no se pueden obtener sin desplegar: reaplicar migraciones sin efecto, una compra real de 250 ⭐, un reembolso real, y una reparación del reconciliador provocada deteniendo la Mini App durante una compra. Avisar del vencimiento del acceso heredado (2026-11-01) con al menos 14 días de antelación. Stars no se activa por proveedor en BotFather -a diferencia de Stripe, no hay paso de habilitación conocido-; la compra real del paso anterior es la que confirma si `subscription_period` funciona sin fricción, ver la corrección en `docs/runbooks/telegram_stars_subscriptions.md`.

Desplegar Fase 120 en Railway y confirmar con un smoke real que la Mini App
lista los 63 slugs y muestra los partidos de clasificación UEFA y Leagues Cup
que motivaron la ampliación. Después, completar el smoke interactivo desde un
usuario Telegram autorizado. Registrar
el short name de la Mini App en BotFather y validar una regla de alerta con
dedupe real. Sólo después activar `MINIAPP_ALERTS_ENABLED=true`. PostgreSQL,
Mini App con paridad completa y worker están desplegados; el menú global del
bot está activo y el worker sigue deshabilitado lógicamente. En paralelo,
vigilar drift de la allowlist Hawkes sin reabrir confirmación. No se exige
esperar una cohorte de 500 partidos. Markov, Hawkes y combinado permanecen en
`shadow` hasta una decisión explícita de promoción.

Para Fase 122, el siguiente paso recomendado es una validación prospectiva:
congelar antes del kickoff los picks que el menú publica y liquidarlos después,
para contrastar la tasa observada histórica de cada tramo contra su desempeño
real. Es lo único que convierte el gate v2 post-hoc en evidencia independiente;
mientras tanto el menú comunica evidencia histórica y nada más.

La fase no autoriza staking real. ROI, CLV y Kelly continúan bloqueados hasta
contar con cuotas históricas comparables.
