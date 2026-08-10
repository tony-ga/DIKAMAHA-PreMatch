# Registro de decisiones DIKAMAHA

| ID | Fecha | Decisión | Estado | Consecuencia |
| --- | --- | --- | --- | --- |
| DEC-001 | 2026-07-26 | El producto objetivo es pre-match. | Congelada | No usar eventos del partido objetivo en inferencia. |
| DEC-002 | 2026-07-26 | Markov será dependiente de contexto y entrenado con secuencias históricas de ventanas de 15 minutos. | Congelada | Reemplaza la dirección centrada en Markov live. |
| DEC-003 | 2026-07-26 | `match_features v1` permanece estable como capa estructural. | Congelada | Markov entra mediante contratos nuevos, no por una alteración silenciosa de v1. |
| DEC-004 | 2026-07-26 | Dixon-Coles y Kalman serán priors/covariables de Markov. | Congelada | No se retiran del sistema. |
| DEC-005 | 2026-07-26 | Hawkes no entra en el núcleo inicial. | Congelada | Sólo se evalúa como señal histórica incremental posterior. |
| DEC-006 | 2026-07-26 | La promoción exige evidencia temporal fuera de muestra por partido completo. | Congelada | No basta una mejora en desarrollo ni en snapshots. |
| DEC-007 | 2026-07-26 | `state_labeling v1` inicia con equilibrio, presión, repliegue y desorganización; agresividad y defensa quedan como variables explicativas. | Congelada | Fase 02 aprobó sensibilidad; `repliegue` requiere pooling por su baja frecuencia. |
| DEC-008 | 2026-07-26 | La calibración Markov inicial condiciona por estado, ventana, marcador, localía, rival y equipo; la fuerza Dixon-Coles/Kalman se integra cuando exista un estado canónico por partido. | Congelada | Evita inventar covariables o usar datos no trazables; Fase 03 validó las matrices sin habilitar promoción. |
| DEC-009 | 2026-07-26 | La Fase 04 podrá simular trayectorias y goles con emisiones históricas condicionadas por estado, mientras el contrato reserva un proveedor de intensidades pre-kickoff Dixon-Coles/Kalman. | Congelada | Evita fabricar lambdas por partido; Fase 04 dejó las probabilidades de mercado como experimentales hasta la evaluación de Fase 05. |
| DEC-010 | 2026-07-26 | La promoción se bloquea si los comparadores obligatorios no aportan predicciones OOS compatibles por partido, aunque algún artefacto heredado contenga métricas parciales. | Congelada | Fase 05 confirmó el caso: no se convierten dry-runs heterogéneos ni una fixture de referencia en evidencia confirmatoria. |
| DEC-011 | 2026-07-26 | La suite canónica OOS reutilizará los cuatro folds temporales comunes ya auditados y generará Markov global/dependiente sólo con el conjunto de entrenamiento de cada fold. | Congelada | Alineó cinco comparadores en 264 partidos, sin modificar estimadores ni usar eventos del partido validado. |
| DEC-012 | 2026-07-26 | Las probabilidades 1X2 heredadas que no sumen uno se renormalizarán de forma explícita en el contrato canónico, preservando sus proporciones y registrando el ajuste. | Congelada | Se aplicó a 264 predicciones heredadas preservando proporciones; habilita métricas probabilísticas válidas. |
| DEC-013 | 2026-07-26 | Markov dependiente no se promociona en v1. | Congelada | En confirmación no superó al baseline simple y sus intervalos bootstrap no sostienen una mejora frente al baseline ni frente a Markov global. |
| DEC-014 | 2026-07-26 | Markov v2 usará una intensidad estructural pre-match por equipo, con Dixon-Coles como base y corrección Kalman fijada antes de evaluación; Markov sólo redistribuirá esa intensidad entre ventanas. | Congelada | Sustituye las emisiones históricas genéricas que causaron el rechazo de v1; la simulación v2 conserva masa, pero aún no está evaluada ni promovida. |
| DEC-015 | 2026-07-26 | Markov v2 no se promociona como predictor de 1X2. | Congelada | Supera al baseline y a Markov v1, pero no mejora Dixon-Coles; el bootstrap frente a Dixon-Coles cruza cero. |
| DEC-016 | 2026-07-26 | Los mercados temporales y de remontada Markov no se promocionan todavía. | Congelada | En confirmación ninguna mejora frente al baseline temporal tuvo intervalo bootstrap estrictamente positivo. |
| DEC-017 | 2026-07-26 | Las remontadas completas no son target de promoción con la muestra actual. | Congelada | Sólo hay 11 remontadas locales y 15 visitantes en 381 partidos; se requieren más temporadas o una definición temporal con mayor cobertura. |
| DEC-018 | 2026-07-26 | La extensión histórica se auditará con los 44 partidos completos de `prospective_staging_v2`, sin alterar `event_windows v1` ni los folds OOS congelados; `temporal_targets v2` añadirá recuperación a empate o victoria y conservará la remontada estricta como diagnóstico. | Congelada para investigación | La cohorte candidata pasa a 425 partidos; ningún target v2 se promociona hasta contar con partición temporal y evaluación confirmatoria independientes. |
| DEC-019 | 2026-07-26 | La evaluación temporal v2 queda como `promising_unconfirmed`: sólo `first_half_goal` mejora al baseline con bootstrap positivo; no se promueve ningún mercado. | Congelada | `second_half_goal` no confirma mejora y los targets de recuperación/remontada carecen de soporte suficiente o tienen intervalos que cruzan cero; se necesita una cohorte confirmatoria mayor. |
| DEC-020 | 2026-07-26 | Se acepta una extensión local de ESPN de 241 partidos completos para confirmación, sin incorporarla al histórico canónico ni modificar PostgreSQL. | Congelada para investigación | Fases 11 y 12 auditaron 48,061 eventos, 2,892 ventanas, cero discrepancias de marcador y cero solapamientos. |
| DEC-021 | 2026-07-26 | Markov v2 no se promociona tras la confirmación ampliada de 241 partidos. | Congelada | Primera mitad: IC de mejora `[-0.077553, -0.003782]`; segunda mitad: `[-0.110793, -0.029344]`; ningún target alcanzó mejora bootstrap estrictamente positiva con soporte suficiente. |
| DEC-022 | 2026-07-26 | Los priors de Markov pasan a ser rolling, venue-aware y con shrinkage; las extensiones normalizan IDs ESPN al catálogo interno antes de entrenar transiciones. | Congelada para investigación | Corrige dos pérdidas de información estructurales: intensidad congelada de otra temporada e identidad de equipo inconsistente. Fase 20 usó 900 partidos previos. |
| DEC-023 | 2026-07-26 | Se adopta un selector conservador por target: Markov sólo se activa si supera un umbral mínimo de mejora en calibración; los demás targets usan baseline. | Congelada | Fase 21 activa Markov únicamente para `first_half_goal`; evita que segunda mitad y reacciones degraden el sistema. Ningún mercado se promociona. |
| DEC-024 | 2026-07-26 | Se evaluará una señal auxiliar pre-match específica para `first_half_goal`, construida con el ritmo histórico de eventos de primera mitad; no modifica `match_features v1` ni activa mercados por sí sola. | Congelada para investigación | Fase 22 entrenará un modelo logístico con features causales de los últimos partidos, con calibración en la cohorte de 44 partidos y confirmación independiente en 241. Sólo una mejora bootstrap confirmatoria podrá abrir una revisión del router. |
| DEC-025 | 2026-07-26 | Los reentrenamientos deben deduplicar partidos por kickoff y equipos normalizados antes de construir historia; si una copia canónica coincide con una cohorte posterior, se conserva la cohorte posterior y se excluye la copia. | Congelada | Fase 22 encontró una duplicación exacta del 26/10/2025; Fase 20 fue regenerada con 855 partidos base y 899 de train final. |
| DEC-026 | 2026-07-26 | La nueva señal de contexto externo sólo podrá usar titulares/formación y cuotas de apertura (`open`) recuperadas de ESPN; se excluyen estadísticas del partido objetivo, cuotas `current`/`close` y proveedores live. | Congelada para investigación | Fase 23 auditará cobertura sobre las 1,140 filas limpias, conservará los payloads crudos en caché y exigirá identidad de evento, kickoff, proveedor y ausencia de campos post-match antes de modelar. |
| DEC-027 | 2026-07-26 | La señal de alineaciones no entra al router: la confirmación no aporta mejora estricta y la fusión con ritmo degrada el baseline. | Congelada | Fase 24 queda rechazada para revisión; no se crearán variantes adicionales con los mismos campos sin una nueva fuente o hipótesis. |
| DEC-028 | 2026-07-26 | Se congela un catálogo shadow separado del router oficial; todo candidato experimental requiere activación explícita y permanece desactivado por defecto. | Congelada | Fase 25 preserva el router de Fase 21 y bloquea activaciones accidentales de ritmo, alineaciones, fusión o cuotas. |
| DEC-029 | 2026-07-26 | El servicio pre-match adjuntará un bloque de observación del catálogo shadow en sólo lectura; no expondrá outputs experimentales ni aceptará activación por request. | Congelada | Fase 26 valida que los valores oficiales permanecen idénticos y que ritmo, alineaciones, fusión y cuotas siguen fuera de la salida oficial. |
| DEC-030 | 2026-07-26 | Se autoriza una captura prospectiva controlada de la cohorte disponible posterior al cutoff, exclusivamente mediante SELECT, sin evaluación, reentrenamiento, bootstrap, promoción ni escritura PostgreSQL. | Congelada para observación | La captura podrá dejar la cohorte `in_progress` o `ready_for_evaluation` sólo por cobertura e integridad; ninguna salida experimental se vuelve oficial. |
| DEC-031 | 2026-07-26 | La cohorte de Fase 28 no se acepta como confirmación independiente del router: sus 42 partidos aparecen en la calibración de Fase 20. | Congelada | No se calculan métricas sobre esa cohorte; la siguiente confirmación debe usar partidos ausentes de todo ajuste, calibración y selección del modelo. |
| DEC-032 | 2026-07-26 | El conector ESPN R5 queda operativo para consultar rangos y escribir únicamente en `prospective_staging_v2`; los IDs presentes en calibración, confirmación o router se excluyen antes de normalizar y persistir. | Congelada para operación | La ingesta puede ejecutarse con escritura explícita, pero nunca evalúa, entrena, modifica el router ni convierte staging en evidencia confirmatoria automáticamente. |
| DEC-033 | 2026-07-26 | Fase 31 ejecutará un gate read-only sobre `prospective_staging_v2` para identificar partidos completos posteriores al cutoff y ausentes de todo ajuste, calibración, confirmación y router. | Congelada para operación | El gate sólo publica candidatos elegibles; no calcula métricas, no entrena y no habilita promoción automáticamente. |
| DEC-034 | 2026-07-26 | Fase 32 sólo preparará candidatos con filas de features y contexto pre-match alineadas por partido y cutoff; no generará predicciones si falta una fuente causal. | Congelada para operación | Evita ejecutar el router con features incompletas o posteriores al kickoff; la salida oficial permanece sin cambios. |
| DEC-035 | 2026-07-26 | Fase 33 materializará los insumos pre-match de cada candidato aprobado por Fase 31: features desde historial estrictamente anterior al kickoff y contexto ESPN limitado a identidad, titulares/formación y cuotas `open`; nunca usará eventos, marcador final, estadísticas post-match ni cuotas `current`/`close`/live del objetivo. | Congelada para operación | Cierra la continuidad entre staging y Fase 32 sin modificar el router, entrenar modelos, calcular targets o promover mercados. |
| DEC-036 | 2026-07-26 | La búsqueda ESPN será adaptativa: primero consultará la ventana reciente y, si no alcanza el mínimo de candidatos fuente, ampliará a las temporadas configuradas (por defecto 2025 y 2024). Los partidos históricos o reutilizados se auditarán, pero no podrán convertirse en cohorte confirmatoria. | Congelada para operación | Reduce esperas por ventanas cortas sin relajar cutoff, independencia, anti-leakage ni el requisito de escritura explícita sólo en `prospective_staging_v2`. |
| DEC-037 | 2026-07-26 | Fase 34 generará un paquete de predicciones exclusivamente pre-match para candidatos preparados por Fase 32, reconstruyendo Markov v2 con artefactos históricos congelados y aplicando la selección de Fase 21; no leerá targets, pérdidas ni eventos del partido objetivo, y no promoverá mercados. | Congelada para operación | Deja lista la inferencia prospectiva sin esperar una cohorte para construir el código; la evaluación confirmatoria seguirá siendo una fase posterior e independiente. |
| DEC-038 | 2026-07-26 | Fase 35 evaluará el paquete de Fase 34 sólo después de que los partidos hayan terminado: leerá scores y eventos de staging como targets, calculará log-loss y bootstrap por partido y mantendrá bloqueada toda promoción. | Congelada para operación | Separa estrictamente inferencia pre-match de scoring post-match y permite confirmar o rechazar señales sin modificar el router durante la evaluación. |
| DEC-039 | 2026-07-26 | La expansión multi-liga se tratará como un corpus global separado: cada partido deberá conservar `league_slug`, competición y temporada; no se mezclará con el entrenamiento oficial de LaLiga hasta validar cobertura, identidad de equipos, efectos de competición y particiones temporales sin fuga. | Congelada para arquitectura | Permite abarcar equipos y ligas adicionales sin contaminar el router vigente ni asumir que las intensidades de goles y eventos son comparables entre competiciones. |
| DEC-040 | 2026-07-26 | La cobertura multi-liga comienza con discovery por scoreboard para todos los slugs explícitos de la documentación local; los endpoints de detalle se ejecutarán después sobre referencias deduplicadas y con caché. | Congelada para operación | Evita descargar play-by-play redundante y deja una auditoría completa de cobertura antes de consumir el volumen mayor de eventos. |
| DEC-041 | 2026-07-26 | La primera simulación multi-liga será de estados únicamente: no generará goles ni mercados hasta disponer de un prior Dixon-Coles/Kalman comparable por liga y partido. | Congelada para investigación | La Fase 41 evita atribuir a Markov una intensidad inventada; usa sólo desarrollo temporal, excluye ligas sin soporte de desarrollo y deja la fusión estructural para una fase separada. |
| DEC-042 | 2026-07-27 | La primera fusión estructural multi-liga usará una parametrización Dixon-Coles regularizada por tasas históricas por liga, no un MLE global de 853 equipos. | Congelada para investigación | El MLE global no es operativo en este volumen; la salida conserva la forma estructural, registra `mle_optimized: false`, añade Kalman causal y queda bloqueada para promoción hasta evaluación OOS. |
| DEC-043 | 2026-07-27 | La fusión Markov multi-liga no se promociona tras la evaluación OOS de 3,713 partidos. | Congelada | En confirmación perdió contra el baseline Poisson estructural en 1X2, Over 2.5, BTTS, primer tiempo y segundo tiempo; se conserva el baseline y se bloquean activaciones de la fusión. |
| DEC-044 | 2026-07-27 | Los mercados completos de la Fase 42 deben calcularse analíticamente cuando Markov sólo redistribuye una intensidad conservada; la señal temporal se recalibra con validación y no se promociona sin IC positivo en confirmación. | Congelada | Fase 44 eliminó ruido Monte Carlo de 1X2/Over/BTTS; Fase 45 dejó 25%/30% de Markov temporal, pero no confirmó valor incremental. |
| DEC-045 | 2026-07-27 | El estado inicial de Markov no se condicionará por perfil reciente de ritmo, presión y disciplina sin evidencia OOS favorable. | Congelada | Fase 46 usó perfiles causales en 78.95% de los roles equipo-partido, pero perdió en primer y segundo tiempo; el router permanece sin cambios. |
| DEC-046 | 2026-07-27 | El gate prospectivo debe excluir IDs usados por cualquier modelo, calibración, evaluación o corpus de ventanas, no sólo por el router oficial. | Congelada | Fase 47 incorpora el corpus multi-liga al catálogo de reutilización y evita evaluar como independiente una cohorte ya usada por Fases 38–45. |
| DEC-047 | 2026-07-27 | Las fases downstream deben detenerse limpiamente cuando el gate no entrega una cohorte aprobada. | Congelada | Fase 47 evita materializar candidatos incompletos y elimina un `KeyError` causado por ejecutar Fase 33 con una cohorte bloqueada. |
| DEC-048 | 2026-07-27 | La primera interfaz universal de usuario expondrá sólo el baseline estructural con provenance; Markov multi-liga seguirá fuera de la salida hasta una promoción OOS independiente. | Congelada para producto | Fase 48 permite solicitar un partido por liga, equipos y kickoff, rechaza historial insuficiente y mantiene Markov como experimental. |
| DEC-049 | 2026-07-27 | La resolución de fixtures ESPN y el refresco del histórico se separan de la inferencia: el endpoint puede consultar ESPN sólo en `operational_readonly`, mientras la actualización de datos requiere una operación explícita y escribe únicamente staging. | Congelada para operación | Fase 49 evita que una request modifique datos, mantiene reproducibilidad local, conserva el snapshot canónico intacto y deja como siguiente paso una materialización versionada con rollback. |
| DEC-050 | 2026-07-27 | El servicio sólo consumirá snapshots pre-match versionados, con manifiesto SHA-256, puntero activo y rollback; una publicación nunca sobrescribe una versión existente. | Congelada para operación | Fase 50 activa `phase38_multileague_v1_20260727`, expone su identidad en provenance y permite seleccionar otra versión mediante configuración sin promover Markov. |
| DEC-051 | 2026-07-27 | Una predicción real puede servirse sólo si pasa resolución ESPN, integridad del snapshot y gates causales; una advertencia de frescura debe quedar visible y bloquear la interpretación como evidencia de calidad actual. | Congelada para operación | Fase 51 resolvió Puebla–Guadalajara con HTTP 200 y sin leakage, pero detectó que el histórico termina en diciembre de 2025; se requiere refresco post-2025 antes de confiar plenamente en la salida. |
| DEC-052 | 2026-07-27 | El refresco post-2025 puede materializarse directamente desde ESPN cuando PostgreSQL staging no está disponible, pero sólo incorpora partidos completos con timeline reconciliado y mantiene fuera los inconsistentes. | Congelada para operación | Fase 52 añadió 168 partidos y 2,016 ventanas de `mex.1`, excluyó 17 discrepancias, activó un snapshot con rollback y eliminó la advertencia de frescura del fixture probado sin escribir PostgreSQL. |
| DEC-053 | 2026-07-27 | El refresco multi-liga debe ejecutarse primero en `dry-run`, limitarse por liga durante la operación inicial y requerir `--activate` para publicar el snapshot. | Congelada para operación | Fase 53 procesó 42 ligas documentadas, incorporó sólo 6 partidos completos y 72 ventanas tras reconciliación, dejó fuera 18 referencias problemáticas, activó una versión inmutable y verificó de nuevo el fixture real sin tocar PostgreSQL ni promover Markov. |
| DEC-054 | 2026-07-27 | La ampliación de cobertura se publicará con un nuevo snapshot inmutable cuando el identificador anterior ya exista; nunca se sobrescribe una versión publicada. | Congelada para operación | Fase 54 procesó enero-julio de 2026, incorporó 293 partidos y 3,516 ventanas, activó `phase54_multileague_post2025_v1_20260727` con 117,000 filas y conservó rollback; el flujo real mantuvo HTTP 200, causalidad y router baseline. |
| DEC-055 | 2026-07-27 | La interfaz universal aceptará nombres de equipos además de IDs, pero resolverá la identidad exclusivamente contra el scoreboard ESPN y exigirá un único fixture futuro. | Congelada para producto | Fase 55 resolvió Puebla–Guadalajara por nombres, devolvió HTTP 200, utilizó el snapshot activo con cutoff causal y mantuvo fuera del request la persistencia y los datos del partido objetivo. |
| DEC-056 | 2026-07-27 | Una solicitud universal multi-liga debe fallar cerrada cuando la liga no alcanza el mínimo histórico requerido; no se rellenan tasas con otra competición. | Congelada para producto | Fase 56 verificó 9 predicciones causales y rechazó Uruguay con `league_history_below_minimum`, preservando la honestidad de cobertura. |
| DEC-057 | 2026-07-27 | El refresco operativo se ejecutará en ventanas incrementales con dry-run predeterminado y snapshot nuevo para cada activación. | Congelada para operación | Fase 57 materializó 7 partidos y 84 ventanas candidatas, activó `phase57_incremental_v1_20260727` y conservó rollback sin escribir PostgreSQL. |
| DEC-058 | 2026-07-27 | Markov se investigará como residual temporal selectivo sobre Dixon-Coles/Kalman, con estado inicial pre-match calibrado y promoción independiente por mercado. | Congelada para investigación | Fase 58 auditó el OOS existente, confirmó que Dixon-Coles + Kalman no domina todos los mercados, mantuvo el router sin cambios y bloqueó Markov hasta una nueva cohorte independiente y un gate confirmatorio. |
| DEC-059 | 2026-07-27 | La estructura correcta de ventanas no basta para entrenar Markov: la calidad semántica exige auditar eventos raw, timestamps y taxonomía antes de aceptar el corpus. | Congelada para investigación | Fase 59 encontró 0 fallos estructurales, pero 58.46% de eventos no clasificados y ausencia del timeline crudo local; se bloquea el entrenamiento residual hasta una auditoría raw ESPN. |
| DEC-060 | 2026-07-27 | Los eventos `unclassified` no se descartarán automáticamente: primero se ampliará la taxonomía raw→normalizado y se separarán eventos modelables de eventos válidos pero irrelevantes para Markov. | Congelada para investigación | La auditoría raw de Fase 59 halló timestamps íntegros, marcador reconciliado en 15/15 y 1,304/1,893 eventos no clasificados dominados por tipos ESPN reconocibles; el siguiente trabajo es corregir el mapeo, no reducir la cohorte arbitrariamente. |
| DEC-061 | 2026-07-27 | La taxonomía ESPN v1.1 clasifica eventos reconocibles no modelables como `auxiliary`; sólo los tipos explícitamente permitidos llegan a Markov y los desconocidos permanecen auditables como `unclassified`. | Congelada para investigación | La cohorte raw reprocesada obtuvo 0/1,893 `unclassified`, 1,096 auxiliares, 797 modelables y 15/15 marcadores reconciliados. Falta rematerializar el snapshot aislado y repetir el gate global antes de entrenar o promover Markov. |
| DEC-062 | 2026-07-27 | La taxonomía v1.1 puede avanzar sólo como candidato aislado cuando conserva señales modelables; la promoción exige además cerrar cobertura y recalibrar cualquier señal recuperada. | Congelada para investigación | Fases 60–61 cubrieron 9,751 partidos activos, eliminaron `unclassified` y dejaron 3,893 diferencias sólo en faltas; los estados no cambiaron, pero las transiciones deben recalibrarse. |
| DEC-063 | 2026-07-27 | Los eventos de tanda identificados por `penalty___scored` o `penalty---scored` con marcador entre paréntesis no cuentan como goles reglamentarios; partidos con discrepancia de marcador permanecen excluidos. | Congelada para investigación | La corrección reconcilió 11 partidos que antes quedaban fuera por tratar penales de tanda como goles; permanecen 401 exclusiones históricas concentradas en `uru.1` y amistosos. |
| DEC-064 | 2026-07-27 | La cohorte confirmatoria debe congelarse antes del kickoff y no puede observar play-by-play ni resultados durante ajuste/calibración. | Congelada para investigación | Fase 62 bloqueó 9 fixtures futuros, con IDs únicos, sin resultados ni play-by-play, para evaluar Markov residual después de calibrar. |
| DEC-065 | 2026-07-27 | El estado inicial Markov se estimará con un clasificador multinomial calibrado sobre perfiles causales de cinco partidos previos; soporte insuficiente de una clase bloquea promoción, no se imputa evidencia. | Congelada para investigación | Fase 63 mejoró los priors global/liga en validación y confirmación histórica, pero `repliegue` no aparece en desarrollo; se reserva para evaluación independiente sin activar router. |
| DEC-066 | 2026-07-27 | Las predicciones Markov candidatas se congelan antes del kickoff sólo para `first_half_goal`; la propagación usa state_0 calibrado, emisiones por estado y transiciones con diferencial `level` bajo la hipótesis pre-match. | Congelada para investigación | Fase 63 publicó 9 salidas con hashes, sin resultados/play-by-play, sin modificar snapshot/router. La hipótesis debe superar log-loss, bootstrap y estabilidad OOS antes de cualquier promoción. |
| DEC-067 | 2026-07-28 | Se permite un replay histórico walk-forward para validar el flujo y detectar fallos, pero sus partidos no cuentan como confirmación si pertenecen al periodo usado para auditar o seleccionar el modelo. | Congelada para investigación | El replay de 30 partidos obtuvo log-loss Markov `0.796682` frente a baseline `0.730142`; la mejora fue negativa y su IC cruzó cero. Se conserva como evidencia diagnóstica, sin promoción ni cambio de router. |
| DEC-068 | 2026-07-28 | Markov se integrará como residual acotado sobre el baseline: `p_fusion=(1-alpha)*p_baseline+alpha*p_markov`; `alpha` se selecciona sólo en validación y el holdout queda intacto. | Congelada para investigación | El replay mostró sobreconfianza Markov. La fusión debe conservar el baseline como ancla y cualquier resultado seguirá siendo diagnóstico hasta una cohorte independiente. |
| DEC-069 | 2026-07-28 | Un replay de partidos ya terminados es válido para evaluar el modelo si cada predicción usa sólo información anterior al kickoff; la cohorte futura queda reservada para independencia de selección, no porque el pasado sea inválido. | Congelada para investigación | Fase 65 auditó 3,921 partidos posteriores al entrenamiento con orden causal estricto. El resultado localiza la falla en la cobertura de transiciones y mantiene bloqueada la promoción. |
| DEC-070 | 2026-07-28 | La recalibración de transición usará pooling jerárquico suave `team→competition→window→global`, con peso específico dependiente del soporte y parámetro seleccionado en la primera mitad del replay. | Congelada para investigación | El backoff rígido de Fase 40 quedó dominado por `global/uniform`; la nueva variante conservará la causalidad, no tocará el router y será evaluada sobre un holdout temporal separado. |
| DEC-071 | 2026-07-28 | El pooling suave no se promoverá ni se seguirá ajustando sin auditar primero la relación entre estados y emisiones de gol. | Congelada para investigación | Fase 66 seleccionó `specificity=2.0` en validación, pero perdió en holdout (`0.641220` vs `0.639682`) con IC estrictamente negativo. La siguiente hipótesis debe revisar `state→emission`, no sólo el peso de transición. |
| DEC-072 | 2026-07-28 | Se probará una emisión temporalmente alineada: la primera ventana conserva el baseline y las siguientes usan `state_t→goles_t+1`; la prueba mantiene las transiciones originales para aislar el efecto de la emisión. | Congelada para investigación | Fase 67 detectó brechas contemporánea→siguiente de `0.0235` a `0.0540` goles por estado. La variante será seleccionada en validación y comprobada en holdout sin tocar el router. |
| DEC-073 | 2026-07-28 | Se probará un residual directo de `state_0` sobre `first_half_goal`: probabilidades de estado latente por equipo, emisión de resultado por par de estados y pooling por liga; no se simulan transiciones para este diagnóstico. | Congelada para investigación | Las transiciones y emisiones temporales no aportaron valor; esta prueba aísla si la información de estado inicial contiene señal pre-match antes de volver a ampliar la cadena. |
| DEC-074 | 2026-07-28 | La siguiente variante debe revisar la semántica de estados e incorporar tiros, tiros a puerta, corners, goles y disciplina; no se continuará ajustando Markov sobre `state_labeling_v1` sin esa auditoría. | Congelada para investigación | Fases 67–69 detectaron desalineación temporal y ningún valor incremental de transición, emisión desplazada o `state_0` directo. `state_labeling_v1` no captura explícitamente amenaza ofensiva. |
| DEC-075 | 2026-07-28 | `state_labeling_v2` no reemplaza v1: aumentar variables sin control semántico sobreagrupa estados y reduce la separación de riesgo futuro. | Congelada para investigación | Fase 70 redujo el spread de goles de la siguiente ventana de `0.132934` a `0.085693`; se conserva v1 y se abre una revisión semántica antes de otra integración. |
| DEC-076 | 2026-07-27 | La Fase 71 separa el régimen conjunto de ritmo, relevante para `first_half_goal`, del régimen direccional de control; el estado de una ventana se infiere con evidencia anterior a esa ventana y Markov sólo puede emitir un residual calibrado sobre el baseline con abstención. | Congelada para investigación | Cuatro taxonomías causales seleccionaron `alpha=0`; el mejor estado inicial fue predecible, pero la transición no superó de forma estable al prior de liga y el spread de gol siguiente fue sólo `0.020323`. El runtime candidato queda funcional con fallback exacto al baseline y sin promoción. |

