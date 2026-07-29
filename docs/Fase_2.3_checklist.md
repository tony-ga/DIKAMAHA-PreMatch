**Fase 2.3 Checklist**

Fuente de referencia:

* \[decision\_matrix\_match\_features\_v1.md\](/mnt/c/Users/marco/Desktop/dikahama\_project/futbol\_predictor/artifacts/phase\_2\_2\_audit\_match\_features\_v1/decision\_matrix\_match\_features\_v1.md)  
* \[audit\_match\_features\_v1.md\](/mnt/c/Users/marco/Desktop/dikahama\_project/futbol\_predictor/artifacts/phase\_2\_2\_audit\_match\_features\_v1/audit\_match\_features\_v1.md)  
* \[Especificación Formal match\_features v1.md\](/mnt/c/Users/marco/Desktop/dikahama\_project/futbol\_predictor/Especificación Formal match\_features v1.md)  
* \[02\_mercados\_dikamaha.md\](/mnt/c/Users/marco/Desktop/dikahama\_project/futbol\_predictor/02\_mercados\_dikamaha.md)  
* \[04\_arquitectura\_dikamaha.md\](/mnt/c/Users/marco/Desktop/dikahama\_project/futbol\_predictor/04\_arquitectura\_dikamaha.md)

## **1\. Preparación de Datos**

* Seleccionar únicamente partidos elegibles para `match_features v1`.  
* Excluir partidos con identidad incompleta:  
  * `match_id` ausente;  
  * `home_team_id` ausente;  
  * `away_team_id` ausente;  
  * `match_date` ausente.  
* Excluir o marcar `704766` por `existing_data_failed_run`.  
* Mantener el orden temporal estricto por `match_date` y luego `match_id`.  
* Resolver `competition_id` de forma explícita y verificable.  
* No inferir `competition_id` solo desde `season`.  
* Validar orientación home/away contra el partido fuente.  
* Rechazar cualquier swap accidental de local/visitante.  
* Marcar como no elegibles los partidos que no cumplan la elegibilidad mínima:  
  * marcador final disponible;  
  * ambos equipos identificados;  
  * al menos 1 partido previo por equipo.

## **2\. Contrato de Salida**

* Garantizar una sola fila por `match_id`.  
* Mantener separación lógica entre features y targets.  
* Identificar campos obligatorios:  
  * `match_id`;  
  * `home_team_id`;  
  * `away_team_id`;  
  * `match_date`;  
  * `season`;  
  * targets principales;  
  * `feature_cutoff_ts`;  
  * versión del dataset.  
* Identificar campos nullable:  
  * features con cobertura incompleta;  
  * ventanas parciales;  
  * provenance auxiliar;  
  * flags de cobertura.  
* Incluir flags de cobertura por bloque:  
  * historial previo;  
  * últimos 5;  
  * descanso;  
  * carga 30 días;  
  * Dixon-Coles;  
  * Kalman.  
* Incluir provenance completo:  
  * fuente usada;  
  * timestamp de disponibilidad;  
  * timestamp de corte;  
  * versión analítica;  
  * razón de exclusión si aplica.  
* Definir `feature_cutoff_ts` como frontera lógica del dataset.  
* Versionar el dataset como contrato explícito, no como detalle implícito.

## **3\. Features Históricas**

* Calcular últimos 5 partidos estrictamente anteriores al cutoff.  
* Calcular puntos de últimos 5 partidos.  
* Calcular goles a favor en últimos 5\.  
* Calcular goles en contra en últimos 5\.  
* Calcular diferencial de goles en últimos 5\.  
* Calcular victorias en últimos 5\.  
* Calcular empates en últimos 5\.  
* Calcular derrotas en últimos 5\.  
* Calcular descanso en días desde el último partido previo.  
* Calcular carga de últimos 30 días.  
* Mantener ventanas incompletas con:  
  * cantidad real de partidos usados;  
  * bandera de cobertura;  
  * `NULL` cuando la métrica no sea estadísticamente válida.  
* No imputar con cero cuando el valor semántico sea desconocido.  
* No usar partidos del propio encuentro para completar ventanas.

## **4\. Dixon-Coles**

* Definir entradas exactas del modelo:  
  * historial previo de goles;  
  * localía;  
  * ataque/defensa previos.  
* Entrenar solo con partidos anteriores al partido objetivo.  
* Prohibir uso del partido objetivo en cualquier ajuste.  
* Tratar equipos con historial insuficiente de forma explícita:  
  * `NULL`;  
  * bandera de cobertura;  
  * exclusión si no cumplen umbral mínimo.  
* Guardar parámetros del modelo con versión reproducible.  
* Validar numéricamente los parámetros:  
  * no NaN;  
  * no infinitos;  
  * rangos plausibles.  
* Verificar que el modelo no consuma `match_statistics` objetivo.  
* Mantener trazabilidad de la corrida del modelo:  
  * fecha;  
  * versión;  
  * inputs;  
  * outputs.

## **5\. Kalman**

* Definir estado inicial a partir de Dixon-Coles.  
* Actualizar secuencialmente solo con partidos previos.  
* Definir covarianzas o parámetros requeridos y versionarlos.  
* Respetar el orden temporal absoluto.  
* Exponer el estado disponible antes de cada kickoff.  
* Tratamiento específico de primeros partidos:  
  * estado inicial;  
  * cobertura parcial;  
  * no leakage.  
* Asegurar reproducibilidad de la evolución temporal.  
* No utilizar el partido objetivo en la actualización del estado.  
* Documentar claramente el punto de corte del estado para cada match.

