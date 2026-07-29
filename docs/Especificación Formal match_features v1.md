**Especificación Formal `match_features v1`**

## **1\. Propósito y alcance**

`match_features v1` es el contrato analítico canónico para modelado **pre-match** de fútbol. Su objetivo es producir **una fila por partido** con:

* identidad y orientación explícita del encuentro;  
* features construidas únicamente con información disponible **antes o como máximo al momento de corte permitido**;  
* targets post-match listos para entrenamiento y evaluación;  
* metadatos de provenance para auditoría y trazabilidad.

Este contrato está diseñado para soportar la primera capa operativa del sistema predictivo:

* Dixon-Coles como base estructural;  
* Kalman como actualización temporal usando solo partidos anteriores.

No incluye todavía:

* eventos del partido objetivo;  
* `match_statistics` del partido objetivo;  
* Markov;  
* Hawkes;  
* corners, tarjetas o player props;  
* clima o viaje sin fuente reproducible.

---

## **2\. Principio temporal**

La regla temporal válida para `match_features v1` es:

`source_available_at <= feature_cutoff_ts <= kickoff_ts`

Donde:

* `source_available_at` es el instante en que el dato de origen estuvo disponible;  
* `feature_cutoff_ts` es el instante de corte lógico del dataset;  
* `kickoff_ts` es la hora de inicio oficial del partido.

Importante:

* `feature_snapshot_ts` puede ser posterior al kickoff si la materialización física ocurre después.  
* `feature_snapshot_ts` no define validez temporal de features; solo define cuándo se generó la fila.  
* Ninguna feature pre-match puede usar información posterior a `feature_cutoff_ts`.

---

## **3\. Grano, clave y orientación**

### **3.1 Grano**

* **1 fila por `match_id`**

### **3.2 Clave única**

* `match_id` es la clave única lógica del contrato.

### **3.3 Orientación obligatoria**

Cada fila debe representar explícitamente:

* `home_team_id`  
* `away_team_id`  
* `match_date`  
* `competition_id`  
* `season`

La orientación es inmutable:

* `home_team_id` siempre es el local;  
* `away_team_id` siempre es el visitante.

No se permite inferir la competencia únicamente desde `season`.

---

## **4\. Separación conceptual**

`match_features v1` se divide en tres bloques:

1. **Features pre-match**  
   * información conocida antes del kickoff;  
   * historial previo;  
   * forma reciente;  
   * fuerza Dixon-Coles;  
   * estado Kalman calculado solo con partidos anteriores.  
2. **Targets post-match**  
   * resultados observados del partido;  
   * derivados del marcador final.  
3. **Provenance y control**  
   * trazabilidad de fuente;  
   * timestamps;  
   * calidad;  
   * versionado;  
   * cobertura.

---

## **5\. Diccionario de datos completo**

### **5.1 Identidad y contexto del partido**

| Campo | Tipo SQL sugerido | Nulabilidad | Definición | Fuente | Timestamp de disponibilidad | `feature_cutoff_ts` | Riesgo de leakage | Versión |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| `match_id` | `BIGINT` | No | Identificador interno único del partido | `matches.id` | Antes del kickoff | `<= kickoff_ts` | No | v1 |
| `home_team_id` | `BIGINT` | No | Equipo local del partido | `matches.home_team_id` | Antes del kickoff | `<= kickoff_ts` | No | v1 |
| `away_team_id` | `BIGINT` | No | Equipo visitante del partido | `matches.away_team_id` | Antes del kickoff | `<= kickoff_ts` | No | v1 |
| `match_date` | `TIMESTAMP` | No | Fecha y hora oficial del kickoff | `matches.match_date` | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `competition_id` | `BIGINT` o `VARCHAR` | No | Identificador explícito de competición/league | catálogo interno \+ mapeo ESPN | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `competition_name` | `VARCHAR(120)` | Sí | Nombre humano de la competición | catálogo interno o raw ESPN | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `season` | `VARCHAR(20)` | No | Temporada del partido | `matches.season` | Antes del kickoff | `<= kickoff_ts` | No | v1 |
| `home_team_name` | `VARCHAR(150)` | Sí | Nombre del equipo local para auditoría | `teams.name` | Antes del kickoff | `<= kickoff_ts` | No | v1 |
| `away_team_name` | `VARCHAR(150)` | Sí | Nombre del equipo visitante para auditoría | `teams.name` | Antes del kickoff | `<= kickoff_ts` | No | v1 |