DEC-077
Fecha: 2026-07-27
Problema: Las taxonomías manuales de 15 minutos no conservan suficiente memoria
temporal ni separan riesgo direccional; además, gran parte del contexto ESPN
disponible antes del kickoff no tiene todavía snapshots con instante de
disponibilidad demostrable.
Opciones: continuar ajustando pesos sobre los cuatro estados existentes;
reemplazar Markov por un clasificador tabular; o reconstruir la capa como un
modelo Markov latente, direccional y condicionado por contexto causal.
Decisión: proponer `markov_pre_match v4` como un modelo de estados latentes con
duración, aprendido en microventanas históricas y agregado a mercados de 15
minutos. Dixon-Coles/Kalman permanecen como prior estructural. La distribución
inicial y las transiciones sólo reciben datos congelados antes del kickoff; el
marcador futuro existe únicamente dentro de la simulación. Se amplía el
conector ESPN y se versionan snapshots por cutoff antes de usar lesiones,
plantillas, alineaciones, cuotas, árbitros o sedes.
Motivo: el fallo observado es semántico y de información, no de mezcla. Un
modelo secuencial sólo se justifica si captura persistencia, duración y
trayectorias que un baseline tabular con los mismos datos no reproduce.
Estado: congelada
Impacto en contratos/fases: crea `prematch_context_snapshot v2`,
`markov_sequence_features v1` y `markov_pre_match v4`; reserva Fases 72–82
para reconstrucción, confirmación e integración y desplaza
`betting_value_validation` hasta después de la promoción probabilística.
Reemplaza únicamente la granularidad fija de 15 minutos de `DEC-002`: las
microventanas sirven para aprender la dinámica y la salida se agrega a
intervalos de 15 minutos. Conserva de `DEC-002` el carácter dependiente de
contexto, histórico y estrictamente pre-match.
Evidencia requerida: causalidad sin excepciones, estados estables y
predecibles, mejora temporal OOS contundente frente a todos los comparadores,
cohorte prospectiva independiente y estabilidad por liga.

DEC-078
Fecha: 2026-07-27
Problema: Fase 75 requiere un target direccional que conserve la identidad de
quién genera riesgo en cada intervalo y un comparador suficientemente fuerte
para no atribuir a Markov señal disponible en agregados históricos.
Opciones: predecir sólo presencia de gol; usar dos Bernoulli independientes por
equipo; o modelar conjuntamente cuatro resultados direccionales por intervalo.
Decisión: congelar por cada intervalo de 15 minutos el target multiclase
`neither`, `home_only`, `away_only`, `both`. Los labels se derivan sólo después
del partido y permanecen fuera del paquete de inferencia. Se comparará un
baseline analítico Poisson/Beta jerárquico contra un modelo tabular multinomial
con exactamente los mismos perfiles históricos pre-kickoff. Hiperparámetros,
modelo y calibración se seleccionan sólo en `selection`; `confirmation` queda
intacta.
Motivo: las cuatro clases preservan dirección y dependencia conjunta, permiten
derivar cualquier-gol y equipo-marca sin convertir esos mercados en el objetivo
de aprendizaje, y exponen si una cadena secuencial aporta algo que un modelo
no secuencial same-data no reproduce.
Estado: congelada para ejecución
Impacto en contratos/fases: crea `directional_interval_targets_v1` y
`temporal_same_data_baseline_v1`; no modifica `match_features v1`, el router,
Dixon-Coles/Kalman ni la política de promoción de Fase 80.
Evidencia requerida: probabilidades normalizadas, calibración seleccionada sin
confirmación, métricas reproducibles, cero target-match events en features y
scoring por partido completo.

DEC-079
Fecha: 2026-07-27
Problema: Fase 76 debe descubrir estados latentes con duración sin reutilizar
las taxonomías manuales fallidas ni introducir una dependencia HSMM no
disponible y difícil de auditar.
Opciones: instalar una implementación externa de HMM/HSMM; construir un EM
completo propio; o separar emisiones latentes reproducibles de la evaluación
explícita de duración y secuencia.
Decisión: aprender emisiones multivariadas gaussianas sobre observaciones
conjuntas de microventanas de 5 minutos, con 4–8 estados y normalización
ajustada sólo en `fit`. La selección usa únicamente `fit/selection`. Para cada
estado se estima una distribución discreta suavizada de duración y se compara
su likelihood OOS contra la duración geométrica implícita del Markov ordinario.
El gol de la ventana siguiente se reserva como label semántico de evaluación y
no entra en emisiones, clustering ni inferencia.
Motivo: aísla si existen regímenes observables, estables y persistentes antes
de construir en Fase 78 transiciones contextuales completas. Mantiene código,
semillas, alineación de estados y likelihood completamente auditables.
Estado: congelada para ejecución
Impacto en contratos/fases: crea `latent_state_discovery_v1`; Fase 76 descubre
emisiones y duración, mientras Fase 78 conserva la responsabilidad de estimar
transiciones jerárquicas pre-match. No modifica el router.
Evidencia requerida: 4–8 estados, ocupación mínima 5%, NMI ≥0.70, spread de
riesgo siguiente ≥0.05, orden estable en ≥75% de ligas con soporte y likelihood
OOS de duración superior al geométrico.

DEC-080
Fecha: 2026-07-27
Problema: La mezcla gaussiana de Fase 76 genera componentes degenerados sobre
conteos cero-inflados, depende de estados transitorios y no conserva identidad
entre periodos. El clustering conjunto y el lag completo tampoco superan los
gates internos de estabilidad y riesgo.
Opciones: seguir ajustando GMM; instalar un HSMM externo; o aprender una
representación predictiva regularizada y discretizarla en regímenes ordenados.
Decisión: reemplazar el candidato GMM, sin borrar su evidencia negativa, por
`predictive_latent_state_v2`. Una regresión logística regularizada aprende en
`fit` el riesgo de que el mismo equipo marque en la microventana siguiente a
partir de emisiones causales actuales. El label futuro participa únicamente en
el ajuste de desarrollo y nunca es una feature. El score continuo se divide en
4–8 estados por cuantiles aprendidos en `fit`; cantidad de estados y
regularización se eligen sólo en `selection`. Duración explícita permanece
separada y se compara contra geométrica.
Motivo: produce estados direccionales ordenados, balanceados e inferibles desde
observaciones presentes, y prueba directamente la semántica futura exigida por
el plan sin reglas manuales ni componentes gaussianos inadecuados para conteos.
Estado: congelada para ejecución
Impacto en contratos/fases: crea una reauditoría aditiva de Fase 76; no
sobrescribe artefactos GMM. Debido a que la confirmación original ya fue
observada, un candidato exitoso se clasifica `promising_unconfirmed` y no
desbloquea Fase 77 hasta nueva evidencia independiente.
Evidencia requerida: gates originales en `fit/selection`, prueba de permutación,
duración OOS, estabilidad por liga, separación física de labels y nueva cohorte
confirmatoria antes de avanzar.

DEC-081
Fecha: 2026-07-27
Problema: La cohorte independiente posterior al cutoff reveló que el endpoint
Core de plays puede exceder 300 filas, mientras el conector sólo consumía la
primera página. Esto truncaba goles y producía discrepancias de marcador.
Opciones: excluir partidos con 300 eventos; inferir goles desde el score final;
o implementar paginación completa raw-first.
Decisión: recorrer `pageCount` con máximo defensivo de 20 páginas, conservar
todas las respuestas fuente dentro del payload raw compuesto y fusionar
`items` sólo después de completar la descarga. Todo partido debe reconciliar
sus goles antes de entrar a una cohorte.
Motivo: excluir por exactamente 300 eventos ocultaría un bug sistemático e
inferir goles desde el marcador destruiría timestamps y causalidad.
Estado: congelada para operación
Impacto en contratos/fases: actualiza el conector ESPN a 1.1.0 y obliga a
reingerir timelines truncados. No modifica modelos ni router.
Evidencia requerida: prueba de dos páginas, raw provenance, cero discrepancias
en la nueva cohorte y replay idempotente.

DEC-082
Fecha: 2026-07-27
Problema: `predictive_latent_state_v2` requiere evidencia posterior al cutoff,
pero sólo 19 partidos de 5 ligas están completos al momento de la primera
captura.
Opciones: declarar éxito con 19 partidos; reutilizar holdouts observados; o
mantener una cohorte acumulativa sellada.
Decisión: recolectar automáticamente en cada ejecución todos los completos
posteriores a `2026-07-26T18:00:00Z`, sin cambiar modelo, features o umbrales.
Fase 76 exige al menos 200 partidos y 10 ligas para una confirmación de estados;
Fase 81 conserva su gate superior de 500 partidos y 10 ligas.
Motivo: 19 partidos permiten verificar causalidad e infraestructura, pero no
estabilidad de estados ni duración con incertidumbre razonable.
Estado: congelada para ejecución
Impacto en contratos/fases: crea colección y evaluación independientes
acumulativas; Fase 77 permanece bloqueada mientras la cobertura sea
insuficiente.
Evidencia requerida: marcador reconciliado, inferencia congelada, cobertura
mínima, métricas por liga y ninguna modificación posterior al cutoff.

## Formato para nuevas decisiones

DEC-083
Fecha: 2026-07-28
Problema: La cobertura posterior al cutoff era insuficiente, aunque staging
contenía una extracción nominal de 10,251 partidos y 42 ligas.
Opciones: mantener sólo el gate cronológico; tratar todos los fixtures como
secuencias válidas; o sellar un holdout por exclusión exacta y reconciliarlo.
Decisión: usar como candidatos los partidos completos cuyos IDs nunca entraron
al corpus de Fase 74, aplicar el modelo congelado y exigir reconciliación de
marcador. Una liga sólo cuenta si aporta secuencias utilizables. Más de 2% de
discrepancias rechaza la cohorte.
Motivo: independencia de entrenamiento y cobertura nominal no son equivalentes
a play-by-play válido. ESPN devolvió timelines vacíos al reconsultar 401 de 777
partidos candidatos.
Estado: congelada para revisión
Impacto en contratos/fases: sustituye la clasificación fija de la evaluación
por gates ejecutables; Fase 76 permanece `rejected_for_revision`.
Evidencia requerida: al menos 200 partidos, 10 ligas útiles, spread >=0.05,
ocupación >=5%, estabilidad >=75%, duración positiva y discrepancias <=2%.

DEC-084
Fecha: 2026-07-28
Problema: Core `/plays` puede devolver una colección vacía aunque Site
`/summary` conserve commentary temporal del mismo partido.
Opciones: excluir siempre el partido; sintetizar desde marcador; o usar el
endpoint documentado como fallback.
Decisión: cuando todas las páginas Core estén vacías, convertir únicamente
`summary.commentary[*].play` al contrato de normalización. Los IDs de equipo se
resuelven desde el header del mismo summary y el payload completo se conserva
como raw provenance.
Motivo: recupera evidencia temporal real sin imputar eventos ni consultar
fuentes no documentadas.
Estado: congelada
Impacto en contratos/fases: conector ESPN 1.2; no cambia features ni router.
Evidencia requerida: prueba de mapeo de identidad, raw summary preservado y
reconciliación obligatoria antes de admitir un partido.