## **6\. Targets**

* Generar `home_goals`.  
* Generar `away_goals`.  
* Generar `result_1x2`.  
* Generar `over_2_5`.  
* Generar `btts`.  
* Generar `goal_margin`.  
* Generar `total_goals`.  
* Derivar todos los targets exclusivamente del marcador final.  
* Validar consistencia entre marcador y derivaciones.

## **7\. Controles Anti-Leakage**

* Verificar timestamps de cada feature contra `feature_cutoff_ts`.  
* Verificar `feature_cutoff_ts <= kickoff_ts`.  
* Excluir el partido objetivo de cualquier feature histórica.  
* Excluir `match_statistics` objetivo como señal pre-match.  
* Excluir `events_timeline` objetivo como señal pre-match.  
* Probar que ningún campo use datos futuros.  
* Detectar features calculadas con fuentes posteriores al cutoff.  
* Rechazar filas con dependencia temporal imposible.  
* Registrar cualquier violación como exclusión o bloqueo.

## **8\. Control de Calidad**

* Garantizar unicidad por `match_id`.  
* Validar orientación home/away.  
* Validar NULLs permitidos y NULLs prohibidos.  
* Validar rangos numéricos.  
* Validar consistencia de targets.  
* Validar cobertura mínima por equipo.  
* Validar trazabilidad de fuente.  
* Validar reproducibilidad de una corrida a la siguiente.  
* Validar que las exclusiones sean auditables.  
* Validar que la razón de exclusión quede persistida en artefacto local.

## **9\. Plan de Pruebas**

* Escribir pruebas unitarias para:  
  * ventanas históricas;  
  * cálculo de últimos 5;  
  * descanso;  
  * targets.  
* Probar partidos iniciales con historial insuficiente.  
* Probar un partido intermedio con cobertura completa.  
* Probar una frontera temporal:  
  * partido previo válido;  
  * partido actual excluido de features.  
* Probar específicamente `704766`.  
* Probar reejecución determinista:  
  * mismo input;  
  * mismo output;  
  * mismas exclusiones.  
* Probar que `match_statistics` y `events_timeline` objetivo no entran en pre-match.  
* Probar que el orden temporal se mantiene estable.

## **10\. Criterios de Aceptación**

* Existe selección clara de partidos elegibles.  
* `704766` está marcado o excluido con razón explícita.  
* La identidad completa y la orientación están validadas.  
* `competition_id` está resuelto de forma verificable, o el bloque sigue pendiente según la decisión aprobada.  
* Los targets coinciden con el marcador final y pasan validación.  
* Las features históricas respetan cutoff temporal.  
* Dixon-Coles y Kalman no usan el partido objetivo.  
* Las ventanas incompletas quedan con flags y `NULL` explícito.  
* Existen artefactos locales de validación.  
* PostgreSQL permanece sin cambios.  
* No existe entrenamiento todavía.  
* No se construyó aún el dataset final.

## **Matriz de Dependencias**

* **Bloquea preparación de datos**  
  * `competition_id` normalizado y verificable.  
  * razón formal para `704766`.  
  * regla final para exclusiones por historial insuficiente.  
* **Bloquea features históricas**  
  * definición exacta de ventana 30 días.  
  * criterio de validez para ventanas incompletas.  
* **Bloquea Dixon-Coles**  
  * parámetros y persistencia versionada.  
  * validación del universo histórico previo.  
* **Bloquea Kalman**  
  * estado inicial reproducible.  
  * parámetros de actualización versionados.  
* **Bloquea salida final**  
  * contrato definitivo de provenance.  
  * validación anti-leakage aprobada.

## **Riesgos**

* Historial insuficiente en partidos iniciales de temporada.  
* Ambigüedad en `competition_id` si no se normaliza el catálogo.  
* Leakage accidental por uso de datos del partido objetivo.  
* Cobertura parcial de ventanas de 5 y 30 días.  
* Inconsistencia entre fuentes si no se fija una fuente canónica.  
* Reproducibilidad débil si no se versionan parámetros de DC/Kalman.

## **Artefactos Esperados**

* Informe local de partidos elegibles y excluidos.  
* Lista local de razones de exclusión.  
* Resumen local de cobertura por equipo.  
* Resumen local de cobertura por feature.  
* Resultado local de validaciones anti-leakage.  
* Registro local de versión del dataset.  
* Evidencia local de que PostgreSQL no fue modificado.

## **Decisión de Paso a Fase 2.4**

Se puede pasar a Fase 2.4 solo si:

* la selección de partidos elegibles está cerrada;  
* la orientación está validada;  
* el contrato de salida está congelado;  
* las ventanas históricas tienen reglas explícitas;  
* Dixon-Coles y Kalman tienen estrategia de inicialización clara;  
* `competition_id` está resuelto o formalmente bloqueado fuera de v1;  
* los artefactos de validación existen y son reproducibles.

## **Decisiones que siguen bloqueadas**

* Inclusión de `competition_id` en v1 sin catálogo relacional verificable.  
* Inclusión de Markov.  
* Inclusión de Hawkes.  
* Inclusión de clima, viaje y Elo sin fuente reproducible y versionada.  
* Inclusión de `match_statistics` y `events_timeline` del partido objetivo como features pre-match.  
* Construcción del dataset final.

Si quieres, el siguiente paso es convertir este checklist en una **lista de tareas ejecutables por lotes** para Fase 2.4, manteniendo el mismo nivel de control.