### **5.2 Historial de resultados previos**

Estos campos se calculan usando únicamente partidos anteriores al `feature_cutoff_ts`.

| Campo | Tipo SQL sugerido | Nulabilidad | Definición | Fuente | Timestamp de disponibilidad | `feature_cutoff_ts` | Riesgo de leakage | Versión |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| `home_matches_played_season` | `INTEGER` | Sí | Partidos jugados por el local en la temporada antes del partido | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_matches_played_season` | `INTEGER` | Sí | Partidos jugados por el visitante en la temporada antes del partido | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_last_5_matches_played` | `INTEGER` | Sí | Partidos válidos usados en la ventana de últimos 5 del local | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_last_5_matches_played` | `INTEGER` | Sí | Partidos válidos usados en la ventana de últimos 5 del visitante | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_last_5_points` | `INTEGER` | Sí | Puntos acumulados por el local en los últimos 5 partidos | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_last_5_points` | `INTEGER` | Sí | Puntos acumulados por el visitante en los últimos 5 partidos | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_last_5_goals_for` | `INTEGER` | Sí | Goles a favor del local en últimos 5 partidos | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_last_5_goals_against` | `INTEGER` | Sí | Goles en contra del local en últimos 5 partidos | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_last_5_goals_for` | `INTEGER` | Sí | Goles a favor del visitante en últimos 5 partidos | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_last_5_goals_against` | `INTEGER` | Sí | Goles en contra del visitante en últimos 5 partidos | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_last_5_goal_diff` | `INTEGER` | Sí | Diferencial de gol del local en los últimos 5 | derivado histórico | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_last_5_goal_diff` | `INTEGER` | Sí | Diferencial de gol del visitante en los últimos 5 | derivado histórico | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_last_5_wins` | `INTEGER` | Sí | Victorias del local en últimos 5 | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_last_5_draws` | `INTEGER` | Sí | Empates del local en últimos 5 | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_last_5_losses` | `INTEGER` | Sí | Derrotas del local en últimos 5 | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_last_5_wins` | `INTEGER` | Sí | Victorias del visitante en últimos 5 | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_last_5_draws` | `INTEGER` | Sí | Empates del visitante en últimos 5 | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_last_5_losses` | `INTEGER` | Sí | Derrotas del visitante en últimos 5 | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |

### **5.3 Forma reciente**

| Campo | Tipo SQL sugerido | Nulabilidad | Definición | Fuente | Timestamp de disponibilidad | `feature_cutoff_ts` | Riesgo de leakage | Versión |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| `home_form_points_per_match_5` | `NUMERIC(8,4)` | Sí | Puntos por partido del local en últimos 5 | derivado de historial | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_form_points_per_match_5` | `NUMERIC(8,4)` | Sí | Puntos por partido del visitante en últimos 5 | derivado de historial | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_form_goal_diff_5` | `NUMERIC(8,4)` | Sí | Diferencial medio de gol local últimos 5 | derivado de historial | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_form_goal_diff_5` | `NUMERIC(8,4)` | Sí | Diferencial medio de gol visitante últimos 5 | derivado de historial | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `form_diff_5` | `NUMERIC(8,4)` | Sí | Diferencia de forma entre local y visitante | derivado | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |

### **5.4 Descanso y carga competitiva**

