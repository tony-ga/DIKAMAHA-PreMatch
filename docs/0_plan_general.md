# **DIKAMAHA AUDITORÍA CRÍTICA**

*Modelo Avanzado de Simulación Dinámica y Predicción en Mercados de Apuestas de Fútbol*

## **Introducción y Evaluación Crítica del Arquitecto**

El plan propuesto presenta una estructura metodológica excepcionalmente sólida para abordar la complejidad de los mercados de apuestas deportivas, integrando modelos macro tradicionales con herramientas de procesos de puntos estocásticos en tiempo real. No obstante, aplicando un riguroso criterio de ingeniería de datos y modelado cuantitativo, es imperativo advertir y modificar ciertos puntos antes de iniciar el desarrollo para mitigar riesgos críticos de bancarrota, latencia e inconsistencia matemática.

## **FASE 0: Estrategia y Fundaciones (Pre-Desarrollo)**

Esta fase establece el marco operativo, los objetivos predictivos y la infraestructura de control inicial.

* **0.1 Definición del Target:** Focalización exclusiva en LaLiga (Temporada 2024/2025) para delimitar el volumen de datos y controlar variables exógenas específicas. COPA MUNDIAL 2026

* **0.2 Variables de Destino (Target Variables):** 

* **0.3 Mapear las variables externas disponibles (Altitud del estadio, distancia de viaje, clima histórico).**

* #### **0.4 Diseño de Arquitectura Multicapa:**

  * ***Dixon-Coles***: Estimación estática basal de capacidades ofensivas y defensivas.

  * ***Filtro de Kalman***: Dinamización temporal de los parámetros entre jornadas.

  * ***Cadenas de Markov***: Modelado táctico micro-estructural de transiciones de estado durante el partido.

  * ***Procesos de Hawkes***: Captura de la inercia emocional y clústeres de eventos tras un suceso crítico (goles, tarjetas, sustituciones).

* **0.5 Entorno Técnico:** Repositorio Git estructurado bajo Gitflow; aislamiento de entorno con estricta reproducibilidad de las dependencias.

para la

## **FASE 1: Pipeline de Datos (Infraestructura)**

1. ### **Diseño de Base de Datos Relacional (PostgreSQL)**

Diseño normalizado optimizado para consultas de series temporales y estados de juego:

| Tabla | Campos Principales | Propósito / Índices |
| :---- | :---- | :---- |
| teams | id (PK), nombre, ciudad, altitud\_estadio | Catálogo maestro de equipos. |
| matches | id (PK), local\_id (FK), visitante\_id (FK), fecha, resultado\_f | Registro de eventos macro. Índice en (fecha, local\_id). |
| events\_timeline | id (PK), match\_id (FK), minuto, segundo, equipo\_id, tipo\_evento | Registro play-by-play. Eventos: shot, goal, corner, foul. |
| state\_transitions | match\_id (FK), timestamp, estado\_local, estado\_visitante | Historial de micro-estados para calibración de Markov. |

2. ### **Conector de API y Parser**

   * **Cliente robusto:** Implementación en Python usando

backoff exponencial para mitigar bloqueos de tasa (Rate Limiting).

, configurando políticas de reintento con

* **Capa de Caché:** Almacenamiento local en disco mediante serialización de las respuestas JSON crudas para independizar el desarrollo local de las cuotas de la API.

  * **Módulo de Extracción Lineal:** Motor Regex optimizado para capturar patrones textuales específicos e indexar cronológicamente cada evento del Play-by-Play.

  3. ### **Orquestación de Flujos (Apache Airflow)**

•

del día posterior.

•

los últimos 5 años.

: DAG de ejecución diaria programada para extraer los encuentros y cuotas del mercado

: DAG de backfill diseñado para la ingesta masiva de datos históricos cronológicos de

* : ETL de normalización lingüística de entidades y estandarización de husos horarios a UTC.

## **FASE 2: Ingeniería de Características (Features)**

* **2.1 Segmentación de Eventos:** Clasificación y agregación cuantitativa de variables in-play en ventanas de tiempo deslizantes de 5 y 10 minutos para medir volumen de presión.

* **2.2 Calibración de Prioris de Markov:** Generación empírica de matrices de transición de probabilidad condicional basadas en datos históricos: ***P(Estado\_t | Estado\_{t-1}, ΔGoles, Minuto)***, exportadas como estructuras JSON estáticas.

* **2.3 Parámetros de Hawkes:** Estimación por Máxima Verosimilitud (MLE) de la tasa base de disparo ***λ0*** por equipo y el parámetro de decaimiento sistémico ***β*** global.

* **2.4 Ajustes Estáticos y Exógenos:** Aplicación de medias móviles con decaimiento exponencial (EWMA, vida media \= 14 días). Codificación One-Hot para localía y normalización MinMax para el diferencial de altitud.

## **FASE 3: Modelado Predictivo (El Cerebro)**

Ejecución secuencial y jerárquica de la arquitectura matemática:

1. ### **Modelo Base de Goles (Dixon-Coles)**

