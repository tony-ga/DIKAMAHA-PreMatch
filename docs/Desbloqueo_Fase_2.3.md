**Desbloqueo Fase 2.3**

## **Prioridad 1: `competition_id`**

### **Estado actual**

* Existe evidencia de `esp.1` como slug canónico en la documentación de ESPN.  
* En los artefactos locales, `raw_api_responses.source_competition_id` ya aparece como campo disponible.  
* No vi un `competition_id` relacional normalizado en `matches` o un catálogo explícito en el esquema base.

### **Tareas ejecutables**

1. Definir `esp.1` como identificador canónico de competencia para el universo actual.  
2. Verificar que todos los `matches` elegibles pertenecen a esa misma competencia.  
3. Vincular cada partido con `source_competition_id` desde `raw_api_responses`.  
4. Comparar `raw_api_responses.source_competition_id` contra el slug esperado `esp.1`.  
5. Marcar partidos sin mapeo inequívoco.  
6. Emitir un artefacto versionado de mapeo partido→competencia.  
7. Bloquear cualquier mezcla de competiciones fuera de `esp.1`.

### **Decisión requerida para el bloqueo**

* **Valor canónico**: `esp.1`  
* **Fuente actual suficiente**: sí, pero solo como base de ingestión y auditoría.  
* **Catálogo local o artefacto versionado**: para Fase 2.3 basta un **artefacto versionado**; no hace falta crear todavía catálogo relacional nuevo, pero el artefacto debe ser la fuente de verdad temporal.  
* **Validación de mezcla de competiciones**: debe ser por igualdad exacta de `source_competition_id` contra `esp.1` y por consistencia de fecha/equipo/evento.

### **Partidos sin mapping**

* Deben identificarse explícitamente en el artefacto.  
* En el estado actual, la prioridad es detectar cualquier `match_id` cuyo `raw_api_responses.source_competition_id` no sea `esp.1` o no exista.

### **Recomendación**

* Usar `esp.1` como canónico para v1.  
* Mantener un artefacto local versionado `competition_mapping_v1.json` o equivalente.  
* No introducir aún una tabla nueva hasta cerrar el contrato de ingestión.

### **Criterio de aceptación**

* Cada uno de los 381 partidos tiene `competition_id`/slug asignado de forma inequívoca.  
* No existe mezcla de ligas.  
* Los partidos sin mapping quedan listados con razón explícita.

### **Dependencias**

* `raw_api_responses.source_competition_id`  
* consistencia ESPN `event -> competition`  
* artefacto versionado de mapeo

---

## **Prioridad 2: reglas históricas**

### **Tareas ejecutables**

1. Definir formalmente la ventana de últimos 5 partidos.  
2. Definir formalmente la ventana de carga de 30 días.  
3. Definir qué es un partido válido para historial.  
4. Definir tratamiento de partidos con historial parcial.  
5. Definir umbral mínimo para materialización.  
6. Definir umbral mínimo para entrenamiento.  
7. Definir política para los 10 partidos iniciales.  
8. Definir tratamiento de `704766` y `existing_data_failed_run`.

### **Definición formal recomendada**

#### **Ventana últimos 5**

* Orden cronológico estrictamente anterior al `feature_cutoff_ts`.  
* Tomar los **5 partidos válidos más recientes** del equipo.  
* Si hay menos de 5, usar los disponibles y marcar cobertura parcial.  
* Si no hay suficientes para una métrica estable, dejar `NULL`.

#### **Ventana carga 30 días**

* Todos los partidos del equipo cuyo `match_date` esté dentro de los 30 días anteriores al `feature_cutoff_ts`.  
* Deben ser estrictamente anteriores al cutoff.  
* Se reporta cantidad real de partidos usados y cobertura parcial si aplica.

#### **Partidos válidos**

* Partidos con:  
  * `match_date` no nula;  
  * identidad completa;  
  * marcador final disponible si se usa para targets;  
  * fecha estrictamente anterior al cutoff del partido objetivo;  
  * competencia válida y no mezclada.  
* No son válidos:  
  * el partido objetivo;  
  * partidos futuros;  
  * partidos con orientación ambigua;  
  * partidos fuera de la competencia aprobada.

#### **Historial parcial**

* Se conserva.  
* Se marca con bandera de cobertura.  
* Se permite para materialización solo si no rompe la elegibilidad mínima.  
* No se imputa cero si eso altera el significado estadístico.

#### **Umbral mínimo para materialización**

* `>= 1` partido previo por equipo.  
* marcador final disponible.  
* ambas identidades validadas.  
* competencia resuelta.

#### **Umbral mínimo para entrenamiento**

