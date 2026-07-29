1\. DEFINICIÓN Y PROPÓSITO  
El Punto 0.4 constituye la columna vertebral matemática de todo el proyecto. No es código, sino el plano lógico que define:

Qué modelo matemático se encarga de cada capa de la realidad.

El orden jerárquico en que se ejecutan (la salida de uno es la entrada del siguiente).

Cómo se comunican entre sí (qué parámetros se pasan y en qué formato).

Este diseño en papel es lo que diferencia a un científico de datos de un aficionado. Mientras otros usan un XGBoost monolítico, este sistema será físicamente interpretable.

2\. LA ARQUITECTURA DE 4 CAPAS (EL "MOTOR DE COMBUSTIÓN")  
El sistema no tendrá un único modelo. Tendrá 4 modelos anidados, cada uno resolviendo una dimensión diferente del problema. La jerarquía es ESTRICTA Y NO NEGOCIABLE.

CAPA 1: DIXON-COLES (El "Esqueleto Estático")  
Aspecto	Descripción  
Función	Estimar la fuerza bruta de cada equipo basada en toda la temporada. Es el modelo más simple y el que da la línea base.  
Entrada	Datos históricos de goles (local/visitante) de los últimos 2 años.  
Salida	Atk\_i (Fuerza Ofensiva) y Def\_i (Fuerza Defensiva) para cada equipo i.  
Ecuación	λ\_local \= Atk\_local \* Def\_visitante \* Localía  
Naturaleza	Estático (no cambia en el tiempo).  
CAPA 2: FILTRO DE KALMAN (El "Reloj de Arena")  
Aspecto	Descripción  
Función	Tomar la fuerza estática de la Capa 1 y actualizarla partido a partido. Reconoce que un equipo no es el mismo en septiembre que en marzo.  
Entrada	Atk\_i y Def\_i de la Capa 1 (como estado inicial) \+ los resultados de cada partido en orden cronológico.  
Salida	Atk\_i(t) y Def\_i(t), que son funciones del tiempo. Un valor diferente para cada jornada.  
Física	Aplica el concepto de inercia bayesiana: si un equipo gana 5-0, el filtro ajusta su fuerza, pero con una confianza limitada (podría ser un accidente estadístico).  
Naturaleza	Dinámico (evoluciona en el tiempo).  
CAPA 3: CADENA DE MARKOV DE 3 ESTADOS (El "DT Táctico")  
Aspecto	Descripción  
Función	Introducir el contexto situacional. No es lo mismo jugar con 0-0 que con 2-0 en contra. Modifica la intensidad de ataque según el marcador y el minuto.  
Entrada	Atk\_i(t) y Def\_i(t) de la Capa 2 (fuerza actual). El marcador simulado en cada minuto. La matriz de transición histórica.  
Salida	Un multiplicador táctico M que vale: 1.0 (Equilibrio), 1.25 (Asedio), 0.75 (Repliegue).  
Estados	0 \= Equilibrio (juego balanceado). 1 \= Repliegue (equipo se cierra atrás). 2 \= Asedio (equipo va a por todas).  
Naturaleza	Probabilística (Cadena de Markov).  
CAPA 4: PROCESO DE HAWKES (El "Efecto Mariposa")  
Aspecto	Descripción  
Función	Añadir la micro-inercia de los últimos minutos. Un gol, un tiro al palo o una tarjeta roja generan un pico de intensidad que decae exponencialmente.  
Entrada	λ\_base(t) (de las capas anteriores). Lista de eventos ocurridos en los últimos 15 minutos (goles, tiros, corners).  
Salida	λ\_real(t) \= λ\_base(t) \+ Σ α \* e^(-β\* Δt).  
Física	El parámetro α (efecto contagio) es específico de cada equipo. Un equipo como el Atlético de Madrid tiene α bajo (no se descompone). Un equipo joven tiene α alto (se viene abajo).  
Naturaleza	Proceso puntual autocatalítico.

4\. LA JERARQUÍA ESTRICTA (ORDEN DE EJECUCIÓN)  
Este orden es MANDATORIO. No se puede alterar.

Primero: Dixon-Coles (Capa 1). Sin esto, no tienes fuerza base para que Kalman actualice.

Segundo: Kalman (Capa 2). Sin esto, la Markov estaría usando fuerzas estáticas de hace 6 meses (error brutal).

Tercero: Markov (Capa 3). Sin esto, el Hawkes no tendría contexto táctico (multiplicaría igual un ataque en el minuto 10 que en el 85).

Cuarto: Hawkes (Capa 4). Sin esto, solo tendrías un modelo pre-partido, perdiendo el potencial de Live Betting.

Si cambias el orden, el sistema se rompe físicamente: estarías sumando inercias cortas sin saber si el equipo está en modo "asedio" o "repliegue", y los multiplicadores se dispararían a valores absurdos.

5\. ESCALABILIDAD Y ADAPTABILIDAD  
El Punto 0.4 es la clave de la escalabilidad porque define interfaces (contratos) entre capas:

Cambio	Acción	Capas Afectadas  
Cambiar fuente de datos (ESPN → Opta)	Reescribir el parseador de eventos (Fase 1).	Ninguna (Capas 1-4 reciben el mismo DataFrame).  
Añadir nueva liga (Premier League)	Entrenar los parámetros con los nuevos datos.	Todas (se recalculan Atk/Def, α, β y matrices de Markov).  
Cambiar de deporte (Fútbol → NBA)	Cambiar la distribución base (Poisson → Normal para puntos).	Solo Capa 1\. El resto (Kalman, Markov, Hawkes) son agnósticos al deporte.  
6\. IMPLEMENTACIÓN PRÁCTICA (EJEMPLO DE FLUJO)  
Para ilustrar el flujo, supongamos un partido Real Madrid vs Barcelona en el minuto 65 con marcador 0-1:

Capa 1+2: El sistema sabe que el Real Madrid tiene Atk=1.8 en este momento de la temporada y el Barcelona Def=0.9. La base es 1.8 \* 0.9 \= 1.62.

Capa 3 (Markov): Como el Real Madrid pierde 0-1 en el minuto 65, la matriz de transición indica que hay un 80% de probabilidad de que esté en Estado 2 (Asedio). Se aplica el multiplicador M \= 1.25. La nueva base es 1.62 \* 1.25 \= 2.025.

Capa 4 (Hawkes): En el minuto 60, el Real Madrid tuvo un tiro al palo. Eso genera un pico de excitación. Si α=0.5 y β=0.1, el aporte es 0.5 \* e^(-0.1\*5) \= 0.30. La intensidad real es 2.025 \+ 0.30 \= 2.325.

Resultado: El sistema predice que en los próximos 10 minutos, la probabilidad de que el Real Madrid marque es significativamente más alta que la media de la liga.