DEC-085
Fecha: 2026-07-28
Problema: v2 pierde separación semántica entre ligas con distinta densidad de
eventos.
Opciones: efectos fijos de liga; normalización retrospectiva por liga; o
emisiones direccionales invariantes con memoria causal.
Decisión: crear `predictive_latent_state_v3` con contrastes equipo-rival,
cuotas de actividad, eficiencias y medias causales de 10/15 minutos. Selección
y congelación usan sólo fit/selection. El holdout se abre una vez y luego pasa
al catálogo de reutilización.
Motivo: evita depender de la escala particular de una liga y conserva una
interpretación física predecible desde secuencias observadas.
Estado: congelada; rechazada para promoción
Impacto en contratos/fases: v3 mejora spread del holdout de 0.034152 a
0.042423, pero no supera 0.05; Fase 77 continúa bloqueada.
Evidencia requerida: nueva cohorte prospectiva no observada con al menos 200
partidos/10 ligas y todos los gates originales.

DEC-086
Fecha: 2026-07-28
Problema: después del rechazo del holdout histórico, seguir evaluando cada
captura permitiría adaptar decisiones a outcomes parciales.
Opciones: métricas diarias; esperar sin contrato; o sellar modelo/cutoff y
mantener outcomes ciegos hasta cobertura.
Decisión: congelar v3 por SHA-256 con cutoff
`2026-07-28T06:44:20.320524Z`. El gate sólo lee identidades y cobertura; no
calcula métricas hasta 200 partidos utilizables de 10 ligas. Cualquier cambio
del hash aborta la operación.
Motivo: convierte la siguiente evaluación en confirmación prospectiva real y
elimina el optional stopping sobre métricas parciales.
Estado: congelada para operación
Impacto en contratos/fases: nueva colección
`phase_76_v3_prospective_collection` y gate ciego; Fase 77 sigue bloqueada.
Evidencia requerida: `metrics_sealed=true`, `outcomes_read=false`, hash
verificado y evaluación única sólo tras `ready_for_evaluation`.

DEC-087
Fecha: 2026-07-28
Problema: la cohorte sellada mezclaba ausencia real de play-by-play con goles
duplicados, tandas y una identidad directa de equipo que el parser ignoraba.
Opciones: modificar eventos PostgreSQL; imputar secuencias desde el marcador;
o preservar raw y reconciliar únicamente con evidencia cerrada por marcador.
Decisión: conservar PostgreSQL como evidencia inmutable, aceptar `team.id`
directo y aplicar reconciliación derivada con rollback total si no coincide
exactamente el marcador por local y visitante.
Motivo: recupera cobertura verificable sin inventar goles, timestamps o
disponibilidad histórica.
Estado: congelada para ingesta
Impacto en contratos/fases: el holdout pasa de 376/9 a 381/10, pero Fase 76
sigue rechazada porque el spread v3 es 0.042241, inferior a 0.05. El lock
prospectivo y el router oficial no cambian.
Evidencia requerida: pruebas de duplicados, tandas, goles simultáneos reales,
no imputación y auditoría PostgreSQL reproducible.

DEC-088
Fecha: 2026-07-28
Problema: esperar 200 partidos para confirmar v3 no corrige el fallo semántico
ya observado (`spread 0.042241 < 0.05`) y retrasaría el rediseño sin una
hipótesis razonable de mejora.
Opciones: mantener v3 congelado hasta completar la cohorte; ajustar otra vez
contra el holdout observado; o retirar v3, reclasificar toda evidencia
histórica como desarrollo y exigir validación temporal anidada y por dominio.
Decisión: retirar el lock v3 como ruta de aprobación, conservarlo
como evidencia negativa inmutable y abrir Fase 76R. La nueva familia se
seleccionará sólo en folds internos y se evaluará con predicciones
cross-fitted en múltiples bloques temporales y ligas excluidas.
Motivo: la robustez debe demostrarse repetidamente antes de volver a congelar
un candidato prospectivo; acumular outcomes para una formulación fallida no
agrega valor.
Estado: congelada
Impacto en contratos/fases: Fase 77 continúa bloqueada; el router permanece
en baseline. Un nuevo lock prospectivo sólo podrá crearse si 76R supera todos
los gates sin reutilizar predicciones in-sample.
Evidencia requerida: estados 4–8 con ocupación ≥5%, NMI ≥0.70, spread ≥0.05
en cada bloque OOS admitido, estabilidad ≥75% de ligas, duración superior y
replay reproducible.
Evidencia obtenida: dos folds externos aprobaron; spread 0.056876/0.056224,
NMI 0.847527/0.796878, ocupación 10.34%/10.89%, estabilidad 100%/96% y mejora
de duración +0.140545/+0.145935.

DEC-089
Fecha: 2026-07-28
Problema: el estado inicial de 76R se observa en los primeros cinco minutos,
pero en producción debe existir antes del kickoff.
Opciones: prior global; prior liga/localía; o clasificador jerárquico con
perfiles rolling de ambos equipos y backoff explícito.
Decisión: usar como baseline el prior liga+localía y entrenar un
clasificador multinomial regularizado sobre distribuciones históricas de
estado y actividad agregada de ambos equipos. Cada perfil se congela antes de
actualizar el partido objetivo.
Motivo: separa predictibilidad pre-match de semántica in-play y garantiza modo
core universal sin fabricar contexto.
Estado: congelada; candidato rechazado
Impacto en contratos/fases: sólo habilita Fase 78 si mejora log-loss ≥1% sin
degradar Brier/ECE en ambos bloques temporales; router sin cambios.
Evidencia requerida: predicciones OOS por partido-equipo, auditoría de cutoff,
backoff sin historia, calibración y replay.
Evidencia obtenida: el clasificador empeora log-loss `0.95%/1.20%` en
selección/confirmación; también empeoran Brier y ECE. El prior específico de
equipo converge al baseline al aumentar shrinkage y no aporta señal estable.

DEC-090
Fecha: 2026-07-28
Problema: un único estado de régimen separa riesgo in-play, pero su apertura
no es predecible; forzar persistencia de equipo dentro del mismo eje degrada
el modelo.
Opciones: abandonar state_0; redefinir estados desde contexto pre-match; o
factorizar estado en estilo persistente y régimen dinámico.
Decisión: usar seis estados `style_state(2) × match_regime(3)`. El estilo se
calcula exclusivamente con perfiles previos al kickoff y permanece fijo
dentro del partido; el régimen usa emisiones causales y colas 15/85.
Motivo: separa lo conocido pre-match de la evolución estocástica sin reemplazar
la señal dinámica ni incorporar eventos del objetivo al estilo.
Estado: congelada para desarrollo
Impacto en contratos/fases: Fases 76–77 quedan `ready_for_next_phase`; Fase 78
queda autorizada. El router sigue en baseline y Fase 80 deberá comparar contra
un modelo tabular con exactamente el mismo contexto de estilo.
Evidencia requerida: gates semánticos y state_0 aprobados en dos folds OOS,
pruebas de causalidad, replay y suite completa.

DEC-091
Fecha: 2026-07-28
Problema: las transiciones históricas dependían demasiado de global/uniform y
el nivel equipo no generaliza en los seis estados duales.
Opciones: insistir en team pooling; usar sólo liga+ventana; o condicionar la
evolución conjunta por régimen simultáneo del rival.
Decisión: modelar únicamente la transición del régimen, mantener
estilo fijo y usar pooling
`rival-context → liga+ventana+régimen propio → global`.
Motivo: el régimen rival está disponible en cada trayectoria simulada y aporta
interacción física distinta de la identidad nominal del equipo.
Estado: congelada para desarrollo
Impacto en contratos/fases: sólo habilita Fase 79 si mejora log-loss ≥1%, la
masa contextual supera 50% y duración queda calibrada dentro de 10%.
Evidencia requerida: evaluación por partido y liga en dos bloques temporales,
matrices normalizadas, soporte/backoff y replay.
Evidencia obtenida: alpha 60 seleccionado en validation; mejora de log-loss
`1.95%/2.16%`, masa contextual `54.77%/58.98%`, estabilidad por liga
`93.33%/90.91%` y error máximo de duración `5.21%/7.05%`.

DEC-092
Fecha: 2026-07-28
Problema: una simulación Markov puede aparentar valor alterando la expectativa
total de goles que ya estiman Dixon-Coles/Kalman, en lugar de aportar estructura
temporal y de interacción.
Opciones: estimar emisiones absolutas por estado; fusionar lambdas con pesos; o
usar Markov sólo para redistribuir exactamente cada lambda entre microventanas.
Decisión: cada trayectoria normaliza pesos positivos de riesgo dual para que la
suma de intensidades de sus 18 ventanas sea exactamente la lambda estructural
de cada equipo. Markov modifica únicamente equipo, tiempo y régimen; nunca la
capacidad goleadora total. Las transiciones de ambos equipos se calculan desde
el estado conjunto anterior para evitar sesgo de orden.
Motivo: hace identificable y auditable el aporte incremental de Markov, permite
mercados temporales distintos del baseline y conserva el rol arquitectónico de
Dixon-Coles/Kalman.
Estado: congelada para desarrollo
Impacto en contratos/fases: define el simulador de Fase 79; no modifica router
ni autoriza promoción. Fase 80 deberá comparar esta redistribución con baseline
estructural y tabular same-data.
Evidencia requerida: replay bit a bit, error de conservación `<1e-6`,
probabilidades válidas, estilo invariante, backoff core universal y auditoría
que demuestre cero lecturas posteriores al cutoff.
Evidencia obtenida: dos modos con 5,000 trayectorias producen replay idéntico,
error máximo `6.661e-16`, error 1X2 `0`, cero cambios de estilo, cero lecturas
post-cutoff y distancia `0.016304` frente al reparto temporal plano.

DEC-093
Fecha: 2026-07-28
Problema: evaluar miles de partidos con Monte Carlo añade ruido al score y
puede confundir una mejora pequeña con variación de simulación; reutilizar
parámetros finales de Fases 77–78 además contaminaría los folds.
Opciones: aumentar simulaciones; fijar ruido común; o propagar exactamente la
distribución conjunta de 36 pares de estados dentro de cada fold.
Decisión: Fase 80 reajusta estado, apertura, transición y riesgo únicamente con
el train de cada fold y usa propagación determinista conjunta para obtener
intensidades esperadas, normalizadas a las lambdas estructurales. Se exige
paridad contra Monte Carlo de Fase 79 en una muestra antes de aceptar scores.
Motivo: elimina varianza computacional sin alterar la semántica Markov y
preserva el aislamiento temporal anidado.
Estado: congelada para evaluación
Impacto en contratos/fases: sólo afecta el motor de evaluación de Fase 80; el
simulador operativo de Fase 79 y el router no cambian.
Evidencia requerida: cero solapamiento train/target, parámetros por fold,
paridad de probabilidades, ablaciones, bootstrap por partido y corrección Holm.

DEC-094
Fecha: 2026-07-28
Problema: la primera corrida de Fase 80 reemplazó la forma temporal del
comparador same-data por una curva Markov basada sólo en riesgo de estado,
eliminando señal causal de tiros, presión y forma que el baseline ya poseía.
Opciones: aceptar el rechazo; añadir esas variables a emisiones absolutas; o
modelar Markov como deformación residual conservativa de la curva fuerte.
Decisión: usar las intensidades implícitas del tabular same-data como carrier
temporal y multiplicarlas por riesgo Markov elevado a una fuerza positiva,
seleccionada únicamente en selection. Después se renormaliza por equipo para
conservar la intensidad total. Fuerza cero queda prohibida.
Motivo: Markov debe aportar dependencia secuencial incremental, no reconstruir
peor la señal estática ni aprobar copiando el baseline.
Estado: congelada; candidato rechazado para promoción
Impacto en contratos/fases: revisión interna de Fase 80; no cambia estados,
transiciones, simulador Fase 79 ni router oficial.
Evidencia requerida: selección positiva de fuerza, distancia no nula respecto
al baseline, gate completo frente al tabular y ablaciones reproducibles.
Evidencia obtenida: fuerza positiva `0.1` y variante `15m`; después de
preservar carrier y odds-ratio, la mejora confirmatoria fue `-0.000002`, Brier
`+0.000002` e IC95% `[-0.000019, 0.000016]`. El gate no se supera.

DEC-095
Fecha: 2026-07-28
Problema: las transiciones sí predicen el siguiente régimen cuando el régimen
actual es observado, pero pre-match ambos regímenes deben marginalizarse; la
mezcla resultante converge al baseline y no mejora el score por ventana.
Opciones: seguir ajustando pooling/pesos; promover mercados sin evidencia; o
rechazar la marginalización como fuente de valor y revisar el target.
Decisión: cerrar Fase 80 como `rejected_for_revision`, mantener Markov fuera
del router y bloquear Fase 81. No continuar optimizando marginales por ventana.
La próxima revisión sólo puede estudiar likelihood de trayectoria completa,
dependencia entre ventanas o mercados explícitamente secuenciales, siempre
contra un comparador same-data que modele la misma dependencia.
Motivo: las correcciones mecánicas redujeron la degradación de `-0.008983` a
`-0.000002`, pero no apareció valor incremental ni IC positivo.
Estado: congelada
Impacto en contratos/fases: Fase 81 y promoción permanecen bloqueadas; Fases
76–79 siguen válidas como semántica y mecánica, no como evidencia predictiva
final.
Evidencia requerida: artefactos completos de Fase 80, ablaciones, bootstrap,
estabilidad por liga y replay.

DEC-096
Fecha: 2026-07-28
Problema: el score marginal por ventana no recompensa la dependencia entre
ventanas, que es precisamente la diferencia estructural de Markov.
Opciones: abandonar Markov; inventar mercados sin comparador; o puntuar la
probabilidad conjunta de la trayectoria direccional completa.
Decisión: abrir Fase 80R con likelihood pre-match de las seis ventanas. El
modelo latente se evalúa mediante forward algorithm sobre 36 pares de estados;
las emisiones son residuos contra el tabular same-data. Debe superar tanto al
tabular factorized como a un comparador secuencial directo de clases observado
con los mismos datos. Todos los parámetros se reajustan dentro de cada fold.
Motivo: mide dependencia temporal real sin permitir que Markov reciba crédito
por información estática ni por observar eventos del partido al predecir.
Estado: congelada; candidato rechazado para promoción
Impacto en contratos/fases: revisión permitida por DEC-095; Fase 81 y router
continúan bloqueados hasta evidencia confirmatoria positiva.
Evidencia requerida: likelihood conjunto normalizado por partido/ventana,
bootstrap por partido, calibración condicional, comparador secuencial directo,
ablaciones de transición/emisión y cero lecturas target en inferencia.
Evidencia obtenida: `no_transition` fue seleccionado en selection. En
confirmation, Markov obtuvo log-loss `0.989798` frente a `0.989387` del
comparador secuencial directo; mejora `-0.000411`, Brier `-0.000088`, IC95%
`[-0.001266, 0.000422]` y 48.28% de ligas no negativas.

DEC-097
Fecha: 2026-07-28
Problema: Fases 80 y 80R muestran que los estados conservan semántica, pero la
transición latente no supera ni marginales ni likelihood de trayectoria.
Opciones: seguir reformulando la misma cadena; eliminar todo Markov; o separar
producto técnico de promoción estadística.
Decisión: conservar el simulador dual y habilitar únicamente salidas
experimentales de trayectoria en shadow (primer gol, número de ventanas con
gol y clustering), claramente etiquetadas y fuera del router. No abrir Fase 81
ni afirmar valor incremental. Una nueva cadena sólo puede partir de un estado
observable/predecible diferente y una cohorte no reutilizada.
Motivo: preserva el trabajo funcional y permite investigación de mercados
genuinamente secuenciales sin convertir evidencia negativa en producción.
Estado: congelada
Impacto en contratos/fases: Fase 80R rechazada; Fase 81 bloqueada; se autoriza
Fase 80S técnica en shadow sin promoción.
Evidencia requerida: mercados normalizados, replay, conservación, provenance,
etiqueta experimental y router intacto.
Evidencia obtenida: cinco mercados de trayectoria en modos contextual/core,
replay idéntico, error categórico `0`, conservación `6.661e-16`, clasificación
`experimental_shadow_not_promoted` y router intacto.

DEC-098
Fecha: 2026-07-28
Problema: el régimen in-play latente no es observable antes del kickoff y su
marginalización elimina la señal de transición.
Opciones: abandonar estados; predecir otra variable in-play; o definir un
estado persistente del matchup enteramente pre-match.
Decisión: evaluar un Markov observable condicionado por arquetipo pre-match.
El estado dinámico es la clase direccional de la ventana y el arquetipo fijo se
construye con ritmo total y dominio esperado de perfiles causales. Sus
transiciones se suavizan hacia el Markov directo same-data.
Motivo: toda heterogeneidad condicionante es conocida al kickoff, la cadena
permanece genuinamente secuencial y el comparador directo aísla el valor del
arquetipo.
Estado: congelada; candidato rechazado para promoción
Impacto en contratos/fases: abre Fase 80T de revisión; Fase 81 y router siguen
bloqueados hasta superar los gates existentes.
Evidencia requerida: cortes aprendidos sólo en train, ocupación y cobertura de
arquetipos, mejora frente a tabular y Markov directo, bootstrap por partido,
estabilidad por liga, replay y cero outcomes target en el arquetipo.
Evidencia obtenida: `home_away_quadrants`, smoothing `200`, ocupación mínima
`11.63%`; en confirmation perdió `0.000344` frente al Markov directo, IC95%
`[-0.000906, 0.000200]` y sólo 34.48% de ligas fueron no negativas.

DEC-099
Fecha: 2026-07-28
Problema: los arquetipos discretos tienen soporte, pero comprimen demasiado el
contexto pre-match y no mejoran la transición directa.
Opciones: aumentar bins; buscar taxonomías manuales; o usar una cadena Markov
no homogénea con transición parametrizada continuamente por contexto.
Decisión: evaluar `P(Y_t | Y_{t-1}, X_pre, ventana)` mediante un solver
multinomial regularizado. `X_pre` usa exactamente los perfiles causales del
tabular; se incluyen interacciones entre estado anterior y contexto fijo. La
distribución conjunta queda definida antes del kickoff.
Motivo: elimina la pérdida por discretización manteniendo un estado dinámico
observable, semántica Markov y disponibilidad universal pre-match.
Estado: congelada; promising_unconfirmed interno, no promocionable
Impacto en contratos/fases: abre Fase 80U exploratoria; router y Fase 81 siguen
bloqueados. Aunque pase desarrollo, la confirmación actual ya fue observada y
el resultado requerirá cohorte independiente antes de promoción.
Evidencia requerida: comparación con tabular, Markov directo y modelo
same-data sin estado anterior; regularización seleccionada antes de
confirmación, bootstrap, estabilidad y replay.
Evidencia obtenida: `C=0.003`, temperatura `1.0`. Markov mejora al directo
`0.001797` con IC positivo, pero contra el mejor comparador estático sólo
mejora `0.000431`, Brier `0.000119`, IC95% `[-0.000161, 0.001051]` y 55.17%
de ligas no negativas. No supera el gate.

DEC-100
Fecha: 2026-07-28
Problema: se agotaron régimen latente, likelihood completo, arquetipo discreto
y transición continua usando la misma confirmación; nuevas iteraciones
seleccionadas contra ella producirían sobreajuste.
Opciones: seguir buscando hasta obtener una métrica favorable; relajar gates;
o cerrar desarrollo y exigir nueva evidencia.
Decisión: cerrar la familia Markov v4 actual para promoción. Mantener Fase 79,
80S y 80U como infraestructura/candidatos shadow; baseline sigue oficial.
Ninguna nueva selección puede usar `confirmation` de Fase 74. La reapertura
requiere una cohorte no reutilizada o un cambio de fuente/estado definido antes
de observar sus outcomes.
Motivo: 80U contiene una señal pequeña frente al Markov directo, pero no
incremental frente al continuo same-data ni estable por liga.
Estado: congelada
Impacto en contratos/fases: Fase 81 permanece bloqueada; router intacto. Se
prohíbe más tuning retrospectivo sobre estos tres bloques.
Evidencia requerida: artefactos 80/80R/80T/80U, hashes, suite integral y
registro explícito de la cohorte clausurada.