* Recomendado: `>= 5` partidos previos por ambos equipos.  
* Fila materializable pero no necesariamente entrenable si no alcanza este umbral.

#### **Política para los 10 partidos iniciales**

* No eliminarlos del universo histórico.  
* Marcarlos como:  
  * materializables parciales o  
  * excluidos de entrenamiento estable.  
* Deben conservar razón explícita de exclusión o cobertura.

#### **`704766` y `existing_data_failed_run`**

* Tratarlo como caso especial de exclusión controlada.  
* Debe quedar marcado con razón de exclusión persistente.  
* No debe entrar al conjunto entrenable hasta que la causa esté resuelta y revalidada.

### **Decisión requerida para el bloqueo**

* Aprobar la ventana formal exacta.  
* Aprobar si `last_5` parcial se materializa con `NULL` o con valor parcial \+ bandera.  
* Aprobar si los 10 iniciales quedan fuera solo del entrenamiento o también de la materialización.  
* Aprobar si `704766` queda excluido permanentemente de v1 o solo en espera.

### **Recomendación**

* Materializar con cobertura parcial explícita.  
* Entrenar solo con filas que cumplan el umbral recomendado.  
* Excluir `704766` hasta resolver el incidente, sin borrarlo del universo auditable.

### **Criterio de aceptación**

* Las reglas permiten reproducir exactamente qué partidos entran, cuáles quedan parciales y cuáles se excluyen.  
* La política de `704766` está cerrada.  
* No hay ambigüedad sobre la ventana temporal.

### **Dependencias**

* orden temporal correcto;  
* kickoff confiable;  
* `match_date` completa;  
* competencia validada.

---

## **Prioridad 3: Dixon-Coles**

### **Tareas ejecutables**

1. Definir universo histórico permitido.  
2. Definir función de ponderación temporal.  
3. Definir parámetros de localía.  
4. Definir parámetros de ataque y defensa.  
5. Definir inicialización.  
6. Definir tratamiento de equipos con poca historia.  
7. Definir outputs por partido.  
8. Definir versión de parámetros.  
9. Definir artefactos de reproducibilidad.  
10. Definir validaciones numéricas.

### **Especificación recomendada**

#### **Universo histórico permitido**

* Solo partidos anteriores al `feature_cutoff_ts`.  
* Solo dentro de la competencia aprobada.  
* Solo partidos válidos y no conflictivos.

#### **Función de ponderación temporal**

* Peso decreciente por recencia.  
* Debe depender de la distancia temporal al partido objetivo.  
* La fórmula exacta puede quedar fijada en una versión del modelo, pero debe ser monotónica decreciente y reproducible.

#### **Parámetros de localía**

* Un término global de home advantage.  
* Opcionalmente ajustable por temporada o competencia.  
* No debe inferirse del partido objetivo.

#### **Parámetros de ataque y defensa**

* Un parámetro por equipo para ataque.  
* Un parámetro por equipo para defensa.  
* Estimados solo con partidos históricos permitidos.

#### **Inicialización**

* Basada en priors neutros o en media de liga.  
* Si hay poca historia, usar priors conservadores, nunca información del partido objetivo.

#### **Equipos con poca historia**

* Deben recibir priors y/o estado inicial neutral.  
* Si la historia es insuficiente, el registro puede materializarse con cobertura parcial.  
* No usar el propio partido para completar el estado.

#### **Outputs por partido**

* `home_attack_dc`  
* `home_defense_dc`  
* `away_attack_dc`  
* `away_defense_dc`  
* `expected_home_goals_dc`  
* `expected_away_goals_dc`  
* `home_advantage_dc`

#### **Versión de parámetros**

* Debe existir una versión explícita del modelo, por ejemplo:  
  * `dc_params_version`  
  * `dc_model_version`  
* La versión debe quedar ligada a la corrida y al universo histórico usado.

#### **Artefactos de reproducibilidad**

* Parámetros estimados.  
* Universo de partidos usados.  
* Fecha de corte.  
* Versión de competencia.  
* Hash o firma del input lógico.

#### **Validaciones numéricas**

* No NaN.  
* No infinitos.  
* Rangos plausibles.  
* Consistencia entre equipos y partidos.  
* Reproducibilidad exacta ante reejecución.

### **Decisión requerida para el bloqueo**

* Fijar la función de ponderación temporal.  
* Fijar el esquema de priors.  
* Fijar la política de equipos con poca historia.  
* Aprobar el formato de versionado.

### **Recomendación**

* Versionar Dixon-Coles como artefacto independiente del dataset.  
* No mezclar entrenamiento del modelo con materialización del dataset.  
* Dejar explícito que el modelo solo consume historial anterior.