| Campo | Tipo SQL sugerido | Nulabilidad | Definición | Fuente | Timestamp de disponibilidad | `feature_cutoff_ts` | Riesgo de leakage | Versión |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| `home_rest_days` | `INTEGER` | Sí | Días desde el último partido del local | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_rest_days` | `INTEGER` | Sí | Días desde el último partido del visitante | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_matches_last_30_days` | `INTEGER` | Sí | Número de partidos jugados por el local en 30 días | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_matches_last_30_days` | `INTEGER` | Sí | Número de partidos jugados por el visitante en 30 días | `matches` históricos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |

### **5.5 Dixon-Coles**

| Campo | Tipo SQL sugerido | Nulabilidad | Definición | Fuente | Timestamp de disponibilidad | `feature_cutoff_ts` | Riesgo de leakage | Versión |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| `home_attack_dc` | `NUMERIC(10,6)` | Sí | Fuerza ofensiva base del local | modelo Dixon-Coles entrenado con partidos previos | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_defense_dc` | `NUMERIC(10,6)` | Sí | Fuerza defensiva base del local | modelo Dixon-Coles | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_attack_dc` | `NUMERIC(10,6)` | Sí | Fuerza ofensiva base del visitante | modelo Dixon-Coles | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_defense_dc` | `NUMERIC(10,6)` | Sí | Fuerza defensiva base del visitante | modelo Dixon-Coles | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `expected_home_goals_dc` | `NUMERIC(10,6)` | Sí | Intensidad esperada local del modelo base | derivado DC | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `expected_away_goals_dc` | `NUMERIC(10,6)` | Sí | Intensidad esperada visitante del modelo base | derivado DC | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_advantage_dc` | `NUMERIC(10,6)` | Sí | Ajuste de localía de Dixon-Coles | calibración histórica | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |

### **5.6 Kalman**

Estos campos representan estados actualizados únicamente con partidos anteriores.

| Campo | Tipo SQL sugerido | Nulabilidad | Definición | Fuente | Timestamp de disponibilidad | `feature_cutoff_ts` | Riesgo de leakage | Versión |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| `home_attack_kalman` | `NUMERIC(10,6)` | Sí | Ataque del local tras actualización temporal | filtro de Kalman | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_defense_kalman` | `NUMERIC(10,6)` | Sí | Defensa del local tras actualización temporal | filtro de Kalman | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_attack_kalman` | `NUMERIC(10,6)` | Sí | Ataque del visitante tras actualización temporal | filtro de Kalman | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_defense_kalman` | `NUMERIC(10,6)` | Sí | Defensa del visitante tras actualización temporal | filtro de Kalman | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_attack_kalman_delta` | `NUMERIC(10,6)` | Sí | Cambio del ataque local respecto a Dixon-Coles | derivado | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `home_defense_kalman_delta` | `NUMERIC(10,6)` | Sí | Cambio de defensa local respecto a Dixon-Coles | derivado | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_attack_kalman_delta` | `NUMERIC(10,6)` | Sí | Cambio del ataque visitante respecto a Dixon-Coles | derivado | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `away_defense_kalman_delta` | `NUMERIC(10,6)` | Sí | Cambio de defensa visitante respecto a Dixon-Coles | derivado | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |

---

## **6\. Targets iniciales**

Los targets iniciales de `match_features v1` son únicamente los siguientes:

| Campo | Tipo SQL sugerido | Nulabilidad | Definición | Fuente | Disponibilidad | Riesgo de leakage | Versión |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| `home_goals` | `SMALLINT` | No | Goles finales del local | `matches.home_score` o fuente canónica final | Post-match | No | v1 |
| `away_goals` | `SMALLINT` | No | Goles finales del visitante | `matches.away_score` o fuente canónica final | Post-match | No | v1 |
| `result_1x2` | `CHAR(1)` | No | Resultado final `1`, `X` o `2` | derivado del marcador final | Post-match | No | v1 |
| `over_2_5` | `BOOLEAN` | No | Si el total de goles supera 2.5 | derivado del marcador final | Post-match | No | v1 |
| `btts` | `BOOLEAN` | No | Ambos equipos marcan | derivado del marcador final | Post-match | No | v1 |
| `goal_margin` | `SMALLINT` | No | Diferencia `home_goals - away_goals` | derivado del marcador final | Post-match | No | v1 |
| `total_goals` | `SMALLINT` | No | Total de goles del partido | derivado del marcador final | Post-match | No | v1 |

---

## **7\. Provenance y control**

Estos campos no son features del modelo, sino metadatos obligatorios.