DEC-101
Fecha: 2026-07-28
Problema: se requiere un reporte interpretable de 100 predicciones históricas
sin seleccionar partidos por desempeño ni reabrir tuning.
Opciones: elegir casos favorables; muestrear aleatoriamente; o tomar una cola
cronológica reproducible.
Decisión: usar los 100 partidos cronológicamente más recientes de
`confirmation`, seleccionados por fecha/match_id antes de calcular métricas.
80U y sus comparadores se reajustan sólo con `fit+selection` usando parámetros
ya congelados. El ranking se realiza después por log-loss medio de trayectoria.
Motivo: produce casos verificables, evita cherry-picking y conserva causalidad.
Estado: congelada; diagnóstico validado, no promocionable
Impacto en contratos/fases: auditoría diagnóstica Fase 80V; no reabre
promotion, no cambia router ni habilita Fase 81.
Evidencia requerida: 100 IDs únicos, predicciones pre-match, outcomes unidos
después, estadísticas reales, comparación 80U/static/baseline, reporte
ordenado, replay e hashes.
Evidencia obtenida: 100/100 IDs únicos con outcomes, 13 ligas y periodo
2026-04-11–2026-07-27. Log-loss medio: 80U `0.954427`, continuo
same-data `0.953792` y baseline `0.958411`; 80U vence al continuo en 55/100
partidos, pero pierde `0.000635` en promedio. Replay completo idéntico por
hash, ranking posterior al scoring y router intacto.

DEC-102
Fecha: 2026-07-28
Problema: se requiere una prueba interpretable de 100 partidos con la cadena
más cercana al funcionamiento final, incluyendo mercados completos y
temporales, sin presentar componentes shadow como oficiales.
Opciones: reutilizar 80U aislado; incorporar Hawkes no aprobado; o evaluar la
ruta causal congelada de Fase 45 con prior Dixon-Coles/Kalman, conservación de
intensidad, simulación Markov y calibración temporal.
Decisión: seleccionar antes del scoring los 100 partidos más recientes del
bloque `confirmation` de Fase 45. Reportar 1X2, over 2.5, BTTS,
first_half_goal y second_half_goal. Definir fiabilidad como acierto de la
decisión (`argmax` o umbral 0.5), acompañada por log-loss, Brier y calidad
probabilística normalizada. Hawkes permanece fuera.
Motivo: es la aproximación histórica disponible más completa y causal al
producto final, con pesos temporales congelados exclusivamente en validation.
Estado: congelada; diagnóstico validado, no promocionable
Impacto en contratos/fases: abre diagnóstico Fase 80W; no reabre promoción,
no cambia router y no habilita Hawkes ni Fase 81.
Evidencia requerida: 100 IDs únicos, selección previa, predicciones separadas
de scoring, cinco mercados, métricas por mercado y total, ranking posterior,
replay idéntico e hashes.
Evidencia obtenida: 100/100 partidos únicos, seis ligas y periodo
2025-12-26–2025-12-30. Fiabilidad por mercado: 1X2 `39%`, over 2.5 `52%`,
BTTS `49%`, gol 1T `60%` y gol 2T `72%`; macro total `54.4%`. La referencia
ingenua por clase mayoritaria obtiene `56.2%`, por lo que el sistema pierde
`1.8 pp`. Calidad probabilística macro Brier-normalizada `73.45%`. Replay
idéntico, hashes verificados, Hawkes excluido y router intacto.

DEC-103
Fecha: 2026-07-28
Problema: el catálogo comercial incluye corners, tarjetas, tiros y mercados
de jugador, pero la cadena vigente sólo tiene targets y evaluación promovible
para goles.
Opciones: derivar todos los mercados desde lambdas de gol; modificar el modelo
actual; o añadir motores laterales con targets, baselines y gates propios.
Decisión: conservar sin cambios Dixon-Coles/Kalman/Markov de goles y abrir
Fase 84A para mercados agregados de equipo. Se modelan conteos causales de
corners, amarillas, rojas, tiros y tiros a puerta mediante perfiles rolling y
regresión Poisson regularizada, con selección exclusivamente en `selection`.
Cada mercado se compara contra un prior liga/localía y sólo puede quedar
shadow hasta superar confirmation. Fase 84B audita identidad, minutos y
alineaciones antes de autorizar props de jugador.
Motivo: los procesos de corners, disciplina y tiros no comparten la misma
intensidad física que los goles; una extensión aditiva preserva el modelo
aceptado y evita atribuirle señales que no modela.
Estado: congelada; Fase 84A ready_for_next_phase
Impacto en contratos/fases: añade un sidecar de mercados, sin modificar el
router de goles, DEC-100, Fase 81 ni Hawkes. Los props de jugador permanecen
bloqueados hasta evidencia de Fase 84B.
Evidencia requerida: targets reconciliados, features estrictamente anteriores,
splits por partido, selección sin confirmation, comparación baseline,
probabilidades normalizadas, replay, estabilidad por liga y artefactos
completos por mercado.
Evidencia obtenida: 9,465 partidos fuente, 1,895 confirmation, 33 ligas y
18,930 observaciones orientadas. Seis de siete conteos mejoran deviance, MAE
y estabilidad; rojas falla MAE. Cuatro líneas superan simultáneamente log-loss
y Brier: corners local O4.5, corners visitante O4.5, tiros visitante O10.5 y
tarjetas 1T O1.5. Quedan habilitadas sólo en shadow. Replay idéntico, cero
features target, modelo de goles y router intactos. Props de jugador
`blocked_by_data` por identidad/minutos/alineación no reconciliados.

DEC-104
Fecha: 2026-07-28
Problema: cuatro líneas agregadas superaron los gates de Fase 84A, pero aún
no forman parte del contrato universal de predicción próxima.
Opciones: promoverlas como oficiales; recalcularlas dentro del modelo de
goles; o integrarlas como sidecar causal, explícitamente experimental y con
degradación segura.
Decisión: Fase 85 integra exclusivamente corners local O4.5, corners visitante
O4.5, tiros visitante O10.5 y tarjetas 1T O1.5. El runtime carga el artefacto
congelado de 84A, reconstruye perfiles sólo con partidos anteriores al kickoff
y excluye el `match_id` objetivo. La salida vive en
`experimental_team_markets`; si faltan artefactos o compatibilidad, devuelve
`shadow_unavailable` sin afectar la predicción oficial.
Motivo: permite probar el flujo final y acumular evidencia prospectiva sin
mezclar mercados heterogéneos ni debilitar el router protegido.
Estado: congelada; Fase 85 ready_for_prospective_shadow
Impacto en contratos/fases: añade un bloque opcional aditivo; no cambia
lambdas, probabilidades, modelo, router, Markov, Hawkes ni catálogo read-only.
Evidencia requerida: cuatro líneas exactas, causalidad de cutoff, exclusión
del objetivo, replay idéntico, equivalencia bit a bit de campos oficiales,
fallback seguro, pruebas de endpoint y hashes del artefacto de integración.
Evidencia obtenida: cuatro líneas exactas en los endpoints universal y
fixture, diez fixtures de replay, probabilidades válidas, campos oficiales
idénticos y fallback seguro. La paridad entre corpus de entrenamiento y
snapshot activo comparó 132,216 valores en 18,888 observaciones orientadas con
cero diferencias. Replay idéntico, hashes publicados y 373 pruebas aprobadas
con PostgreSQL.

DEC-105
Fecha: 2026-07-28
Problema: la integración shadow necesita confirmación realmente prospectiva
sin reconstruir predicciones después de conocer corners, tiros o tarjetas.
Opciones: reutilizar confirmation de 84A; puntuar futuros partidos
recalculando el modelo post-kickoff; o congelar modelo, baseline y metadatos
antes de cada kickoff en una cohorte append-only.
Decisión: Fase 86 crea una cohorte prospectiva de fixtures ESPN programados.
Cada registro conserva probabilidades del modelo y del prior liga/localía,
hash del modelo, hash del snapshot, timestamp de captura y kickoff. Los
outcomes permanecen inaccesibles durante colección. El gate mínimo es 500
partidos completos y 10 ligas; cada mercado se decide independientemente con
mejora de log-loss cuyo IC95% bootstrap por partido sea positivo, Brier no
degradado y al menos 70% de ligas elegibles no negativas.
Motivo: sólo una predicción materializada antes del evento prueba capacidad
operativa y evita rehacer el pasado con información posterior.
Estado: congelada; cohorte prospectiva sellada
Impacto en contratos/fases: añade comparadores congelados al sidecar y un
almacén prospectivo aislado; no modifica el router ni promueve mercados.
Evidencia requerida: respuestas raw-first, IDs únicos, timestamps anteriores
al kickoff, hashes invariantes, replay idempotente, cero outcomes leídos,
cobertura por liga/mercado y evaluación bloqueada hasta 500/10.
Evidencia obtenida: 523 predicciones de 18 ligas y 1,302 scoreboards raw-first.
Cada fixture conserva cuatro probabilidades de modelo y cuatro del baseline.
Todos los timestamps preceden al kickoff, los IDs son únicos y los hashes de
modelo/snapshot son invariantes. El replay no sobrescribe predicciones.
Outcomes y endpoints post-match permanecieron sin consultar. Nueve fixtures
sin historia mínima fueron excluidos de forma explícita. Suite integral:
376 pruebas aprobadas con PostgreSQL.

DEC-106
Fecha: 2026-07-28
Problema: los outcomes de corners y tiros existen en el boxscore final, pero
tarjetas de primera mitad requiere eventos temporales y no puede inferirse del
total del partido.
Opciones: aproximar tarjetas 1T desde el total; usar sólo summary; o persistir
summary y play-by-play paginado después del partido, reconciliando ambas
fuentes.
Decisión: Fase 87 consulta únicamente fixtures cuyo kickoff haya pasado por
al menos tres horas. Persiste summary y todas las páginas de plays antes de
parsear. Corners y tiros se toman del boxscore orientado; tarjetas 1T se
cuentan desde eventos amarillos válidos con reloj menor a 45:00. El outcome
sólo se acepta con identidad, orientación, estado final, cuatro targets
presentes y consistencia de tarjetas totales entre summary y eventos.
Motivo: evita inventar una partición temporal y conserva una fuente agregada
fuerte junto con la evidencia cronológica necesaria.
Estado: congelada; colector validado, insufficient_coverage
Impacto en contratos/fases: añade outcomes append-only y un parser post-match;
las 523 predicciones permanecen inmutables y el scoring sigue bloqueado hasta
completar la cohorte.
Evidencia requerida: raw-first summary/plays, paginación completa, identidad y
orientación, post-kickoff estricto, outcomes idempotentes, rechazos auditables,
predicciones sin cambios y cero scoring prematuro.
Evidencia obtenida: parser estricto de boxscore y plays paginados, store
append-only y gate `kickoff + 3h`. La primera ejecución halló 0 fixtures
elegibles, realizó 0 llamadas post-match, produjo 0 outcomes y conservó
idéntico el hash de las 523 predicciones. Scoring bloqueado. Suite integral:
379 pruebas aprobadas con PostgreSQL.

DEC-107
Fecha: 2026-07-28
Problema: el modelo agregado de Fase 84A predice conteos finales, pero no
representa cómo cada equipo transita hacia corners, tiros y tarjetas durante
1T y 2T.
Opciones: esperar toda la cohorte prospectiva; reutilizar el Markov de goles;
o entrenar cadenas independientes por métrica/equipo sobre ventanas causales.
Decisión: Fase 88 crea tres Markov separados (`corners`, `shots`,
`yellow_cards`) por equipo y ventanas de 15 minutos. Cada métrica usa estados
semánticos fijos de actividad baja/media/alta, emisiones empíricas y
transiciones con pooling equipo→liga/localía. Se generan distribuciones de
conteo por equipo para 1T y 2T. El desarrollo usa 181 partidos de 2024
reconciliados con 9,465 de Fase 74; el backtest usa las últimas 100
predicciones walk-forward de confirmation, seleccionadas cronológicamente
antes del scoring.
Motivo: los estados describen el proceso propio de cada mercado y conservan
la dependencia temporal que una Poisson final no puede expresar.
Estado: congelada
Impacto en contratos/fases: abre Fase 88 histórica sin eliminar Fases 86–87,
sin modificar goles/router y sin reclamar promoción antes de comparar contra
baseline de mismo dato.
Evidencia requerida: mapeo causal 2024, estados por métrica, transiciones
normalizadas, 1T/2T, 100 IDs únicos, predicción previa a outcome, métricas por
mercado, ranking posterior, replay e hashes.
Evidencia obtenida: reauditoría comercial sobre 9,646 partidos/39 ligas y 100
IDs únicos. El bloque no supera al baseline (`0.608606` frente a `0.596778`),
pero cuatro líneas mejoran log-loss y Brier: tiros visitante 2T O5.5, corners
local 2T O2.5 y tiros local 1T/2T O5.5.
Replay doble idéntico, cero eventos del partido objetivo como features y
router de goles intacto. Las otras líneas permanecen en fallback.

DEC-108
Fecha: 2026-07-28
Problema: los mercados Markov históricamente aprobados en Fase 88 no
están disponibles en el flujo universal porque la cadena final no se
serializa ni existe un adaptador runtime.
Opciones: reentrenar en cada solicitud; reemplazar el sidecar 84A; o congelar
la cadena y componerla aditivamente con el proveedor existente.
Decisión: Fase 89 serializa el Markov final de Fase 88, congela cutoff/hash y
añade sólo sus cuatro líneas a `experimental_team_markets`. Las cinco líneas
84A permanecen. El adaptador rechaza kickoffs no posteriores al cutoff y, si
el artefacto Markov no está disponible, conserva el sidecar 84A como fallback.
Motivo: evita reentrenamiento online, preserva causalidad y no convierte una
validación parcial en sustitución global.
Estado: congelada
Impacto en contratos/fases: amplía el sidecar shadow de cinco a nueve líneas;
no modifica goles, Dixon-Coles/Kalman, Fases 86–87 ni mercados oficiales.
Evidencia requerida: hash del modelo, cutoff causal, exactamente cuatro líneas
Markov, fallback 84A, paridad de campos oficiales, replay y suite integral.
Evidencia obtenida: modelo `team_market_markov.joblib` congelado y validado
por hash; diez fixtures emiten exactamente nueve líneas shadow, cuatro Markov
y cinco 84A. El fallback forzado conserva las cinco 84A, los campos oficiales
son idénticos y el replay doble coincide por hash.

DEC-109
Fecha: 2026-07-29
Problema: Fase 89 dejó líneas Markov integradas, pero carecen de cohorte
prospectiva propia; esperar a descubrir otros 500 fixtures duplicaría una
colección ya disponible antes del kickoff.
Opciones: esperar una cohorte nueva; añadir probabilidades retrospectivamente
a Fase 86; o reutilizar sólo las identidades/kickoffs futuros de Fase 86 y
emitir ahora predicciones Markov nuevas en un store separado.
Decisión: Fase 90 toma como catálogo ciego los fixtures de Fase 86 cuyo kickoff
sea posterior a la captura, ejecuta el artefacto congelado de Fase 88 y guarda
únicamente las cuatro probabilidades aprobadas y sus baselines en una cohorte
append-only independiente. No modifica Fase 86 ni consulta outcomes.
Motivo: las identidades de fixture no son targets y 520 partidos de 18 ligas
siguen siendo genuinamente prospectivos al momento del lock.
Estado: congelada
Impacto en contratos/fases: abre Fase 90 para colección y Fase 91 para
settlement; Fases 86–87 permanecen intactas y ningún mercado se vuelve oficial.
Evidencia requerida: captura anterior a kickoff, 500/10, cuatro líneas exactas,
hash único de modelo, baseline congelado, replay append-only, cero endpoints
post-match y store aislado.
Evidencia obtenida: 520 predicciones pre-kickoff/18 ligas, cuatro líneas
exactas, un hash de modelo v2, un hash de snapshot, cero outcomes/endpoints
post-match y replay append-only idéntico.

DEC-110
Fecha: 2026-07-29
Problema: los artefactos 84A/88 entrenaron `shots` desde eventos de tiro, pero
la taxonomía separa `goal`; el boxscore comercial `totalShots` incluye también
el tiro que termina en gol. El settlement prospectivo no sería semánticamente
equivalente al entrenamiento.
Opciones: aceptar la discrepancia; excluir goles también del settlement; o
versionar el target comercial como `shots + goals` y reauditar sin cambiar la
cohorte histórica congelada.
Decisión: versionar los mercados de tiros para que `shots` y
`shots_on_target` sumen los goles válidos, tanto offline como runtime. Repetir
Fases 84A/88 con los mismos splits y gates, invalidar cualquier lock creado con
la semántica anterior y congelar una cohorte prospectiva nueva.
Motivo: el usuario necesita mercados comparables con la estadística comercial
que se liquidará desde ESPN; una mejora sobre un target distinto no es
promocionable.
Estado: congelada
Impacto en contratos/fases: puede cambiar las líneas habilitadas 84A/88/89 y
obliga a rematerializar Fase 90 antes de Fase 91. Goles/router siguen intactos.
Evidencia requerida: equivalencia `shots_commercial = shot_events + goals`,
reauditoría fija de 100 partidos, artefactos/modelos versionados, cohorte
prospectiva con hash nuevo y pruebas de reconciliación play-by-play/boxscore.
Evidencia obtenida: tests unitarios de equivalencia, Fases 84A/88 reejecutadas
con versiones comerciales, 5+4 líneas calificadas, modelo hash nuevo y cohorte
v2 de 520 partidos. Parser Fase 91 reconcilia mitades contra boxscore.

DEC-111
Fecha: 2026-07-29
Problema: después del settlement se necesita una decisión automática,
reproducible e individual por mercado antes de exponer resultados oficiales.
Opciones: promover por accuracy; promover el bloque completo; o aplicar el gate
probabilístico congelado por línea.
Decisión: Fase 92 compara cada mercado Markov contra su baseline congelado con
log-loss, Brier, bootstrap pareado por partido y estabilidad por liga. Una
línea avanza sólo si el IC95% de mejora de log-loss es completamente positivo,
Brier no empeora y al menos 70% de ligas con 30 partidos son no negativas.
Motivo: evita que una línea fuerte o una métrica de clasificación oculte mala
calibración en otro mercado.
Estado: congelada; evaluador listo, scoring sellado
Impacto en contratos/fases: prepara promoción selectiva para acoplamiento de
usuario; scoring permanece sellado hasta completar outcomes de Fase 91.
Evidencia requerida: unión 1:1 predicción/outcome, 10,000 bootstraps por
partido, métricas por línea/ligas, lista de aprobadas y replay determinista.
Evidencia obtenida: motor y runner implementados, tests de aprobación/rechazo
determinista aprobados; 520/0 mantiene `insufficient_coverage` sin scoring.

DEC-112
Fecha: 2026-07-29
Problema: el sidecar expone claves técnicas y probabilidades, pero no un
contrato estable para que la interfaz explique equipo, periodo, línea, modelo
y estado de promoción.
Opciones: transformar nombres en el frontend; devolver sólo un diccionario; o
añadir una vista de mercados tipada y aditiva desde el backend.
Decisión: Fase 93 añade `user_market_view` dentro de
`experimental_team_markets`, con clave, métrica, lado, periodo, línea,
probabilidad, baseline, fuente y estado. Sigue etiquetada experimental y no
altera campos oficiales.
Motivo: evita duplicar semántica comercial en clientes y deja listo el
acoplamiento sin anticipar promoción.
Estado: congelada
Impacto en contratos/fases: adición compatible al endpoint universal/fixture;
el diccionario actual se conserva y no se habilitan apuestas oficiales.
Evidencia requerida: catálogo exacto, probabilidades equivalentes, fallback,
paridad oficial, replay y tests HTTP.
Evidencia obtenida: diez fixtures, nueve mercados exactos, vista equivalente
a ambos diccionarios, estados experimentales, salida oficial y replay
idénticos.