### **Criterio de aceptación**

* El modelo puede describirse completamente sin ambigüedad.  
* El universo de entrenamiento está cerrado.  
* Los outputs por partido son reproducibles y verificables.

### **Dependencias**

* historial permitido ya limpio;  
* competencia validada;  
* orden temporal estricto;  
* política de cobertura acordada.

---

## **Prioridad 4: Kalman**

### **Tareas ejecutables**

1. Definir vector de estado.  
2. Definir estado inicial.  
3. Definir covarianza inicial.  
4. Definir ruido de proceso.  
5. Definir ruido de observación.  
6. Definir actualización secuencial.  
7. Definir punto exacto de corte antes de cada kickoff.  
8. Definir comportamiento de los primeros partidos.  
9. Definir artefactos de reproducibilidad.

### **Especificación recomendada**

#### **Vector de estado**

* Debe incluir, como mínimo:  
  * ataque local;  
  * defensa local;  
  * ataque visitante;  
  * defensa visitante.  
* Si se modela por equipo, el vector debe ser consistente con Dixon-Coles como estado base.

#### **Estado inicial**

* Arranca desde Dixon-Coles o desde priors neutros.  
* No puede usar información del partido objetivo.

#### **Covarianza inicial**

* Debe ser explícita y versionada.  
* Representa incertidumbre inicial del estado.

#### **Ruido de proceso**

* Captura evolución natural entre partidos.  
* Debe ser estable y reproducible.

#### **Ruido de observación**

* Captura variabilidad del resultado observado.  
* Debe estar claramente separado del ruido de proceso.

#### **Actualización secuencial**

* Solo con partidos anteriores ordenados cronológicamente.  
* Cada estado se calcula antes del kickoff del partido objetivo.

#### **Punto exacto de corte**

* El estado para un partido `M` se calcula usando información hasta el último partido anterior a `M`.  
* Nunca se usa el propio `match_statistics` de `M` ni `events_timeline` de `M`.

#### **Primeros partidos**

* Deben usar estado inicial y alta incertidumbre.  
* Si no hay historia, el estado queda más cercano al prior que a una estimación firme.  
* No deben contaminarse con datos del propio encuentro.

#### **Artefactos de reproducibilidad**

* Estado inicial.  
* Covarianzas.  
* Ruido de proceso.  
* Ruido de observación.  
* Serie de actualizaciones.  
* Versión del filtro.

### **Decisión requerida para el bloqueo**

* Fijar composición exacta del vector de estado.  
* Fijar covarianza inicial y ruidos.  
* Fijar la política de arranque de primeros partidos.  
* Fijar el formato de artefactos de reproducción.

### **Recomendación**

* Mantener Kalman como capa separada, alimentada por Dixon-Coles.  
* No exponer parámetros implícitos.  
* Versionar cada corrida para trazabilidad.

### **Criterio de aceptación**

* El estado para cada kickoff puede reconstruirse.  
* No hay fuga temporal.  
* La evolución es determinista dado el mismo input.

### **Dependencias**

* Dixon-Coles establecido;  
* historial previo limpio;  
* orden cronológico garantizado;  
* competencia validada.

---

## **Lista priorizada de tareas ejecutables de desbloqueo**

1. Cerrar `competition_id` canónico:  
   * aprobar `esp.1`;  
   * construir artefacto versionado de mapping;  
   * validar que no haya mezcla de competiciones.  
2. Cerrar reglas históricas:  
   * ventana de últimos 5;  
   * ventana 30 días;  
   * regla de partidos válidos;  
   * política de parciales;  
   * umbrales de materialización y entrenamiento;  
   * tratamiento de `704766`.  
3. Cerrar especificación de Dixon-Coles:  
   * universo permitido;  
   * ponderación temporal;  
   * priors;  
   * outputs;  
   * versionado.  
4. Cerrar especificación de Kalman:  
   * estado;  
   * covarianza;  
   * ruidos;  
   * actualización;  
   * artefactos.  
5. Solo después, habilitar implementación de Fase 2.3.

---

## **Recomendación final**

Para desbloquear Fase 2.3 sin tocar PostgreSQL:

* resolver `competition_id` con `esp.1` como canónico y artefacto versionado;  
* fijar reglas históricas con materialización parcial explícita;  
* congelar Dixon-Coles y Kalman como especificaciones versionadas antes de escribir cualquier dataset.

Si quieres, el siguiente paso es que convierta esto en una **lista de tareas atómicas con orden de ejecución y criterio de “done” por tarea**.