| Campo | Tipo SQL sugerido | Nulabilidad | Definición | Fuente | Disponibilidad | `feature_cutoff_ts` | Riesgo de leakage | Versión |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| `feature_snapshot_ts` | `TIMESTAMPTZ` | No | Momento físico en que se materializó la fila | pipeline | Puede ser posterior al kickoff | n/a | No | v1 |
| `feature_cutoff_ts` | `TIMESTAMPTZ` | No | Hora lógica máxima de datos admitida por la fila | pipeline | Debe cumplir regla temporal | `<= kickoff_ts` | No | v1 |
| `source_available_at` | `TIMESTAMPTZ` | Sí | Momento en que la última fuente usada estuvo disponible | raw/catálogos/model outputs | Antes del cutoff | `<= feature_cutoff_ts` | Bajo | v1 |
| `feature_version` | `VARCHAR(32)` | No | Versión del contrato analítico | sistema | n/a | n/a | No | v1 |
| `provenance_version` | `VARCHAR(32)` | No | Versión de reglas de provenance | sistema | n/a | n/a | No | v1 |
| `source_system` | `VARCHAR(32)` | Sí | Sistema de origen principal del feature pack | raw/catálogos/model outputs | Antes del kickoff | `<= kickoff_ts` | Bajo | v1 |
| `source_event_id` | fuera de features pre-match | Sí | Identificador del evento fuente para auditoría, no para modelado | provenance | post-ingestión | n/a | No aplica en features | v1 |
| `source_hash` | `VARCHAR(128)` | Sí | Hash de trazabilidad del input lógico | pipeline | al materializar | n/a | No | v1 |

### **Observación obligatoria sobre `source_event_id`**

`source_event_id` no forma parte de las features pre-match. Debe residir en provenance o auditoría, porque su propósito es trazabilidad, no señal predictiva.

---

## **8\. Campos fuera de v1**

Quedan explícitamente fuera de `match_features v1`:

### **Fuera por diseño**

* eventos del partido objetivo;  
* `events_timeline` del partido objetivo;  
* `match_statistics` del partido objetivo;  
* Markov;  
* Hawkes;  
* corners;  
* tarjetas;  
* player props;  
* odds;  
* probabilidades live;  
* alineaciones live;  
* estado del partido live.

### **Fuera por falta de fuente reproducible en v1**

* clima;  
* viaje;  
* Elo;  
* variables contextuales externas no auditables de forma consistente.

Si se incorporan en el futuro:

* deben entrar en una versión posterior;  
* deben declarar fuente reproducible;  
* deben tener `source_available_at` verificable;  
* deben cumplir la regla temporal.

---

## **9\. Reglas de calidad**

### **9.1 Unicidad**

* Un `match_id` solo puede aparecer una vez en `match_features v1`.

### **9.2 Orientación**

* `home_team_id` y `away_team_id` deben coincidir con el partido fuente.  
* No se permiten swaps.  
* No se permiten filas ambiguas.

### **9.3 Fechas**

* `match_date` no puede ser nulo.  
* `feature_cutoff_ts <= kickoff_ts`.  
* `source_available_at <= feature_cutoff_ts`.  
* `feature_snapshot_ts` puede ser posterior al kickoff, pero no invalida la restricción temporal.

### **9.4 Nulls**

* Los campos de identidad y targets no deben ser nulos.  
* Los campos de features históricas pueden ser nulos cuando no exista cobertura suficiente.  
* Los nulos deben ser explícitos, no encubiertos con valores engañosos.  
* No usar defaults artificiales si cambian el significado estadístico del dato.

### **9.5 Consistencia de marcador**

* `home_goals` y `away_goals` deben concordar con la fuente canónica final.  
* `result_1x2`, `over_2_5`, `btts`, `goal_margin` y `total_goals` deben derivarse exclusivamente del marcador final.  
* Si hay contradicción entre fuentes, la fila debe quedar bloqueada o marcada para revisión, no corregida silenciosamente.

### **9.6 Cobertura histórica**

* Las métricas de últimos 5 partidos solo deben calcularse con partidos anteriores al cutoff.  
* Si la cobertura es insuficiente, el valor puede quedar nulo o con bandera de cobertura insuficiente.  
* Nunca completar una ventana con partidos del propio encuentro.

### **9.7 Trazabilidad**

* Toda fila debe poder rastrearse a:  
  * match origen;  
  * competencia;  
  * versión del contrato;  
  * cutoff temporal;  
  * timestamp de materialización;  
  * fuente primaria usada para cada bloque.

---

## **10\. Reglas anti-leakage**

1. No usar datos del partido objetivo para construir features pre-match.  
2. No usar `events_timeline` del partido objetivo en features pre-match.  
3. No usar `match_statistics` del partido objetivo en features pre-match.  
4. No usar datos con `source_available_at > feature_cutoff_ts`.  
5. No usar eventos o agregados post-kickoff para features históricas.  
6. No usar el marcador final para construir variables de entrada.  
7. No usar Markov o Hawkes dentro de `match_features v1`.  
8. No usar clima, viaje o Elo salvo que exista una fuente reproducible y versionada.  
9. No inferir la competencia solo a partir de `season`.  
10. No mezclar targets con features ni con provenance.