DEC-113
Fecha: 2026-07-29
Problema: esperar la liquidación de 520 fixtures futuros no aporta una
respuesta útil al acoplamiento actual, aunque sigue siendo la única evidencia
prospectiva independiente.
Opciones: esperar la cohorte; reutilizar los últimos 100; o ejecutar una
validación histórica semi-oficial causal sobre una cohorte distinta y
preseleccionada.
Decisión: conservar Fase 90 sólo como evidencia prospectiva, pero eliminarla
como bloqueo operativo. Fase 94 evalúa los 500 partidos `confirmation` más
recientes que no pertenecen a Fase 88 y cuya liga superó, usando sólo splits
anteriores, presencia taxonómica mínima (10 partidos, media de corners >= 2 y
tiros >= 8). Emite nueve mercados antes de actualizar el modelo, reconcilia
outcomes con play-by-play y reporta métricas por línea, familia, liga, partido
y total.
Motivo: usa ahora la evidencia disponible sin simular que los resultados eran
desconocidos ni esperar un año para probar el contrato de entrega.
Estado: congelada
Impacto en contratos/fases: habilita el paso 5 y una entrega semi-oficial; no
promueve mercados a oficiales ni reemplaza el gate prospectivo.
Evidencia requerida: 500 IDs únicos, cero solapamiento con Fase 88, 4,500
decisiones, calidad PBP determinada sin confirmation, causalidad pre-kickoff,
equivalencia de targets con play-by-play, bootstrap pareado, replay idéntico y
suite integral.

DEC-114
Fecha: 2026-07-29
Problema: la ruta hacia ventaja económica mezcla tareas ejecutables con los
datos actuales y tareas que requieren cuotas externas todavía inexistentes.
Opciones: bloquear todo hasta conseguir odds; simular cuotas; o avanzar sólo
calibración probabilística y control de dependencia con la cohorte histórica
ya auditada.
Decisión: Fases 95–96 usan exclusivamente las 500 predicciones y outcomes de
Fase 94. Fase 95 aplica calibración Platt expansiva por mercado, con warm-up de
100 partidos y ajuste estrictamente anterior a cada predicción evaluada. Fase
96 cuantifica dependencia de outcomes/probabilidades, distribución de aciertos
conjuntos y genera una política shadow de exposición: máximo una selección por
componente altamente correlacionado y tres por partido. No calcula ROI, CLV,
Kelly, stakes ni promociona mercados.
Motivo: completa preparación probabilística y de riesgo sin fabricar precios
de mercado ni presentar una ventaja económica no observada.
Estado: congelada
Impacto en contratos/fases: añade calibrador y política shadow aditivos; no
modifica Dixon-Coles/Kalman, el router oficial, Fases 90–93 ni las
probabilidades históricas fuente.
Evidencia requerida: ajuste prequential, 400 partidos evaluables, métricas
raw/calibradas por línea, ECE, bootstrap por partido, matrices de dependencia,
auditoría de 9/500, replay idéntico, artefactos completos y regresión integral.

DEC-115
Fecha: 2026-07-29
Problema: el flujo universal y `user_market_view` están listos, pero no existe
una interfaz conversacional para observación controlada por usuarios internos.
Opciones: integrar lógica del modelo dentro del bot; exponer un webhook
público desde el inicio; o crear un adaptador privado que consuma la API
DIKAMAHA vigente mediante long polling.
Decisión: Fase 97 implementa un bot Telegram privado y aditivo. Usa long
polling con offset monotónico, allowlist de usuarios, chats privados, rate
limit, timeout y reintentos. Los comandos `/partido` y `/predict` delegan en
los endpoints `/v1/predict/fixture` y `/v1/predict/upcoming`; no recalculan ni
duplican modelos. La respuesta etiqueta baseline oficial y mercados shadow
experimentales. Token, API key y allowlist se leen sólo desde entorno.
Motivo: permite pruebas reales de interfaz y latencia conservando una sola
fuente de verdad, sin abrir apuestas, persistencia financiera ni promoción.
Estado: congelada
Impacto en contratos/fases: añade un proceso cliente separado; no modifica
router, probabilidades, snapshots, Dixon-Coles/Kalman/Markov ni los endpoints
existentes.
Evidencia requerida: token ausente de código/logs/artefactos, allowlist,
deduplicación, manejo de errores Telegram/DIKAMAHA, paridad de payload,
mensajes bajo límite, replay con transportes falsos y regresión integral.

DEC-116
Fecha: 2026-07-29
Problema: la interfaz Telegram exige IDs y no permite explorar de forma
compacta mercados por periodo, partidos históricos, play-by-play,
estadísticas por mitad ni perfiles de jugadores.
Opciones: consultar ESPN directamente desde el bot; copiar payloads crudos a
Telegram; o añadir un explorador read-only detrás de la API DIKAMAHA y mantener
el bot como cliente de presentación.
Decisión: abrir Fase 98 con un explorador ESPN raw-first, cacheado y de sólo
lectura. La navegación será liga→fecha→partido para play-by-play y
estadísticas, y liga→equipo→jugador para perfiles. Telegram usará botones
inline paginados, búsqueda de equipo por prefijo/texto tras envío y vistas
compactas por 1T, 2T y total. Los mercados de predicción se agrupan por
periodo y conservan su estado oficial/shadow.
Motivo: minimiza tiempo y escritura manual sin duplicar inferencia, filtrar
secretos ni presentar datos experimentales como oficiales.
Estado: congelada
Impacto en contratos/fases: añade endpoints read-only y menús compatibles; no
modifica probabilidades, modelos, router, snapshots, gates de promoción ni
persistencia financiera.
Evidencia requerida: caché raw-first, paginación completa de plays, identidad
de liga/partido/equipo, separación 1T/2T/total, búsqueda acotada, mensajes
bajo límite, callbacks compactos, fallback de datos ausentes, smoke ESPN real,
tests y regresión integral.
Evidencia obtenida: smoke real `mex.1` con dos fixtures; Necaxa–Monterrey
produjo 1,183 plays en cuatro páginas, 101 eventos clave, estadísticas
reconciliadas y boxscore de ambos equipos. Búsqueda `Monter` devolvió una
coincidencia, roster de 33 jugadores y perfil con 15 acumulados. API y bot
reiniciados, 33 pruebas dirigidas y 410 de regresión integral aprobadas, con
router intacto.
La revisión visual v1.1 añadió tablas monoespaciadas y tarjetas a todas las
vistas, mantuvo mensajes bajo 3,900 caracteres y cerró con 37 pruebas
dirigidas y 414 de regresión integral.
La revisión v1.2 eliminó la liga implícita del acceso a próximos partidos:
añadió rutas global, por liga y por fecha, filtros `leagues/date` en la API y
consulta paralela tolerante a fallos parciales. El smoke global devolvió cuatro
ligas activas y el 1 de agosto seis ligas distintas, incluida Liga MX. Cerró
con 417 pruebas aprobadas y 7 integraciones PostgreSQL opt-in omitidas.
La revisión visual v1.3 sustituyó las etiquetas local/visitante por nombres
reales del fixture en 1X2, mercados por equipo, conteos esperados,
estadísticas y boxscore. Si el proveedor omite el nombre, el fallback visible
es Equipo 1/Equipo 2. La regresión permaneció en 417 pruebas aprobadas.

DEC-117
Fecha: 2026-07-29
Problema: el explorador mostró un gol para Deportivo Riestra–Boca Juniors
aunque el marcador oficial fue 3–0. ESPN entregó los goles de los minutos 7 y
23 como `goal---header` y el del 38 como `goal`; el contrato visual sólo
contaba el tipo exacto `goal`.
Opciones: tomar siempre el marcador sin desglose temporal; añadir el alias
sólo en Telegram; o ampliar la taxonomía común y auditar PBP contra header.
Decisión: `goal---header` y variantes de gol se normalizan mediante la
taxonomía ESPN compartida. El explorador conserva el desglose temporal,
expone el marcador del header y calcula `score_reconciled`.
Motivo: corrige la causa semántica sin imputar goles ni perder la distinción
por mitades y convierte una discrepancia silenciosa en un fallo observable.
Estado: congelada
Impacto en contratos/fases: corrección aditiva de taxonomía y explorador de
Fase 98; no modifica features pre-match, modelos, probabilidades ni router.
Evidencia requerida: Riestra 3–0 Boca con tres goles 1T, marcador 3–0,
`score_reconciled=true`, pruebas de alias y regresión integral.
Evidencia obtenida: ESPN summary y scoreboard reportan 3–0; Core PBP contiene
goles 7, 23 y 38; el explorador devuelve 3+0 goles y ambas reconciliaciones
verdaderas. 418 pruebas aprobadas y 7 PostgreSQL opt-in omitidas.

DEC-118
Fecha: 2026-07-29
Problema: se requiere preparar una segunda interfaz conversacional para
Discord sin duplicar inferencia ni obligar al usuario a escribir IDs.
Opciones: comandos de texto con message-content intent; endpoint público de
interacciones HTTP; o Gateway Discord con slash commands y componentes.
Decisión: abrir Fase 99 con un adaptador privado sobre `discord.py`, slash
commands, selectores y botones. Consumirá exclusivamente la API DIKAMAHA y
reutilizará su contrato de ligas, fechas, fixtures, predicción y explorador.
Las respuestas de usuario serán ephemeral por defecto y el acceso podrá
restringirse por usuario y servidor desde entorno.
Motivo: los componentes nativos minimizan escritura, no requieren el intent
privilegiado de contenido y permiten operar localmente sin exponer un webhook.
Estado: congelada
Impacto en contratos/fases: cliente aditivo; no modifica router, modelos,
probabilidades, snapshots, gates, settlement ni política económica.
Evidencia requerida: token fuera del repositorio, allowlists, timeout y manejo
de error, componentes bajo límites Discord, paridad con API, tests sin red y
smoke real sólo después de recibir credenciales.
Evidencia obtenida: Gateway conectado, guild visible, seis comandos
sincronizados, navegación completa equivalente a Telegram y 425 pruebas.

DEC-119
Fecha: 2026-07-29
Problema: Discord devuelve 403 `Missing Access` si el guild configurado aún no
contiene a la aplicación.
Decisión: capturar el rechazo durante `setup_hook`, registrar sólo el estado
sanitizado y sincronizar comandos globales como respaldo. El Gateway queda
conectado mientras se completa la invitación al servidor.
Evidencia inicial: identidad Discord HTTP 200, Gateway conectado y
`guild_count=0`. Tras la invitación, `guild_count=1`, el guild configurado es
visible y los seis comandos responden HTTP 200 en el catálogo del servidor.

DEC-120
Fecha: 2026-07-29
Problema: la documentación ESPN ofrece contexto de fixture, equipo, jugador,
liga, live y odds que aún no tiene una ruta de incorporación controlada.
Opciones: añadir endpoints ad hoc dentro de los bots; usar todos los campos
como features; o versionar un programa raw-first que separe presentación,
candidatos causales, live/settlement y datos financieros.
Decisión: abrir Fase 100 con objetivos 100A–100F. Los bots sólo consumirán
nuevos datos a través de DIKAMAHA. Todo candidato de modelo exige snapshot
previo al kickoff y evaluación independiente; live sirve para UI/settlement;
odds y noticias quedan aisladas de promoción y staking.
Motivo: maximiza valor visible y de investigación sin contaminar el contrato
pre-match ni reinterpretar datos no timestamped.
Estado: congelada para ejecución
Impacto en contratos/fases: añade adaptadores y contratos read-only futuros;
no cambia router, modelo oficial, Markov/Hawkes, probabilidades ni Fase 83.
Evidencia requerida: raw-first, hashes, timestamps, identidad, cobertura por
liga, causality audit, tests y artefactos de cada subfase.

DEC-121
Fecha: 2026-07-29
Problema: el canal Telegram `@viewtofuture` necesita publicaciones automáticas
pre-match y resultados sin duplicados, recalcular predicciones ni adelantar
datos post-partido.
Opciones: usar cron con mensajes sin estado; incorporar publicaciones al long
polling conversacional; o crear un worker idempotente con ledger propio.
Decisión: abrir Fase 101 con un publicador independiente. A las 09:00
`America/Mexico_City` congela las predicciones de los partidos del día
siguiente y publica un resumen. Cada tarjeta individual se publica a T-90m.
Los resultados se publican sólo desde `kickoff + 3h`, con estado final,
marcador y play-by-play reconciliados. El destino es `@viewtofuture`.
Motivo: separa difusión de conversación, conserva causalidad y permite replay
sin mensajes duplicados.
Estado: congelada para ejecución
Impacto en contratos/fases: añade persistencia y transporte de publicaciones;
no cambia modelos, probabilidades, router, promoción ni política económica.
Evidencia requerida: predicción anterior al kickoff, hash inmutable,
idempotencia por mensaje, zona horaria correcta, cero envíos en pruebas,
rechazo de resultados no finales/no reconciliados y smoke de acceso al canal.
Evidencia obtenida: Telegram confirmó canal, administrador y permiso de
publicación. Diez predicciones fueron congeladas para 2026-07-30, un resumen
real fue confirmado y el replay produjo cero mensajes nuevos. Once pruebas
dirigidas aprobaron congelación, idempotencia, T-90, settlement +3h,
reconciliación y límite de mensaje.

DEC-122
Fecha: 2026-07-29
Problema: la entrega diaria necesita una edición completa y otra abreviada,
mejor legibilidad y predicciones disponibles desde el propio aviso, no T-90.
Opciones: mantener T-90; duplicar workers; o añadir un modo validado al mismo
publicador idempotente.
Decisión: Fase 101 v1.1 incorpora el interruptor `full|lite`. `full` conserva
todos los fixtures disponibles del día siguiente; `lite` selecciona los tres
más próximos por kickoff. A las 09:00 se publica primero la agenda y, debajo,
una tarjeta por partido. Los escudos oficiales ESPN son presentación
opcional y nunca entran al modelo. Si faltan, la tarjeta cae a HTML legible.
Motivo: reduce el tiempo hasta la información útil sin duplicar inferencia ni
alterar causalidad, modelos o settlement.
Estado: congelada
Impacto en contratos/fases: reemplaza exclusivamente la ventana T-90 de
DEC-121; mantiene su congelación, replay, resultados +3h y reconciliación.
Evidencia requerida: tests full/lite, exactamente tres fixtures en lite,
tarjetas en el ciclo diario, fallback sin logos e idempotencia.
Evidencia obtenida: cuatro pruebas dirigidas aprobaron. Smokes reales contra
la API local descubrieron 10 fixtures en `full` y exactamente 3 en `lite`,
con una agenda y una tarjeta inmediata por fixture, sin llamadas a Telegram.

DEC-123
Fecha: 2026-07-29
Problema: la difusión del canal no debe depender de automatizaciones de Codex
ni de que un operador levante la API manualmente.
Opciones: conservar cron de Codex; exigir dos procesos externos; o entregar un
servicio autocontenido del repositorio que supervise API y publicador.
Decisión: retirar la automatización de Codex y versionar Fase 101 v1.2 con un
entrypoint permanente. El servicio reutiliza una API DIKAMAHA saludable o
inicia una instancia `operational_readonly`, ejecuta el ledger idempotente cada
cinco minutos y detiene únicamente los procesos que él mismo creó.
Motivo: acerca la operación a producción, concentra ciclo de vida y conserva
la separación entre DIKAMAHA, publicación y transporte Telegram.
Estado: congelada
Impacto en contratos/fases: despliegue operativo aditivo; no cambia modelos,
probabilidades, cutoff, selección lite, settlement ni reconciliación.
Evidencia requerida: pruebas de ciclo de vida, smoke continuo, modo lite leído
desde `.env`, cierre limpio, ausencia de automatización Codex y replay sin
duplicados.
Evidencia obtenida: 13 pruebas dirigidas aprobaron. El servicio inició su API
read-only y el worker continuo como hijos supervisados, leyó `lite`, completó
un replay con cero duplicados y permaneció activo. La automatización Codex y
sus archivos operativos quedaron ausentes.

DEC-124
Fecha: 2026-07-29
Problema: el aviso del canal sólo muestra 1X2 y goles, aunque el contrato de
usuario contiene líneas adicionales por equipo y periodo.
Opciones: saturar el caption de los escudos; omitir mercados shadow; o publicar
un bloque separado inmediatamente después de cada tarjeta principal.
Decisión: Fase 101 v1.3 publicará, por partido, una tarjeta visual oficial y un
mensaje separado con todas las filas válidas de `user_market_view`, agrupadas
en 1T, 2T y partido completo. Cada fila muestra equipo, línea, probabilidad del
modelo y baseline. El bloque conserva la etiqueta
`experimental_shadow_not_promoted` y tiene idempotencia independiente.
Motivo: mantiene escudos y legibilidad sin exceder el límite de captions ni
presentar mercados experimentales como apuestas oficiales.
Estado: congelada
Impacto en contratos/fases: presentación aditiva; no promueve mercados, no
altera probabilidades, selección lite, router, cutoff ni settlement.
Evidencia requerida: cobertura de todas las filas disponibles, nombres reales
de equipos, separación por periodo, límites Telegram, replay y smoke real.
Evidencia obtenida: 13 pruebas dirigidas aprobaron. La simulación real `lite`
publicó una agenda, tres tarjetas y tres bloques de mercados; cada uno incluyó
las nueve filas disponibles, para 27 predicciones shadow visibles. El servicio
se reinició con la versión nueva y su replay produjo cero duplicados.

DEC-125
Fecha: 2026-07-29
Problema: las líneas fijas de Fases 84A/88 no representan la distribución
individual de resultados posibles de cada equipo.
Opciones: entrenar un clasificador por línea; interpolar las nueve
probabilidades actuales; o derivar todas las líneas desde la distribución
conjunta causal ya producida por Markov.
Decisión: abrir Fase 102. La cadena específica de equipo genera la PMF de
conteos 1T/2T/total; la misma cadena sin identidad de equipo genera el baseline
liga × localía. Over/under se derivan por CDF y deben ser complementarios y
monotónicos. La API expone escaleras completas y los bots sólo los escenarios
no triviales más probables. Todo permanece shadow.
Motivo: una sola distribución coherente evita clasificadores contradictorios,
personaliza por equipo y reutiliza el estado causal auditado.
Estado: congelada para ejecución
Impacto en contratos/fases: salida aditiva sobre Fase 93; no cambia líneas
promovidas, router, modelo oficial de goles, cutoff ni política económica.
Evidencia requerida: PMF, monotonicidad, complementos, causalidad, replay,
paridad oficial, cobertura por equipo/periodo y pruebas de interfaz.
Evidencia obtenida: 47 pruebas dirigidas aprobaron. Tres próximos partidos
reales devolvieron 18 PMF, 218 líneas y seis escenarios específicos cada uno;
PMF normalizadas, colas monotónicas, complementos exactos, cutoff causal y
router oficial intacto.
Adenda de cobertura: tiros a puerta se incorporó desde la distribución
negativo-binomial de Fase 84A para cada equipo y total de partido. El contrato
final contiene 21 PMF y 269 líneas; no se imputaron mitades sin modelo.