Implementación de una regresión de Poisson bivariante modificada para incorporar el factor de subdispersión de empates a cero goles. Determina las fuerzas inherentes de ataque (***Atki***) y defensa (***Defi***) mediante optimización  
log-línea en	.

2. ### **Actualización Bayesiana del Estado (Filtro de Kalman)**

Los parámetros estáticos del Dixon-Coles se definen como variables de estado dinámicas. Al finalizar cada jornada, el filtro ejecuta el paso de predicción y actualización:

***Xt \= \[Atki(t), Defi(t)\]T***

Esto permite capturar rachas, crisis internas y cambios súbitos en la dinámica real de los equipos sin sobrentrenar el modelo histórico.

3. ### **Simulación Táctica In-Play (Cadenas de Markov)**

Durante el desarrollo del directo, se definen tres estados macro: 0 \= Equilibrio, 1 \= Repliegue defensivo, 2 \= Asedio ofensivo. La tasa de gol basal derivada del modelo previo se escala dinámicamente mediante los multiplicadores empíricos de la cadena en simulaciones de Montecarlo:

* Si Estado \= 1 (Repliegue): ***λ \* \= 0.75***

  * Si Estado \= 2 (Asedio): ***λ \* \= 1.25***

  4. ### **Modelado de Inercia Emocional (Proceso de Hawkes)**

Para modelar la volatilidad extrema inmediata a un evento crítico (el efecto "gol engendra gol" o desorganización por tarjeta), la intensidad instantánea de eventos se rige por:

***λ(t) \= λbase(t) \+ Σ α · e\-β(t \- ti)***

Donde ***α*** representa la reactividad emocional entrenada de forma individualizada para cada equipo ante la ocurrencia de eventos en el instante ***ti***.

## **FASE 4: Validación y Backtesting (Control de Calidad)**

* **4.1 Validación Cruzada Temporal:** Uso estricto deventanas expansivas ( Expanding Window ) para evitar filtración de información del futuro. Entrenamiento inicial en 2020-2023, validación fuera de muestra en 2024\.

  * #### **4.2 Métricas Cuantitativas de Calibración:**

    * : Evaluación estricta de las probabilidades del mercado 1X2.

      * 	: Medición de la calibración de la incertidumbre para verificar si los eventos mapeados al 70% ocurren exactamente el 70% de las veces.

    * **4.3 Optimización Financiera (Rediseño Crítico):** En lugar de apostar 1 unidad fija en base a una probabilidad arbitraria, se implementa el **Criterio de Kelly Modificado por Fracción (f \= 0.25)**:

***f\* \= (p · b \- q) / b***

Donde ***p*** es la probabilidad estimada, ***b*** son las cuotas decimales \- 1, y ***q \= 1 \- p***. El paso a producción exige un ROI simulado continuo superior al 5%.

## **FASE 5: Integración y Despliegue (Producción)**

* **5.1 Capa de Servicios (FastAPI):** Desarrollo de endpoints asíncronos de baja latencia. POST /predict/

para la inferencia de probabilidades basales e iniciales, y

el cálculo en streaming del vector de intensidades Hawkes de eventos en tiempo real.

para

* **5.2 Aislamiento y Orquestación de Contenedores:** Empaquetamiento de la API con Docker. Definición de un

entorno multi-contenedor vía	que levanta la API de Python, la base de datos PostgreSQL y

una instancia de Redis como memoria caché de alto rendimiento en vivo.

* **5.3 Infraestructura en la Nube:** Despliegue elástico automatizado sobre AWS ECS o Google Cloud Run protegido por balanceadores de carga (ALB).

  * **5.4 Interfaz de Notificaciones (Bot de Telegram):** Robot interactivo escrito con	que

expone los comandos

y

, entregando cuotas justas estimadas

comparadas contra las de las casas de apuestas (Value Detection).

## **FASE 6: Escalabilidad y MLOps (Mantenimiento)**

* **6.1 Arquitectura de Software Desacoplada:** Implementación de patrones de diseño de fábrica y estrategia

mediante clases abstractas puras ( EventParser ,

). Esto permite la sustitución transparente

del proveedor de datos (ej. ESPN a Opta) sin alterar los núcleos matemáticos.

* **6.2 Telemetría y Observabilidad:** Emisión de logs estructurados JSON distribuidos hacia Prometheus para el rastreo del rendimiento de inferencia, complementado con dashboards de Grafana para monitorizar en tiempo real el desvío del ROI real frente al backtesting.

  * **6.3 Pipeline de Reentrenamiento Automatizado:** Tarea semanal en Airflow ejecutada los lunes a las 00:00 UTC. Descarga los nuevos datos reales del fin de semana, re-estima los vectores de parámetros mediante MLE, y guarda los artefactos binarios (.pkl/.json) en un bucket de Amazon S3, provocando una recarga en caliente de los modelos en la API sin caída del servicio.

    * **6.4 Abstracción Multideporte:** Capacidad de extensión modular de la lógica estocástica subyacente. Para transicionar a deportes de alta frecuencia como la NBA, se prevé la sustitución paramétrica de la distribución de Poisson por procesos de difusión de Wiener o distribuciones continuas adaptadas, reutilizando la estructura de transiciones de Markov.