---

## **11\. Tratamiento de cobertura insuficiente**

La cobertura insuficiente debe manejarse de forma explícita.

### **Regla**

Si un equipo no tiene suficiente historial previo para una ventana:

* el feature correspondiente puede quedar `NULL`;  
* o puede llevar una bandera de cobertura insuficiente si el diseño físico lo requiere.

### **Umbrales recomendados**

* `last_5` requiere al menos 1 partido previo para emitir algo útil;  
* si hay menos de 5 partidos, los agregados deben basarse solo en los disponibles;  
* si no hay partidos previos, el bloque de forma puede quedar nulo.

### **Principio**

Nunca inventar historia.  
Nunca llenar con un promedio global sin marcarlo explícitamente.

---

## **12\. Reglas de versionado**

### **12.1 Versión del contrato**

* `match_features v1` es el contrato inicial estable.  
* Cualquier cambio de campos, semántica o cutoff requiere incremento de versión.

### **12.2 Compatibilidad**

* Cambios aditivos no rompen versión si no alteran la semántica existente.  
* Cambios de significado, fuente o temporalidad sí requieren nueva versión.

### **12.3 Versiones separadas**

* `feature_version`: versión del contrato de features.  
* `provenance_version`: versión del esquema de trazabilidad.  
* `model_basis_version`: opcional para distinguir evolución de Dixon-Coles/Kalman.

### **12.4 Regla práctica**

Si cambia:

* el conjunto de columnas,  
* la ventana temporal,  
* el criterio de cutoff,  
* la fuente primaria,  
  entonces sube la versión.

---

## **13\. Dependencias pendientes**

Para materializar `match_features v1` faltan o deben consolidarse:

1. **Catálogo explícito de competición**  
   * `competition_id` debe estar formalizado.  
   * no basta con `season`.  
2. **Mapeo estable de partidos y competiciones**  
   * `match_id` interno debe resolverse limpiamente contra fuentes ESPN.  
3. **Historial suficiente de partidos previos**  
   * necesario para Dixon-Coles y Kalman.  
4. **Reglas definitivas de ventanas históricas**  
   * cómo calcular últimos 5, descanso y partidos de temporada.  
5. **Fuente canónica para marcador final**  
   * debe definirse con prioridad explícita si existen discrepancias.  
6. **Política de nulabilidad por campo**  
   * qué se deja nulo y qué obliga a bloqueo.  
7. **Catálogo de versionado analítico**  
   * para asegurar reproducibilidad futura.  
8. **Definición de provenance persistible**  
* dónde vive `source_event_id`, hash, source timestamps y referencias a raw payload.

---

## **14\. Criterios de aceptación para cerrar Fase 2.1**

Fase 2.1 queda cerrada cuando se cumpla todo esto:

1. Existe una especificación formal versionada de `match_features v1`.  
2. La clave lógica es `match_id` y hay una sola fila por partido.  
3. La orientación home/away está fija y explícita.  
4. `competition_id` existe como identificador explícito.  
5. La regla temporal queda fijada como:  
   * `source_available_at <= feature_cutoff_ts <= kickoff_ts`.  
6. `feature_snapshot_ts` se distingue correctamente de la validez temporal.  
7. Las features pre-match se limitan a:  
   * identidad y contexto del partido;  
   * historial previo;  
   * forma últimos 5;  
   * goles a favor/en contra;  
   * descanso;  
   * partidos jugados en temporada;  
   * ratings Dixon-Coles;  
   * estados Kalman basados solo en partidos anteriores.  
8. Los targets iniciales son solo:  
   * `home_goals`  
   * `away_goals`  
   * `result_1x2`  
   * `over_2_5`  
   * `btts`  
   * `goal_margin`  
   * `total_goals`  
9. `source_event_id` queda fuera de las features y dentro de provenance.  
10. `match_statistics` del partido objetivo queda excluido del pre-match.  
11. No se incluyen Markov, Hawkes, corners, tarjetas, player props, clima ni viaje sin fuente reproducible.  
12. Se documentan reglas de calidad, anti-leakage, versionado y cobertura insuficiente.  
13. No se ejecutan migraciones ni cambios en PostgreSQL.  
14. No se construye todavía el dataset.