DEC-126
Fecha: 2026-07-29
Problema: Fase 102 produce escaleras coherentes, pero todavía no demuestra que
una línea elegida antes de confirmación mejore su baseline fuera de muestra.
Opciones: evaluar las 269 líneas sobre el mismo bloque; elegir líneas con
confirmación; o separar selección y confirmación con una sola apertura ciega.
Decisión: abrir Fase 103. Las líneas candidatas se elegirán exclusivamente en
`selection`, como máximo una por equipo lógico × métrica × periodo. El bloque
`confirmation` permanecerá sellado hasta congelar el manifiesto de selección.
El gate usa log-loss, Brier, calibración, bootstrap pareado de 10,000 remuestras
por partido y estabilidad por liga. Tiros a puerta tendrá evaluación separada
si su partición de ajuste impide conservar independencia.
Motivo: evita cherry-picking entre líneas correlacionadas y convierte la
escalera de Fase 102 en una prueba falsable de valor incremental.
Estado: congelada para ejecución
Impacto en contratos/fases: sólo evaluación histórica; no cambia API, bots,
router, probabilidades vigentes ni promoción oficial.
Evidencia requerida: manifiesto con hash previo al scoring confirmatorio,
predicciones causalmente anteriores al outcome, métricas de selección y
confirmación, IC95%, estabilidad por liga, auditoría de cobertura y hashes.
Evidencia obtenida: 9,646 partidos; 1,891 en selección y 1,895 en
confirmación. Se evaluaron 218 líneas Markov, se congelaron 18 candidatos con
SHA-256 previo a confirmación y 12 aprobaron el gate completo. Seis líneas con
mejora media fueron rechazadas por estabilidad entre ligas. Tiros a puerta
permanece pendiente de evaluación anidada independiente porque Fase 84A ya usa
`selection` para elegir sus hiperparámetros.

DEC-127
Fecha: 2026-07-29
Problema: el ledger de Fase 101 conserva predicciones congeladas antes de Fase
102; por ello los avisos actuales muestran nueve líneas fijas aunque el runtime
ya produce recomendaciones distribucionales variables.
Opciones: sobrescribir predicciones congeladas; esperar a fixtures nuevos; o
añadir un snapshot append-only versionado para el contrato distribucional.
Decisión: Fase 101 v1.4 añadirá snapshots de mercado por
`fixture × contract_version`. Sólo se recalculan antes del kickoff cuando la
predicción original no contiene `recommended_market_view`. El snapshot se
persiste antes de enviar y la publicación usa una clave idempotente versionada.
Los avisos muestran exclusivamente recomendaciones variables cuando existen;
la vista fija permanece como fallback explícito si el runtime no está
disponible.
Motivo: corrige avisos ya congelados sin destruir evidencia causal ni alterar
probabilidades oficiales, y permite evolucionar el contrato de presentación.
Estado: congelada para ejecución
Impacto en contratos/fases: persistencia y presentación aditivas en Telegram;
no cambia Fase 102/103, router, modelos, cutoff, settlement ni predicción
oficial.
Evidencia requerida: snapshot anterior al kickoff, hash inmutable, ninguna
sobrescritura, claves v2 idempotentes, recomendaciones variables visibles,
fallback seguro, replay y prueba real en modo lite.
Evidencia obtenida: 15 pruebas dirigidas aprobaron. Los tres fixtures lite
antiguos conservaron intacta su predicción original y recibieron snapshots
`phase102_v1` con 21 grupos distribucionales y seis recomendaciones variables
cada uno. Telegram confirmó las publicaciones versionadas 29, 30 y 31; el
servicio continuo quedó activo y el replay no duplica esas claves.

DEC-128
Fecha: 2026-07-29
Problema: elegir la dirección de mayor probabilidad favorece líneas triviales,
por ejemplo under 16.5 tiros, y no permite leer la pendiente de riesgo de cada
mercado.
Opciones: subir el umbral mínimo de probabilidad; conservar una sola línea; o
publicar una rejilla local acotada alrededor de la mediana de cada PMF.
Decisión: Fase 102 v1.1 añadirá `bounded_market_grid_view`. Toda línea visible
queda entre 1.5 y 9.5 inclusive. Por cada
`equipo × métrica × periodo` se eligen tres líneas consecutivas alrededor de
la probabilidad over más cercana a 50% y se muestran simultáneamente over,
under y sus baselines. Los avisos Telegram priorizan esta rejilla, con bloques
separados para 1T, 2T y total.
Motivo: evita probabilidades altas por construcción, muestra sensibilidad a la
línea y conserva coherencia exacta `over + under = 1`.
Estado: congelada para ejecución
Impacto en contratos/fases: selección y presentación aditivas; no reajusta
PMF, estados, transiciones, router, promociones ni settlement.
Evidencia requerida: tres líneas por grupo, límites 1.5/9.5, complemento
exacto, orden monotónico, nombres de equipo, ambos tiempos, mensajes bajo
3,900 caracteres, replay y smoke real.
Evidencia obtenida: 15 pruebas dirigidas aprobaron. Cada uno de tres fixtures
reales produjo 21 grupos, incluidos 12 de 1T/2T, con tres líneas por grupo,
límites 1.5–9.5 y complementos exactos. Telegram confirmó mensajes 32–34; sus
tamaños fueron 3,304, 3,336 y 3,114 caracteres. El servicio continuo permanece
activo con snapshots `phase102_v2_bounded_grid`.

DEC-129
Fecha: 2026-07-29
Problema: una sola línea extensa por equipo dificulta comparar las tres líneas
over/under y obliga a leer texto horizontal denso en móvil.
Opciones: abreviar aún más una sola publicación; eliminar baselines; o dividir
la entrega en tarjetas visuales por periodo con tablas compactas.
Decisión: Fase 101 v1.5 publica una tarjeta independiente para 1T, 2T y total.
Cada tarjeta agrupa por equipo y usa tablas monoespaciadas por métrica: líneas
en columnas y filas para Más, Menos y referencia baseline. El contenido
probabilístico y snapshot v2 permanecen idénticos; sólo cambia layout y clave
de publicación.
Motivo: maximiza legibilidad móvil sin eliminar probabilidades, comparación
contra baseline ni trazabilidad.
Estado: congelada para ejecución
Impacto en contratos/fases: presentación Telegram aditiva; no cambia modelos,
PMF, selección, snapshots, router, cutoff ni settlement.
Evidencia requerida: tres tarjetas ordenadas, tablas válidas, nombres largos
legibles, límite Telegram, replay idempotente y smoke real.
Evidencia obtenida: 16 pruebas dirigidas aprobaron. Tres fixtures reales
publicaron nueve tarjetas visuales: 1T, 2T y total por fixture, confirmadas por
Telegram como mensajes 35–43. Las tarjetas reales midieron 1,047–1,470
caracteres y el replay devolvió cero publicaciones nuevas.

DEC-130
Fecha: 2026-07-29
Problema: tres tarjetas consecutivas por partido multiplican los avisos y el
lector puede perder su asociación con el fixture dentro de un lote lite.
Opciones: mantener tres mensajes; condensar las tablas; o fusionar tarjetas
del mismo fixture respetando el límite Telegram.
Decisión: Fase 101 v1.6 concatena las tarjetas de 1T, 2T y total en un único
dashboard por partido cuando cabe bajo 3,900 caracteres. Cada sección conserva
el encabezado completo con equipos y periodo. Sólo si el dashboard excede el
límite se conservan las tarjetas separadas, que ya son autoidentificables.
Motivo: reduce el lote lite de nueve a tres mensajes de mercados sin perder
contexto, tablas ni distinción temporal.
Estado: congelada para ejecución
Impacto en contratos/fases: sólo layout y clave idempotente; snapshots, PMF,
probabilidades, router y settlement permanecen intactos.
Evidencia requerida: dashboard por partido, cabecera repetida por sección,
límite Telegram, fallback seguro, replay y smoke real.
Evidencia obtenida: 16 pruebas dirigidas aprobaron. Los tres dashboards reales
midieron 3,510, 3,614 y 3,632 caracteres, bajo el límite de Telegram, y
redujeron la difusión lite de nueve a tres mensajes de mercados. Telegram
confirmó los dashboards 44–46; el replay generó cero mensajes adicionales.

DEC-131
Fecha: 2026-07-29
Problema: las probabilidades por equipo son legibles, pero falta un pronóstico
total coherente por mercado al final de cada fixture.
Opciones: sumar probabilidades individuales; elegir arbitrariamente una línea;
o construir una PMF total desde las distribuciones de ambos equipos.
Decisión: Fase 102 v1.2 añadirá `global_market_view`. Para corners, tiros y
tarjetas, la PMF total se deriva por convolución de las PMF local y visitante,
con la hipótesis visible de independencia condicional. Para tiros a puerta se
reutiliza la PMF total negativo-binomial existente. El dashboard muestra una
línea total informativa por mercado, con over, under, baseline y media.
Motivo: agrega conteos de manera probabilísticamente coherente, sin sumar
probabilidades ni inventar un nuevo modelo.
Estado: congelada para ejecución
Impacto en contratos/fases: salida shadow aditiva; no reentrena PMF, no
promueve mercados, no modifica router, cutoff, snapshots ni settlement.
Evidencia requerida: masa normalizada, media total coherente, complementos,
provenance de agregación, límite Telegram, replay y smoke de flujo completo.
Evidencia obtenida: 12 pruebas dirigidas aprobaron. Los cuatro totales por
fixture tienen PMF normalizada y media igual a la esperanza de su masa; corners,
tiros y tarjetas registran convolución condicional y tiros a puerta PMF directa.
El dashboard compacto quedó bajo 3,900 caracteres. Una ejecución limpia con
ledger aislado congeló 3 fixtures, publicó 1 agenda, 3 tarjetas y 3 dashboards
globales; su replay publicó 0 mensajes.

DEC-132
Fecha: 2026-07-29
Problema: la auditoría observó totales redundantes: las emisiones Markov
globales se parecen entre fixtures y la restricción de línea 9.5 satura tiros
totales cerca de 100%.
Opciones: conservar convolución; ampliar líneas visibles; o usar el modelo de
conteos histórico directo para totales y presentar distribución resumida.
Decisión: Fase 102 v1.3 reemplaza `global_market_view` por PMF total
negativo-binomial derivada de las intensidades pre-match Fase 84A de cada
equipo, rival y liga, con dispersión empírica de la misma cohorte. El bloque
global deja de forzar una línea O/U y muestra media, moda, intervalo central
60% y media baseline por mercado.
Motivo: usa los conteos históricos reales disponibles, evita saturación visual
y expresa incertidumbre de un total de manera más útil.
Estado: congelada para ejecución
Impacto en contratos/fases: reemplaza sólo resumen global shadow; no altera
PMF por equipo, router, mercado aprobado, cutoff, snapshots ni settlement.
Evidencia requerida: inputs históricos causales, PMF normalizada, medias y
rangos verificables, variación entre fixtures, mensaje bajo límite y replay.
Evidencia obtenida: 16 pruebas dirigidas aprobaron. Los tres fixtures reales
mostraron variación directa: corners 13.87/14.24/11.37, tarjetas
5.82/5.98/4.40 y tiros a puerta 6.61/7.31/6.74. Cada PMF normaliza a uno y el
dashboard publicó los totales directos en mensajes Telegram 67–69. El replay
no generó publicaciones nuevas.

DEC-133
Fecha: 2026-07-29
Problema: la tarjeta oficial de Telegram etiqueta Dixon-Coles + Kalman, pero
el router universal todavía calcula un Poisson estructural simplificado y
declara correctamente `kalman_used=false` y `markov_used=false`.
Opciones: cambiar sólo la etiqueta; activar toda la cadena sin evidencia; o
versionar un router de goles con gates por componente y fallback explícito.
Decisión: abrir Fase 104. El candidato oficial ajustará Dixon-Coles sobre
historia causal, inicializará y actualizará Kalman sólo con partidos
anteriores al kickoff y calculará mercados desde la matriz corregida de
marcadores. Se comparará walk-forward contra el baseline universal actual.
Cada mercado se promoverá individualmente sólo si mejora log-loss y Brier con
IC95% pareado positivo; los mercados que no pasen conservarán exactamente el
baseline. Markov de goles se integrará únicamente como sidecar de trayectoria
shadow porque DEC-100 prohíbe su promoción sin nueva evidencia independiente.
Los mercados de equipo mantienen sus gates individuales de Fases 92/103.
Motivo: integra la arquitectura real sin convertir una etiqueta de interfaz en
una afirmación estadística falsa ni degradar mercados ya funcionales.
Estado: congelada; promoción selectiva ejecutada
Impacto en contratos/fases: añade `official_goal_router_v2` con procedencia por
mercado, rollback y compatibilidad de salida; no modifica outcomes históricos,
Hawkes, settlement ni predicciones ya congeladas.
Evidencia requerida: causalidad por cutoff, Dixon-Coles MLE convergente,
replay Kalman por buckets temporales, matriz normalizada, backtest
walk-forward, bootstrap por partido, estabilidad por liga, fallback exacto,
paridad API/Telegram y suite de regresión.
Evidencia obtenida: 500 partidos de 31 ligas y 10,000 bootstraps por mercado.
1X2 mejora log-loss `1.195493→1.066077`, Brier `0.710985→0.644897`,
IC95% `[0.068550, 0.195753]` y estabilidad `77.42%`. Over 2.5 mejora
log-loss `1.023257→0.722363`, Brier `0.311618→0.258740`, IC95%
`[0.184474, 0.426642]` y estabilidad `80.65%`. Ambos marcan mejora en
promedio, pero falla estabilidad (`61.29%`) y conserva baseline. La cadena se
activó sólo para 1X2/over 2.5 con fallback completo; Markov de goles sigue
shadow. Pruebas dirigidas: 45 aprobadas.

DEC-134
Fecha: 2026-07-29
Problema: se necesitaba una prueba histórica completa de 1,000 partidos con
la cadena actualmente desplegada y comparación por motor, sin confundir el
sidecar de mercados con el router de goles.
Decisión: ejecutar Fase 105 sobre los 1,000 partidos más recientes con
predicción causal disponible simultáneamente para la cadena selectiva de
goles, Fase 84A y Markov temporal Fase 88. Reportar 12,000 decisiones,
confianza de la decisión, log-loss, Brier y tablas de extremos por partido.
BTTS se puntúa como baseline porque no fue promovido en Fase 104.
Estado: congelada; diagnóstico histórico
Impacto en contratos/fases: no cambia router, promoción ni predicciones
congeladas. Los resultados no se presentan como confirmación prospectiva.
Evidencia obtenida: 1,000 partidos de 21 ligas, 12,000 mercados, 4 partidos
con 12/12, cero partidos con 0/12, confianza global `61.00%`, accuracy global
`60.11%`, log-loss `0.707251` y Brier `0.270837`. La cadena oficial obtuvo
`50.55%` en sus 2,000 decisiones; aggregate `64.28%`; Markov temporal
`61.80%`. Todos los contextos fueron estrictamente anteriores al kickoff y
el PBP fue reconciliado.

DEC-135
Fecha: 2026-07-29
Problema: Fase 105 detectó sobreconfianza severa en BTTS y una línea Markov
que degrada simultáneamente log-loss y Brier frente a su baseline.
Decisión: abrir Fase 106. Se descartó Platt porque el coeficiente aprendido
invertía el ranking de la señal estructural. BTTS se repara mediante una tasa
causal por liga, contraída hacia `0.50`; el shrinkage `500` se seleccionó sólo
con el warm-up de 200 partidos y cada objetivo usa exclusivamente historia
anterior a su kickoff. Sólo se activa si mejora log-loss, Brier y ECE, el
IC95% pareado es positivo y al menos 70% de ligas no degradan. La línea
`home_corners_second_half_over_2_5` vuelve inmediatamente a su baseline por
política conservadora; no se promociona ningún candidato nuevo. Las líneas
que pierden accuracy pero mejoran log-loss/Brier permanecen sin cambios.
Estado: congelada; reparación selectiva integrada
Impacto en contratos/fases: versiona procedencia de BTTS y fallback selectivo
del sidecar; mantiene Dixon-Coles/Kalman, Markov de goles, settlement y
predicciones congeladas.
Evidencia obtenida: 800 predicciones prequential. Log-loss
`0.874028→0.691966`, Brier `0.302916→0.249410`, ECE
`0.185686→0.016445`, IC95% pareado `[0.129208, 0.237257]` y 19/21 ligas
no degradadas (`90.48%`). El replay completo de 1,000 partidos mejoró el
resultado global a accuracy `60.29%`, log-loss `0.692561` y Brier
`0.266393`. Fallback exacto y 38 pruebas dirigidas aprobados.

DEC-136
Fecha: 2026-07-29
Problema: la cadena predictiva ya es operativa, pero la entrega actual expone
terminología interna, el contenedor no incluye todos los artefactos del runtime
vigente y aún no existe evidencia de comportamiento bajo concurrencia y fallos
propia de un despliegue con usuarios reales.
Opciones: desplegar directamente; separar API y worker desde el primer día; o
crear una fase de preproducción autocontenida para Railway con un único
supervisor, volumen persistente e interfaces públicas sanitizadas.
Decisión: abrir Fase 107. Railway ejecutará un supervisor del repositorio que
levanta la API en `0.0.0.0:$PORT` y el publicador Telegram, con health/readiness,
límites de concurrencia, rate limit, timeouts, retry, logs JSON, cierre limpio
y ledger SQLite alojado en un volumen persistente. Telegram conservará las
probabilidades y distinciones por mercado/periodo, pero retirará terminología
interna como proveedor, experimental, shadow, baseline y nombres de motores.
El gate incluirá al menos 100 solicitudes concurrentes, degradación de
dependencias, payload inválido/grande, autenticación, timeout, idempotencia,
reinicio y ausencia de secretos en logs.
Motivo: reducir superficie operativa para las primeras pruebas sin alterar
modelos, probabilidades, snapshots ni settlement.
Estado: congelada; preproducción validada
Impacto en contratos/fases: presentación pública, empaquetado y operación;
no cambia el contrato matemático ni promueve mercados nuevos.
Evidencia obtenida: imagen de 190.6 MB construida y ejecutada como usuario
`app`; health/readiness saludables, auth `401/200`, predicción real y cierre
SIGTERM código 0. La prueba de 100 solicitudes produjo 16 respuestas `200` y
84 `503` con backpressure, cero timeouts y p95 `2.892 s`. Contratos inválidos
y grandes devolvieron `422/413`; 48 pruebas dirigidas y la suite completa con
441 aprobadas/8 omitidas pasaron. El dry-run Telegram produjo una agenda, tres
tarjetas y tres dashboards. Runbook, volumen y rollback documentados.

DEC-137
Fecha: 2026-07-29
Problema: el workspace acumuló más de 10 GB de evidencia, cachés y entornos
locales; el snapshot activo plano excedía el límite habitual de GitHub aunque
el servicio sólo requiere una fracción de los artefactos.
Opciones: borrar toda la historia; subirla mediante Git LFS; o conservar la
evidencia local y versionar únicamente el runtime mínimo reproducible.
Decisión: conservar la evidencia científica fuera de Git, eliminar sólo
material regenerable confirmado y distribuir el snapshot activo como gzip
validado contra el hash lógico del JSON original. Docker usa una lista
explícita de contratos y modelos.
Motivo: reducir el repositorio y la imagen sin perder trazabilidad ni alterar
predicciones.
Estado: congelada; implementación Fase 108
Impacto en contratos/fases: sólo empaquetado y retención; no cambia datos,
modelos, probabilidades, router ni settlement.
Evidencia requerida: hash y conteo del snapshot, pruebas del registro,
construcción Docker, smoke real e inventario antes/después.

DEC-138
Fecha: 2026-07-30
Problema: el canal público ya entrega tarjetas y dashboards validados, pero el
bot privado usa una presentación distinta y todavía no tiene una unidad
Railway independiente para usuarios premium.
Opciones: incluir long polling en el servicio del canal; duplicar API y modelos
en un segundo contenedor; o desplegar un adaptador premium separado que consuma
la API pública y reutilice exactamente el presentador del canal.
Decisión: abrir Fase 109. El bot premium será un servicio Railway sin modelos
ni base propia, conectado por HTTPS a la API DIKAMAHA. La allowlist de IDs será
obligatoria y fail-closed. Las predicciones seleccionadas producirán la misma
tarjeta principal y el mismo dashboard de mercados que el canal; explorador,
PBP, estadísticas y jugadores conservarán sus contratos existentes.
Motivo: aislar fallos y escalado del long polling, evitar lógica predictiva
duplicada y mantener paridad visual entre la entrega pública y premium.
Estado: congelada; implementación validada para despliegue
Impacto en contratos/fases: presentación, acceso y empaquetado únicamente; no
cambia Dixon-Coles, Kalman, Markov, snapshots, probabilidades ni promoción.
Evidencia requerida: paridad byte a byte del presentador, rechazo sin allowlist,
smoke del contenedor, health del API remoto y pruebas del flujo Telegram.

