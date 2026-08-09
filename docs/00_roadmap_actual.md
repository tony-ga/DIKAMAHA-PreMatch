# Roadmap vigente de DIKAMAHA

**Actualizado:** 2026-08-08
**Objetivo operativo:** preservar la integridad matemática y causal de Fase
113 mientras Fase 115 presenta Fase 114 mediante una Mini App Telegram
híbrida, sin ampliar mercados ni afirmar ventaja económica.

Fase 113 reemplaza las cifras de Fases 84A, 88, 94, 95, 96, 103, 104, 105 y
106 cuando dependían de recorridos intra-kickoff, fronteras fraccionales o
métricas mixtas. La salida vigente conserva 1X2 y over 2.5 en la cadena
oficial, BTTS reparado por Fase 106 y ocho mercados de equipo en shadow.
La revisión operativa de artefactos queda validada para despliegue: la imagen
Linux verifica sólo los componentes que consume y acepta la representación
LF/CRLF sin relajar contenido, esquema ni hashes binarios.

## Objetivo congelado

Generar predicciones pre-match mediante simulación de trayectorias históricas.
El núcleo nuevo será `markov_pre_match v4`: un modelo latente, direccional y
con duración, aprendido sobre microventanas causales y agregado a mercados de
15 minutos. Dixon-Coles y Kalman aportan capacidad estructural y forma reciente.

## Arquitectura objetivo

`historial causal + contexto snapshot -> Dixon-Coles/Kalman -> estados latentes y duración -> simulación Markov v4 -> mercados temporales pre-match`

Hawkes queda fuera del núcleo pre-match. En live sólo puede operar como
residual `shadow` de Markov Live hasta demostrar valor incremental fuera de
muestra.

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
| 73 | `prematch_multicutoff_snapshots` | activa; 60 snapshots en 5 fixtures/ligas | 100% pre-kickoff; falta segundo bucket por fixture para cerrar cobertura |
| 74 | `causal_sequence_corpus` | autorizada para implementación | Cero leakage/solapamiento, marcador reconciliado y ≥95% de secuencias completas en ligas admitidas |
| 75 | `directional_targets_strong_baselines` | programada | Targets post-match aislados y comparadores calibrados, incluido modelo tabular same-data |
| 76 | `latent_state_discovery` | ready_for_next_phase | 76R supera NMI, spread, ocupación, estabilidad y duración en dos folds OOS |
| 77 | `prematch_initial_state` | ready_for_next_phase | representación dual supera log-loss, Brier y ECE en dos folds OOS |
| 78 | `context_duration_transitions` | ready_for_next_phase | mejora 1.95%/2.16%, masa contextual >50% y duración <10% |
| 79 | `coherent_prematch_simulation` | ready_for_next_phase | Replay idéntico, conservación `6.661e-16`, probabilidades válidas, core universal y cero lecturas posteriores al cutoff |
| 80 | `nested_walkforward_ablation` | rejected_for_revision | Mejor variante `-0.000002`, IC95% cruza cero y sólo 44.83% de ligas no degradan |
| 80V | `historical_100_match_report` | diagnóstico validado, no promocionable | 100 partidos/13 ligas; 80U `0.954427` vs continuo `0.953792`, reporte causal ordenado y replay idéntico |
| 80W | `complete_system_100_match_test` | diagnóstico validado, no promocionable | Cadena DC/Kalman→Markov en 100 partidos; fiabilidad `54.4%` vs mayoría `56.2%`, replay idéntico |
| 81 | `independent_prospective_confirmation` | programada | ≥500 partidos, ≥10 ligas, cohorte sellada y confirmación bootstrap positiva |
| 82 | `official_markov_v4_integration` | bloqueada por Fases 80–81 | Paridad offline/online, provenance completo, rollback y activación sólo de mercados aprobados |
| 83 | `betting_value_validation` | bloqueada por Fase 82 | Cuotas, ROI, Kelly y drawdown sólo después de aprobar probabilidades |
| 84A | `team_count_markets` | ready_for_next_phase, shadow | 4 líneas aprobadas en 1,895 confirmation/33 ligas; goles y router intactos |
| 84B | `player_market_readiness` | blocked_by_data | Exige identidad, minutos, titularidad, alineación causal y atribución de eventos |
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
| 115 | `telegram_mini_app` | paridad visual desplegada; acceso privado | Catálogo 18 ligas/14 días, búsqueda global, predicciones, live observable y medios PNG vía BFF; smoke Railway y 542 Python + 16 Vitest + 7 Playwright |

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

## Reglas no negociables

- No usar eventos, estadísticas ni marcador del partido objetivo como features pre-match.
- La unidad de inferencia y bootstrap es el partido completo; las ventanas no son IID.
- No avanzar de fase sin los artefactos y gates definidos en `docs/phases/`.
- Toda decisión arquitectónica debe quedar en `docs/decision_log.md`.
- `docs/status.md` es el estado operativo de una sola mirada.

## Próximo hito

Completar el smoke interactivo desde un usuario Telegram autorizado. Registrar
el short name de la Mini App en BotFather y validar una regla de alerta con
dedupe real. Sólo después activar `MINIAPP_ALERTS_ENABLED=true`. PostgreSQL,
Mini App con paridad completa y worker están desplegados; el menú global del
bot está activo y el worker sigue deshabilitado lógicamente. En paralelo,
vigilar drift de la allowlist Hawkes sin reabrir confirmación. No se exige
esperar una cohorte de 500 partidos. Markov, Hawkes y combinado permanecen en
`shadow` hasta una decisión explícita de promoción.

La fase no autoriza staking real. ROI, CLV y Kelly continúan bloqueados hasta
contar con cuotas históricas comparables.