DEC-139
Fecha: 2026-07-29
Problema: cumplir el límite de 3,900 caracteres no evita que Telegram envuelva
filas anchas, botones, nombres o comparativas en móviles y vuelva ambigua la
asociación entre equipo, valor y periodo.
Opciones: confiar en el ajuste automático; reducir toda la información; o
definir un contrato móvil medible y compactar únicamente campos variables.
Decisión: Fase 109 v1.1 congela un contrato de hasta 72 columnas visibles para
prosa, 40 para tablas monoespaciadas, 32 para botones y 3,900 caracteres por
mensaje. Se acotan nombres dinámicos con elipsis, las estadísticas pasan de 46
a 38 columnas, el contexto separa equipos y el play-by-play limita cada texto
sin retirar eventos. Bot premium y avisador comparten las mismas reglas.
Motivo: evitar saltos automáticos ambiguos manteniendo probabilidades, periodos
y navegación completos.
Estado: congelada; validación móvil aprobada
Impacto en contratos/fases: presentación únicamente; no modifica API, datos,
modelos, probabilidades, router, snapshots ni settlement.
Evidencia obtenida: 24 pruebas dirigidas y 450 pruebas integrales aprobadas;
8 integraciones opcionales omitidas. Casos de presión incluyen nombres largos,
tablas, tarjetas, dashboards, botones, contexto, eventos y perfiles.

DEC-140
Fecha: 2026-07-29
Problema: Fase 109 sólo admite usuarios enumerados, pero las pruebas públicas
requieren abrir temporalmente el bot sin editar una allowlist por persona.
Opciones: retirar la autorización; usar una allowlist infinita; o añadir un
interruptor explícito con modo privado seguro por defecto.
Decisión: versionar `TELEGRAM_ACCESS_MODE=private|public`. `private`
seguirá exigiendo una allowlist no vacía. `public` admitirá cualquier usuario
en chat privado, conservará rate limit por usuario, API key, HTTPS, una sola
réplica y rechazo de grupos. El modo inválido impedirá el arranque.
Motivo: permitir apertura controlada y reversible sin debilitar por accidente
el despliegue premium.
Estado: congelada; interruptor validado para Railway
Impacto en contratos/fases: sólo autenticación del adaptador Telegram; no
cambia API, modelos, probabilidades, mercados, canal ni settlement.
Evidencia obtenida: default privado, rechazo privado, aceptación pública, rate
limit por usuario, grupos ignorados, modo inválido rechazado y configuración
Railway validados; 30 pruebas dirigidas y 457 integrales aprobadas, 8 omitidas.

DEC-141
Fecha: 2026-08-07
Problema: la auditoría integral detectó que la corrección de baja anotación
Dixon-Coles intercambia las intensidades local y visitante en los marcadores
`1-0` y `0-1`; además, varios recorridos walk-forward actualizan historia entre
partidos de la misma liga y hora de inicio, y algunas rutas aceptan estados,
métricas o artefactos numéricamente inválidos.
Opciones: conservar resultados previos por compatibilidad; corregir únicamente
la fórmula; o abrir una fase de integridad que corrija fórmula, causalidad,
métricas y validación fail-closed y vuelva a generar toda evidencia afectada.
Decisión: abrir Fase 113 y congelar el contrato `model_integrity_v1`. La fórmula
Dixon-Coles seguirá la definición canónica para `x=home_goals` y
`y=away_goals`; ningún resultado con el mismo kickoff podrá alimentar otra
predicción de ese kickoff; los splits se alinearán a kickoffs completos; y las
rutas oficiales y sidecars rechazarán convergencia, probabilidades, PMF,
historia o hashes inválidos. Toda promoción previa afectada queda pendiente de
revalidación, sin inferir ventaja económica ni usar cuotas sintéticas.
Motivo: restablecer la interpretación matemática del modelo, eliminar leakage
intra-kickoff y hacer que cualquier corrupción produzca fallback explícito.
Estado: congelada; Fase 113 validada con salidas selectivas
Impacto en contratos/fases: reemplaza la evidencia numérica de Fases 84A, 88,
94, 103, 104, 105 y 106 cuando dependa de las rutas corregidas. No amplía los
mercados aprobados ni autoriza ROI, Kelly, apuestas combinadas o despliegue.
Evidencia obtenida: fórmula exacta, invariancia al orden intra-kickoff, 27
fronteras compartidas reducidas a cero, 45 cold starts excluidos, hashes
completos, PMF adaptativas válidas, runtime fail-closed, replays sellados y
suite integral de 485 pruebas aprobadas con 8 integraciones opcionales
omitidas. La cadena oficial conserva sólo 1X2 y over 2.5; BTTS usa la
reparación causal de Fase 106 y hay ocho mercados de equipo en shadow.

DEC-142
Fecha: 2026-08-07
Problema: el producto sólo tiene una ruta Markov pre-match promovible y una
envoltura `markov_v1` live sintética. Para predicción in-play se necesita
actualizar el régimen con observaciones del partido sin convertir Hawkes en un
modelo competidor ni sumar dos intensidades que explican la misma señal.
Opciones: usar únicamente Hawkes; sustituir Markov pre-match por un modelo
live; o mantener el prior pre-match, añadir un filtro Markov Live y aplicar
Hawkes exclusivamente como residual de memoria corta.
Decisión: abrir Fase 114 con tres capas versionadas. Dixon-Coles/Kalman y
Markov pre-match fijan el prior anterior al kickoff; `markov_live_v1` actualiza
estado, intensidades restantes y hazards con marcador, reloj y eventos
observados hasta cada snapshot; `hawkes_live_v2` sólo modula esos hazards en
escala logarítmica mediante shrinkage acotado. `rho=0` debe reproducir Markov
Live exactamente. Las tres salidas (`markov_live`, `hawkes_residual` y
`combined_live`) se conservan separadas y permanecen shadow.
Motivo: Markov representa régimen, marcador y tiempo restante; Hawkes
representa clustering transitorio. La composición residual evita doble conteo
y permite medir valor incremental de Hawkes contra un Markov Live congelado.
Estado: congelada; Markov y Hawkes selectivo validados históricamente en shadow
Impacto en contratos/fases: amplía DEC-002 sólo para una nueva ruta in-play;
no reabre ni modifica Markov pre-match v4, DEC-100, `match_features v1`, el
router oficial o mercados pre-match. Extiende 100E con captura live raw-first.
Probabilidades y odds ESPN quedan como benchmark/archivo y nunca son features.
Evidencia requerida: replay determinista, causalidad por snapshot, reloj
monótono, fallbacks exactos, shrinkage por objetivo, estabilidad subcrítica,
API compatible y gate histórico DEC-143 antes de cualquier integración.
Evidencia obtenida: contratos y runner Fase 114 implementados; 519 pruebas
aprobadas y 8 integraciones opcionales omitidas; `py_compile` y diff limpios;
replay Markov y combinado idéntico; radio espectral Hawkes
`0.31428571428571433`; score/PBP y eventos futuros rechazados fail-closed. El
smoke ESPN inicial recibió HTTP 403. DEC-144 corrigió el transporte; el gate
histórico posterior usa 7,400 partidos/34 ligas. DEC-145 añade admisión Hawkes
por liga seleccionada sólo en validación y mantiene el router intacto.

DEC-143
Fecha: 2026-08-07
Problema: esperar 500 partidos futuros no satisface el ritmo de validación
requerido, mientras `prospective_staging_v2` ya contiene más de diez mil
partidos completos y timelines ESPN históricos suficientes para reconstruir
snapshots pseudo-live.
Opciones: conservar el gate prospectivo; promover sin evidencia; o reemplazar
la espera por una evaluación histórica causal con separación temporal.
Decisión propuesta: reemplazar el umbral prospectivo de Fase 114 por un gate
histórico read-only. Sólo cuentan partidos con marcador reconciliado, periodo
reglamentario e identidad completa. Los priors pre-match se reconstruyen
walk-forward usando exclusivamente partidos con kickoff anterior; snapshots,
targets y splits se agrupan por partido y por kickoff atómico. Desarrollo
selecciona parámetros Markov, validación selecciona únicamente shrinkage
Hawkes y confirmación permanece intacta hasta el scoring final.
Motivo: aprovecha la base existente sin convertir snapshots correlacionados en
unidades IID ni permitir que el resultado del partido objetivo contamine su
prior. También mide por separado Markov contra un baseline score/tiempo y la
combinación contra Markov congelado.
Estado: congelada; gate histórico ejecutado
Impacto en contratos/fases: revisa sólo el gate de Fase 114 y no reabre
holdouts clausurados de Markov pre-match, no modifica `match_features v1` ni
autoriza salidas oficiales. El gate mínimo será 5,000 partidos reconciliados y
20 ligas, con confirmación temporal, bootstrap por partido y replay idéntico.
Evidencia requerida: inventario read-only, cero solapamiento de kickoffs,
priors estrictamente anteriores, score/PBP reconciliado, métricas de 1X2,
over 2.5, BTTS y próximo evento, resultados por liga, intervalos bootstrap y
hashes reproducibles.
Evidencia obtenida: 10,251 partidos y 1,349,977 eventos permanecieron
idénticos antes/después; 9,649 partidos de regulación reconciliaron; 7,400
partidos/34 ligas superaron warm-up. Splits 4,417/1,586/1,397 sin kickoffs
compartidos. Markov mejoró objetivo `-0.002259`, IC95%
`[-0.002858, -0.001635]`, con 84.375% de ligas no degradadas. Hawkes para
goles global mejoró agregado `-0.000648`, IC95%
`[-0.001026, -0.000272]`, sin tocar próximo evento, pero sólo no degradó
59.375% de ligas. DEC-145 corrige la heterogeneidad mediante selección causal
por liga. Replay final
`c926fd712c596e4d475856cf6259db766cbb1f950a83e0d6e2da7bad47612b53`.

DEC-144
Fecha: 2026-08-07
Problema: `site.api.espn.com` devuelve HTTP 403 de Akamai desde el entorno
actual, aunque Core ESPN continúa en HTTP 200. La implementación trataba ese
403 regional como indisponibilidad total del proveedor.
Opciones: desactivar ESPN; usar CDN pese a responder 202 sin JSON; o conservar
Site API como primario y usar el host ESPN `site.web.api.espn.com` como
fallback equivalente sólo ante bloqueos de transporte.
Decisión propuesta: mantener los paths Site/Core documentados, permitir ambos
hosts ESPN en la allowlist y reintentar una sola vez el mismo path y parámetros
en `site.web.api.espn.com` cuando el primario Site responda 403. Core sigue sin
fallback; CDN no entra mientras no entregue JSON 200. La URL efectiva se
conserva en provenance y raw-first.
Motivo: las pruebas reales devolvieron 200 JSON para scoreboard, summary y
standings en el fallback, y 200 para Core event, mientras CDN devolvió 202
vacío. El cambio es acotado, auditable y no oculta el origen efectivo.
Estado: congelada; corrección validada
Impacto en contratos/fases: corrige transporte ESPN de Fases 72, 73, 100 y
114 sin cambiar semántica de features ni habilitar probabilities/odds.
Evidencia requerida: tests de fallback, allowlist, caché/provenance, smoke
scoreboard-summary-standings-event-plays y fallo cerrado si ambos hosts fallan.
Evidencia obtenida: tests de fallback y doble 403 aprobados. Smoke real:
scoreboard histórico, summary y standings 200 por `site.web.api.espn.com`;
event 200 y 206 plays por `sports.core.api.espn.com`. El parser usa
`displayClock` para conservar descuento `90'+N'`; el ciclo Fase 114 terminó
sin errores y el CDN quedó excluido tras responder 202 sin JSON.

DEC-145
Fecha: 2026-08-08
Problema: Hawkes global mejora el agregado confirmatorio, pero degrada más del
40% de ligas y por tanto no cumple el gate robusto. Descartarlo perdería señal
útil; habilitarlo globalmente degradaría ligas sin soporte.
Opciones: retirar Hawkes; aceptar la media global; o congelar una política de
admisión por liga seleccionada antes de abrir confirmación y usar Markov fuera.
Decisión: seleccionar una allowlist Hawkes exclusivamente con validación. Una
liga requiere al menos 30 partidos de validación y mejora estricta en log-loss
de mercados de gol. `rho_goal=1` se aplica sólo a ligas admitidas;
`rho_next_event=0` y todas las ligas rechazadas reproducen Markov exactamente.
Se exigen al menos cinco ligas admitidas, IC95% objetivo confirmatorio bajo
cero y 70% de ligas no degradadas. El prior live se vincula a identidad de
evento/equipos/liga y a un cutoff estrictamente anterior al kickoff.
Motivo: Markov permanece baseline universal; Hawkes aporta sólo memoria corta
donde validación demuestra soporte, sin competir, reemplazar ni doble-contar
la intensidad estructural.
Estado: congelada; gate histórico selectivo aprobado en shadow
Impacto en contratos/fases: añade
`hawkes_live_v2_league_admission_v1` y su artefacto versionado. El runner carga
la política por defecto, pero el router oficial, bots y pre-match no cambian.
Evidencia requerida: selección `validation_only`, confirmación no consultada,
fallback exacto, bootstrap por partido, replay idéntico y robustez por liga.
Evidencia obtenida: validación admitió 17 ligas. En 1,397 partidos/6,985
snapshots de confirmación, Hawkes selectivo mejoró objetivo `-0.000398`, IC95%
`[-0.000650, -0.000135]`, con 84.375% de ligas no degradadas. La variante
global quedó en 59.375%. Dos ejecuciones reprodujeron
`c926fd712c596e4d475856cf6259db766cbb1f950a83e0d6e2da7bad47612b53` y
PostgreSQL conservó 10,251 partidos/1,349,977 eventos sin escrituras.

DEC-146
Fecha: 2026-08-08
Problema: Railway devolvía `shadow_unavailable` para Cambridge United–Barnet
y el bot no mostraba probabilidades de 1T, 2T ni partido completo. Los logs
aislaron `artifact_hash_mismatch:audit.json` tanto en mercados de equipo como
en la reparación BTTS. El manifiesto se había sellado con CRLF, Git entregaba
LF a Linux y la imagen mínima excluía evidencia científica no consumida que el
verificador amplio exigía de todos modos.
Opciones: empaquetar toda la evidencia histórica; retirar la verificación de
hashes; o limitar el gate a componentes runtime obligatorios y hacer portable
únicamente la representación de finales de línea.
Decisión: cada proveedor verifica de forma fail-closed todos sus archivos
runtime requeridos. Las entradas del manifiesto que son sólo evidencia no se
exigen dentro de la imagen. Para texto conocido se acepta hash raw, LF o CRLF;
binarios, contenido, esquema, causalidad y aprobaciones permanecen estrictos.
Motivo: restaura paridad Windows/Linux sin aumentar la imagen, desactivar
integridad ni tratar evidencia de evaluación como dependencia de inferencia.
Estado: congelada; hotfix validado para despliegue
Impacto en contratos/fases: revisión operativa de Fases 107, 109 y 113. No
modifica modelos, probabilidades, router, mercados aprobados, Hawkes, Markov ni
política económica.
Evidencia requerida: regresiones de LF/CRLF, ausencia de evidencia opcional,
rechazo de alteraciones reales, imagen mínima, fixture real, contrato móvil y
suite integral.
Evidencia obtenida: 22 pruebas dirigidas; imagen Docker construida; Cambridge
United–Barnet produjo 8 filas, 21 grupos y los tres periodos; tarjeta de 345 y
dashboard de 2,941 caracteres; BTTS cargó el calibrador; suite integral de 522
aprobadas y 8 omitidas.

DEC-147
Fecha: 2026-08-08
Problema: Markov Live y Hawkes residual superaron el gate histórico de Fase
114, pero la API sólo aceptaba snapshots ya ensamblados y Telegram no ofrecía
un menú de partidos activos. Además, el prior pre-match normal rechaza por
diseño cualquier kickoff pasado, por lo que una consulta iniciada durante el
partido no podía construir el baseline causal necesario.
Opciones: mantener Live sólo como runner técnico; permitir que Telegram llame
ESPN o calcule modelos; o publicar un flujo API read-only que descubra el
scoreboard, capture raw-first, reconstruya el prior desde el snapshot histórico
versionado con cutoff de kickoff y ejecute las capas shadow centralizadas.
Decisión: añadir catálogo y predicción de fixtures live autenticados a la API.
El prior se reconstruye de forma determinista usando exclusivamente partidos
anteriores al kickoff objetivo, excluye el match actual, conserva hash y se
declara `reconstructed_causal_prematch_prior`; no se presenta como captura
prospectiva congelada. Markov Live es el baseline universal. Hawkes sólo aplica
el residual de goles en ligas admitidas por la política de validación congelada;
fuera de ella, y para próximo evento, el combinado reproduce Markov exactamente.
Telegram consume sólo la API, expone `Partidos en vivo` y `Modelos en operación`
y rotula Markov, Hawkes y combinado como experimentales shadow no promovidos.
Motivo: activa el producto sin duplicar inferencia, usar datos posteriores al
kickoff para el prior ni hacer competir a Hawkes con Markov. La reconstrucción
causal permite usar la base histórica existente sin esperar una cohorte nueva.
Estado: congelada; validada para despliegue
Impacto en contratos/fases: revisión operativa de Fases 109 y 114. Añade
endpoints read-only y presentación Telegram; no modifica router oficial,
parámetros congelados, política Hawkes, probabilidades pre-match ni promoción.
Evidencia requerida: regresiones de prior causal, identidad, política por liga,
fallback exacto, listado sin partidos, menús y formato móvil; suite integral,
imágenes Docker y smoke de producción de API y bot.
Evidencia obtenida: prior real reconstruido con cutoff estricto, hash y cero uso
del target; fallback Hawkes fuera de allowlist idéntico a Markov; proveedor ESPN
real sin fallos parciales; 529 pruebas aprobadas y 8 omitidas. Las imágenes API
y bot construyeron como usuario no privilegiado; la API declaró 6 modelos, la
política de 17 ligas y el catálogo live, mientras el bot conservó cero
artefactos locales. La verificación Railway se ejecuta tras integrar a `main`.

DEC-148
Fecha: 2026-08-08
Problema: la navegación Telegram vigente obliga a recorrer árboles de botones
y volver al inicio para cambiar de tarea. Una interfaz visual rica necesita
estado persistente, filtros, gráficas, favoritos y alertas sin exponer la clave
de inferencia ni duplicar ESPN/modelos en el cliente.
Opciones: ampliar indefinidamente callbacks; sustituir el bot por una web; o
crear una Mini App híbrida con BFF autenticado y mantener el bot como fallback
y canal de notificaciones.
Decisión: abrir Fase 115. La Mini App será Next.js/TypeScript en un servicio
Railway separado. Validará `Telegram.WebApp.initData` en servidor, custodiará
la clave DIKAMAHA, usará PostgreSQL para preferencias y un worker sin
`getUpdates` para alertas. Todo dato predictivo vendrá de la API DIKAMAHA.
Markov Live, Hawkes residual y combinado permanecerán separados y shadow.
Motivo: entregar una experiencia móvil de aplicación sin crear una segunda
fuente de verdad, competir por el long polling ni modificar la cadena
matemática validada.
Estado: congelada; desplegada en Railway con acceso privado gradual
Impacto en contratos/fases: presentación, autenticación, preferencias y
notificaciones de Fase 115. No cambia Fases 113/114, router oficial, snapshots,
parámetros, políticas de promoción ni semántica de mercados.
Evidencia requerida: autenticación Telegram criptográfica, secreto ausente del
bundle, ownership y dedupe PostgreSQL, worker sin `getUpdates`, paridad BFF,
pruebas responsive, Docker y smoke Railway.
Evidencia obtenida: firma/expiración/grupos/CSRF cubiertos; bundle sin secretos
ni URLs ESPN; límites y dedupe comprobados sobre PostgreSQL 17; 535 pruebas
Python, 12 Vitest y 4 Playwright; build Docker no-root y smoke HTTP 200.
PostgreSQL, Mini App y worker están `Online` en Railway; health remoto `ready`,
sesión vacía rechazada y `setChatMenuButton` confirmado. La Mini App opera para
la allowlist privada y el worker permanece apagado lógicamente. Quedan el smoke
interactivo, el short name BotFather y la activación posterior de alertas.

DEC-149
Fecha: 2026-08-08
Problema: Fase 115 cubre predicción, live, modelos y suscripciones, pero el bot
conserva capacidades de exploración que aún no tienen equivalente visual:
calendario histórico, contexto, play-by-play, estadísticas por periodo,
equipos, búsqueda, plantillas y perfiles de jugador.
Opciones: mantener esas funciones sólo en callbacks; duplicar el conector ESPN
en Next.js; o publicar paridad visual mediante el BFF y los endpoints
`/v1/explorer/*` ya gobernados por DIKAMAHA.
Decisión: ampliar Fase 115 con un Centro de datos que cubra todas las funciones
del bot. El BFF usará una allowlist cerrada de rutas explorer, conservará la
sesión Telegram y nunca construirá URLs ESPN. Contexto, estadísticas y perfiles
son presentación; no modifican predicción, modelos ni promoción.
Motivo: completar la transición a dashboard sin crear una segunda fuente de
verdad ni perder el fallback nativo del bot.
Estado: congelada; desplegada en Railway con acceso privado
Impacto en contratos/fases: extensión de presentación de Fases 98, 100 y 115.
No cambia Fases 113/114, router, snapshots, probabilidades ni clasificación
official/shadow.
Evidencia requerida: matriz de paridad completa, rutas BFF autenticadas,
pruebas de navegación, contratos de payload y conexión real con API DIKAMAHA.
Evidencia obtenida: matriz documentada y Centro de datos implementado; allowlist
de nueve capacidades explorer con rechazo de rutas arbitrarias; transporte BFF
autenticado probado con credencial sólo servidor; conexión real aprobada para
readiness, modelos, ligas, fechas y próximos; 536 pruebas Python, 16 Vitest,
7 Playwright y build Next aprobados. El commit `95946d7` quedó activo en
Railway; health y `/explore` respondieron 200, auth sin sesión respondió 401 y
el worker confirmó `enabled: false`. La salida de modelos permanece intacta.

DEC-150
Fecha: 2026-08-08
Problema: el dashboard puede quedar vacío porque próximos sólo consulta tres
ligas y cuatro días, la búsqueda de equipos exige seleccionar liga y las
entidades deportivas carecen de identidad visual consistente.
Opciones: mantener los filtros rígidos; consultar proveedores desde el
navegador; o ampliar los catálogos DIKAMAHA y servir únicamente imágenes PNG
transparentes validadas mediante el BFF.
Decisión: próximos usará por defecto el catálogo completo de ligas y una
ventana futura de 14 días; equipos admitirá búsqueda global con dos caracteres.
Logos y retratos se propagarán como metadatos de presentación y se entregarán
por un proxy autenticado DIKAMAHA/BFF con allowlist de hosts, firma PNG y canal
alfa. Nunca serán features de modelos ni fuentes de inferencia.
Motivo: recuperar navegación y predicciones, mejorar reconocimiento visual y
mantener ESPN detrás de la API DIKAMAHA.
Estado: congelada
Impacto en contratos/fases: sólo amplía contratos aditivos de catálogo y
presentación de Fases 100 y 115. No altera snapshots, parámetros, router,
probabilidades ni clasificación official/shadow.
Evidencia requerida: búsqueda global tolerante a acentos, catálogo futuro
multiliga, imágenes sin acceso directo del navegador al proveedor, tests de
contrato, build y smoke Railway.
Evidencia obtenida: 542 pruebas Python, 16 Vitest, 7 Playwright, builds Next y
Docker aprobados. Smoke Railway autenticado confirmó 18 ligas en próximos,
Barnet global, live sin fallos parciales, PNG transparente y predicción con
1T, 2T y total. La cuenta de GitHub Actions no inició jobs por facturación;
no se observó un fallo de código. La extensión visual conserva ahora nombres
de equipo desde el catálogo hasta títulos, 1X2 y mercados, y recupera la
identidad para enlaces anteriores aun cuando inferencia sólo devuelve IDs.
Gráfica 1X2, comparación xG/lambda, tabla e indicadores derivados son sólo
presentación: no recalculan ni sustituyen probabilidades. Regresión
Cruzeiro–Mirassol, 543 Python, 16 Vitest, 8 Playwright, typecheck y build Next
aprobados. El commit `8aa3aca` fue integrado por PR #13; Railway confirmó
`SUCCESS` y los smoke posteriores sobre Mini App y API respondieron HTTP 200.
Una regresión posterior de catálogos reveló `PORT=8080` frente al fallback
interno `8000`. La corrección propaga la URL administrada, añade reintentos GET,
health dependiente de ligas y error visual recuperable; no duplica catálogos ni
altera datos/modelos. Gates: 544 Python, 17 Vitest y 9 Playwright aprobados.

DEC-151
Fecha: 2026-08-08
Problema: en Telegram Web/Desktop la autenticación BFF respondía 200, pero
todas las lecturas posteriores de catálogos respondían 401. La cookie de
sesión `SameSite=Lax` era rechazada al ejecutar la Mini App dentro del contexto
embebido de Telegram; el frontend aceptaba el login sin comprobar que la cookie
hubiera quedado disponible.
Opciones: exponer el token de sesión al JavaScript; retirar autenticación de
catálogos; o conservar la sesión HttpOnly y adaptar su transporte al contexto
cross-site embebido.
Decisión: en producción la cookie será `HttpOnly; Secure; SameSite=None` y
`Partitioned`; en desarrollo conservará `SameSite=Lax`. Todo fetch BFF incluirá
credenciales explícitamente y el proveedor verificará `/api/session/me` después
del intercambio de `initData` antes de renderizar rutas protegidas.
Motivo: recuperar Telegram Web/Desktop sin exponer tokens, relajar permisos ni
permitir que el frontend muestre una falsa sesión autenticada.
Estado: congelada; hotfix validado localmente para despliegue
Impacto en contratos/fases: transporte de sesión de Fase 115. No cambia API
DIKAMAHA, ESPN, modelos, probabilidades, router, persistencia ni promoción.
Evidencia requerida: cookie de producción segura y particionada, secuencia
401→login 200→sesión 200 antes de catálogos, suites y smoke Railway.
Evidencia obtenida: Network Logs reales con login 200 seguido de catálogos
401; regresión E2E de confirmación post-login; 18 Vitest, 10 Playwright,
typecheck y build Next aprobados. Falta smoke posterior al despliegue.

DEC-152
Fecha: 2026-08-09
Problema: `/v1/live` consultaba sólo la fecha UTC actual. ESPN conserva
partidos nocturnos activos bajo la fecha local de la competición, por lo que
el catálogo podía responder vacío después del cambio de día UTC aunque hubiera
partidos en curso. El detalle tampoco podía reconstruir esos fixtures. La Mini
App mostraba únicamente marcador y predicciones, sin exponer el play-by-play
observado que el runtime ya captura causalmente.
Opciones: fijar la zona horaria de una liga; aceptar fecha manual; usar streaming
directo ESPN desde el navegador; o ampliar de forma acotada la ventana del
scoreboard detrás de DIKAMAHA.
Decisión: cuando no se solicita fecha explícita, catálogo y detalle inspeccionan
D-1, D y D+1 UTC, deduplican por `match_id` y conservan fecha exacta cuando el
cliente sí la indica. El detalle añade estadísticas y acciones derivadas sólo
del `live_event_stream_v1` raw-first; goles usan el marcador autoritativo. La
Mini App refresca catálogo cada 20 s y detalle cada 10 s mediante BFF.
Motivo: cubre fronteras de fecha y actualizaciones automáticas sin WebSocket,
sin acceso ESPN desde navegador y sin añadir señales nuevas a inferencia.
Estado: congelada
Impacto en contratos/fases: extensión aditiva de Fases 114 y 115. No altera el
prior pre-match, Markov, residual Hawkes, combinación, probabilidades, router ni
clasificación shadow.
Evidencia requerida: partido real de D-1 descubierto en D UTC, detalle con
logos PNG, marcador, estadísticas, acciones y tres capas; prueba automática de
refresco y suites sin regresión.
Evidencia obtenida: prueba real sobre 18 ligas encontró tres fixtures activos
de D-1 sin fallos parciales. Jaguares de Córdoba–Once Caldas completó captura,
prior causal, estadísticas observadas y Markov/Hawkes/combinado. Regresiones
dirigidas y suite integral: 546 Python, 18 Vitest, 10 Playwright, typecheck y
build Next aprobados. PR #17 integrada; Railway `SUCCESS`; smoke autenticado de
producción devolvió catálogo y predicción 200 con 3 activos, 18 ligas, 0 fallos,
logos, estadísticas, 24 acciones y tres capas.

DEC-153
Fecha: 2026-08-09
Problema: se solicita integrar las probabilidades pre-match/live atribuidas al
predictor/SPI de ESPN, el historial minuto a minuto y `pickcenter`, además de
valorar si deben sustituir Markov Live y Hawkes. La inspección real encontró
`pickcenter` en summaries, pero no encontró `predictor`, `winprobability` ni un
recurso Core de probabilidades utilizable en las muestras auditadas.
Opciones: reemplazar las capas DIKAMAHA por el feed externo; derivar una falsa
probabilidad desde cuotas; ignorar la fuente; o añadir un benchmark tolerante
a ausencia y una dinámica heurística separada.
Decisión: crear `provider_match_context_v1`. Las probabilidades del proveedor
se normalizan sólo cuando existen campos explícitos de predictor o historial de
win probability y se muestran como benchmark `display_only/live_only`, nunca
como feature, calibrador o fallback predictivo. Markov Live conserva el rol de
baseline universal y Hawkes el residual selectivo: no se sustituyen sin una
comparación OOS versionada. `pickcenter` sólo publica metadatos de disponibilidad
y permanece `financial_isolated`; no se exponen cuotas ni se derivan como SPI.
El play-by-play produce una curva de presión heurística firmada con pesos
congelados y media móvil de cinco minutos, separada de toda inferencia.
Motivo: evita confundir precios de mercado con un modelo analítico, conserva
cobertura cuando ESPN no publica predictor y añade contexto visual reproducible
sin romper causalidad ni promoción.
Estado: congelada; implementada y validada para despliegue
Impacto en contratos/fases: extensión aditiva de 100E, 100F, 114 y 115. No
modifica `match_features v1`, priors, modelos, probabilidades oficiales,
política Hawkes ni router.
Evidencia requerida: fixtures con y sin predictor, normalización 1X2 válida,
ausencia explícita, aislamiento de pickcenter, curva de presión reproducible,
API/BFF sin URLs ESPN en navegador, regresiones y smoke Railway.
Evidencia obtenida: dos summaries reales (`col.1/401877868` y
`arg.1/401841485`) respondieron correctamente pero no publicaron predictor ni
historial; ambos sí declararon contexto financiero aislado y cero cuotas
expuestas. Fixtures sintéticos cubrieron predictor 0–100, historial live,
tripletes inválidos y ausencia. La curva firmada verificó pesos, orientación,
anulación, marcadores de gol y media móvil. Gates locales: 555 Python
aprobadas/8 omitidas, 18 Vitest, 10 Playwright, typecheck y build Next.
PR #19 integrada en `main`; Railway confirmó `SUCCESS` para los cuatro
servicios. El smoke BFF autenticado devolvió sesión, benchmark y live HTTP 200,
contratos `provider_match_context_v1`/`match_pressure_v1`, 90 puntos de presión
y las tres capas DIKAMAHA presentes.

DEC-154
Fecha: 2026-08-09
Problema: la interfaz oculta el contenido de `pickcenter` y del scoreboard con
`activeodds=true`, por lo que el usuario no puede distinguir apertura, cierre y
valor live. Además, la ausencia real del predictor analítico se percibe como un
fallo y el catálogo visual sólo ofrece 18 de los 49 slugs ya auditados.
Opciones: convertir cuotas en un supuesto SPI; sustituir Markov/Hawkes; exponer
el payload financiero sin control; o publicar una cinta de mercado normalizada,
separada del predictor y de cualquier inferencia.
Decisión: ampliar `provider_match_context_v1` con `provider_market_tape_v1`.
Se muestran únicamente líneas y cuotas americanas de apertura, cierre y live
publicadas por el proveedor, sin enlaces de apuesta, probabilidad implícita,
agregación, recomendación, ROI, Kelly ni stakes. Este contexto permanece
`financial_isolated`, `display_only` y jamás entra a features, calibración,
fallback o promoción. El predictor analítico conserva estado explícito
`not_published` cuando ESPN no lo entrega; no se imputa desde mercado. Los 49
slugs validados en Fase 36 se habilitan en catálogos y filtros, con fallos
parciales aislados y límites de concurrencia.
Motivo: hacer visible la información solicitada sin confundir precio con
probabilidad analítica, sin degradar cobertura live y reutilizando el catálogo
multiliga ya auditado.
Estado: congelada; implementada y desplegada en Railway
Impacto en contratos/fases: extensión aditiva de Fases 36, 100 y 115. No
modifica snapshots, Dixon-Coles/Kalman, Markov, Hawkes, probabilidades oficiales,
router ni estados de promoción. Autoriza sólo presentación financiera aislada y
reemplaza `odds_exposed=false` de DEC-153.
Evidencia requerida: normalización determinista de moneyline/spread/total;
apertura/cierre/live cuando existan; ausencia analítica explícita; endpoint BFF
autenticado; 49 ligas navegables; pruebas de contrato, navegación, build y smoke
real sobre un fixture con `pickcenter`.
Evidencia obtenida: `col.1/401877857` confirmó predictor analítico
`not_published` y un proveedor con moneyline open/close/live completo; el
scoreboard `activeodds=true` devolvió tres fixtures normalizados. El catálogo
visual coincide exactamente con los 49 slugs de Fase 36; el barrido live real
encontró un activo, 49 ligas y cero fallos parciales. Gates: 559 Python
aprobadas/8 omitidas, 18 Vitest, 11 Playwright, typecheck y build Next.
PR #21 integrada en `main`; Railway reportó `SUCCESS` para API, Mini App, bot
y worker. El smoke público confirmó servicio
`dikamaha_local_service_v1.8_provider_markets`, readiness `true`, Mini App
`ready` con PostgreSQL/upstream y `/markets` HTTP 200. Los endpoints protegidos
continúan cerrados sin la credencial server-side.

DEC-155
Fecha: 2026-08-09
Problema: Fase 114 entrega Markov Live y Hawkes como capas shadow validadas,
pero la salida live oficial heredada sigue siendo `markov_v1` y no representa
de forma conjunta tiempo restante, estados continuos, presión reciente,
expulsiones, fuerza relativa ni incertidumbre numérica.
Opciones: mantener las capas shadow; promover sólo Markov Live tras otro gate;
o reemplazar inmediatamente la salida live por un motor compuesto con fallback
automático, conservando intacta la ruta pre-match.
Decisión: abrir Fase 116 y crear `live_probability_engine_v1`. El motor oficial
compone Poisson dinámico, CTMC, hazard tipo Cox, Elo live latente y
`hawkes_live_v2` residual en escala de intensidades. Monte Carlo será un
diagnóstico asincrónico determinista de 20,000 simulaciones y no decidirá la
salida. `POST /v1/predict/live/fixture` publicará el bloque oficial nuevo; los
bloques de Fase 114 se conservan como aliases de compatibilidad. Un fallo o un
snapshot sin reloj/marcador revierte a Markov Live o a `markov_v1` sin alterar
pre-match. Predictor ESPN y `pickcenter` permanecen benchmark display-only y
archivo financiero aislado, nunca features.
Motivo: modelar la evolución física del partido con componentes identificables
y auditables sin promediar probabilidades competidoras ni duplicar la señal de
Hawkes. El fallback permite revertir el cambio oficial sin tocar snapshots,
datos históricos o Dixon-Coles/Kalman.
Estado: congelada e implementada; replay histórico y gates integrales aprobados
Impacto en contratos/fases: sustituye DEC-142/147/148 únicamente respecto de la
clasificación oficial de la ruta live y crea
`live_probability_engine_contract_v1`. No reabre DEC-100, no modifica
`match_features v1`, la ruta pre-match, mercados financieros ni predicciones
congeladas. La evaluación histórica de Fase 116 se registra, pero no bloquea la
activación inicial solicitada.
Evidencia requerida: causalidad por snapshot, probabilidades normalizadas,
CTMC conservativa, hazards finitos, Hawkes subcrítico, replay, Monte Carlo
determinista no bloqueante, fallback exacto, p95 analítico menor a 250 ms,
compatibilidad API/bot/Mini App y suites integrales.

DEC-156
Fecha: 2026-08-10
Problema: la Mini App renderiza únicamente `user_market_view`, es decir las
nueve líneas congeladas por DEC-103/107/108/110 y expuestas por DEC-112. Sus
umbrales son constantes de `MARKET_METADATA` y, dentro de Fase 88,
`market_name` deriva la línea de `MARKET_LINES[metric]`, que es por métrica y
no por periodo. En consecuencia todo partido muestra el mismo conjunto de
mercados y primer tiempo comparte umbral con segundo tiempo para una misma
métrica. La escalera adaptativa de Fase 102 ya se calcula y el canal Telegram
la publica desde Fase 101 v1.5, pero la Mini App no la consume.
Opciones: reparametrizar `MARKET_LINES` por periodo; exponer la escalera
completa `distributional_market_view`; o renderizar en la Mini App la rejilla
acotada `bounded_market_grid_view` que ya produce el runtime.
Decisión: renderizar `bounded_market_grid_view` en el detalle pre-match de la
Mini App como bloque adicional por periodo, conservando intacto el bloque de
`user_market_view`. La rejilla mantiene etiqueta experimental, muestra over y
under complementarios con su baseline y cae al comportamiento actual cuando la
vista viene vacía.
Motivo: `MARKET_LINES` por periodo invalidaría la evaluación congelada de Fase
88 y los locks versionados de DEC-110, y exigiría re-correr el gate
walk-forward completo. La rejilla acotada ya está calculada, es causal, varía
por partido y periodo mediante `_centered_lines` centrada en P(over)≈50% y ya
fue publicada por el canal sin incidencias.
Estado: congelada e implementada
Impacto en contratos/fases: cambio de presentación exclusivo de la Mini App.
No modifica el router, la salida oficial, `user_market_view`, `match_features
v1`, `MARKET_LINES`, los artefactos sellados de Fase 84A/88 ni la clasificación
shadow de Fase 102. No promueve ninguna línea a oficial.
Evidencia requerida: equivalencia exacta del bloque `user_market_view`
existente, fallback visible cuando la rejilla falta, complementariedad
over/under, separación por periodo, etiqueta experimental presente, y gates
Vitest, Playwright, typecheck y build Next aprobados.
Evidencia obtenida (2026-08-10): `bounded_market_grid_view` se renderiza en un
bloque propio de `prediction-detail.tsx` sin tocar el bloque `user_market_view`,
que conserva sus nueve líneas congeladas. La rejilla no se monta cuando la
vista llega vacía, de modo que un payload sin Fase 102 reproduce exactamente la
pantalla anterior. Prueba Playwright nueva `renders an adaptive market grid
with distinct lines per period` verifica líneas 1.5/2.5/3.5 en primer tiempo
frente a 2.5/3.5/4.5 en segundo tiempo sobre la misma métrica y equipo, más el
delta contra baseline. Gates: 18 Vitest, 12 Playwright, typecheck y build Next
aprobados. La suite Python no se ejecutó porque el cambio no toca `src/`.

```text
DEC-NNN
Fecha:
Problema:
Opciones:
Decisión:
Motivo:
Estado: propuesta | congelada | reemplazada
Impacto en contratos/fases:
Evidencia requerida:
```
