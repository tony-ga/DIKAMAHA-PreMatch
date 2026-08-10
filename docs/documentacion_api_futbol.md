# ⚽ Documentación de la API de Fútbol de ESPN (No Oficial)

Esta documentación detalla absolutamente todos los datos sobre fútbol (Soccer) que puedes extraer utilizando la API pública (y no documentada) de ESPN, basándonos en el repositorio `Public-ESPN-API`.

> [!NOTE]
> **Aviso:** Esta API no es oficial y no requiere autenticación, pero carece de soporte oficial y puede cambiar sin previo aviso. Es recomendable usar caché en tus aplicaciones y respetar los límites de peticiones (Rate Limiting).

## 🌐 URLs Base Principales

Para fútbol (el slug utilizado es `soccer`), los endpoints principales se construyen sobre las siguientes bases:

| API | URL Base | Propósito |
|---|---|---|
| **Site API v2** | `https://site.api.espn.com/apis/site/v2/sports/soccer/` | Datos listos para el usuario: marcadores, equipos, posiciones. |
| **Core API v2** | `https://sports.core.api.espn.com/v2/sports/soccer/` | Datos detallados, eventos, play-by-play, atletas, estadísticas. |
| **Core API v3** | `https://sports.core.api.espn.com/v3/sports/soccer/` | Esquemas enriquecidos, líderes y listas de atletas activos. |
| **CDN API** | `https://cdn.espn.com/core/soccer/` | Datos optimizados de partidos en vivo. |

---

## Nota operativa DIKAMAHA

En el entorno actual, `site.api.espn.com` puede responder HTTP 403 de Akamai.
El adaptador mantiene ese dominio como primario y, sólo para ese estado,
repite una vez el mismo path Site en `site.web.api.espn.com`. La prueba real
de 2026-08-08 obtuvo JSON 200 para scoreboard, summary y standings en el
fallback, y 200 para event y plays en Core. El cambio de host conserva tanto
`/apis/site/v2` como `/apis/v2`; la URL efectiva se conserva en provenance.

La CDN respondió HTTP 202 sin cuerpo y no se usa como fuente del modelo. Esto
no afecta Core ni autoriza probabilities u odds como features.

---

## 🏆 Ligas y Competiciones Soportadas

Para acceder a una liga específica, debes reemplazar `{league}` en las URLs con el **slug** correspondiente de la liga. Algunos de los más destacados son:

### 🌍 Internacionales / FIFA
* **Mundial:** `fifa.world`, `fifa.wwc` (Femenino), Mundiales Sub-20 y Sub-17.
* **Mundial de Clubes:** `fifa.cwc`, `fifa.intercontinental_cup`.
* **Amistosos:** `fifa.friendly`, `fifa.friendly.w`.
* **Juegos Olímpicos:** `fifa.olympics`, `fifa.w.olympics`.
* **Clasificatorias:** `fifa.worldq` (y por confederaciones: `.uefa`, `.conmebol`, `.concacaf`, etc.).

### 🇪🇺 UEFA (Europa)
* **Champions League:** `uefa.champions`, `uefa.wchampions` (Femenina).
* **Europa League:** `uefa.europa`
* **Conference League:** `uefa.europa.conf`
* **Supercopa:** `uefa.super_cup`
* **Eurocopa:** `uefa.euro`, `uefa.weuro` (Femenina)
* **Nations League:** `uefa.nations`

### 🏴󠁧󠁢󠁥󠁮󠁧󠁿 Inglaterra
* **Ligas:** `eng.1` (Premier League), `eng.2` (Championship), `eng.3`, `eng.4`, `eng.5`.
* **Copas:** `eng.fa` (FA Cup), `eng.league_cup` (Carabao Cup).
* **Femenino:** `eng.w.1` (Women's Super League).

### 🇪🇸 España
* **Ligas:** `esp.1` (LaLiga), `esp.2` (LaLiga 2), `esp.w.1` (Liga F).
* **Copas:** `esp.copa_del_rey`, `esp.super_cup`.

### 🌎 América (CONMEBOL y CONCACAF)
* **CONMEBOL:** `conmebol.libertadores`, `conmebol.sudamericana`, `conmebol.america` (Copa América).
* **CONCACAF:** `concacaf.champions`, `concacaf.gold`, `concacaf.nations.league`, `usa.1` (MLS), `mex.1` (Liga MX), `arg.1` (Liga Argentina), `bra.1` (Brasileirão).
* **Otros:** Colombia (`col.1`), Chile (`chi.1`), Uruguay (`uru.1`), etc.

---

## 📡 Endpoints: Site API v2 (Para Apps y Sitios Web)
*Esta es la forma más fácil de obtener datos resumidos y útiles.*

**Patrón:** `GET https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/{recurso}`

| Recurso | Descripción |
|---|---|
| `/scoreboard` | Marcadores en vivo y partidos programados. Puedes añadir `?dates=YYYYMMDD` para un día específico. |
| `/teams` | Todos los equipos de la liga. |
| `/teams/{id}` | Detalles de un equipo específico. |
| `/teams/{id}/roster` | La plantilla (roster) del equipo. |
| `/teams/{id}/schedule` | El calendario de partidos del equipo. |
| `/teams/{id}/injuries` | Reporte de lesiones del equipo. |
| `/news` | Noticias sobre partidos o transferencias. |
| `/summary?event={id}` | Reporte completo del partido (eventos, alineaciones, etc). |

> [!WARNING]
> **Nota sobre la Tabla de Posiciones (Standings):**
> La ruta `/apis/site/v2/` devuelve un `{}` vacío para las tablas de posiciones. **Debes usar la ruta sin `/site/`**:
> `https://site.api.espn.com/apis/v2/sports/soccer/{league}/standings`

---

## ⚙️ Endpoints: Core API v2 & v3 (Datos Profundos y Específicos)

**Patrón General:** `https://sports.core.api.espn.com/v2/sports/soccer/leagues/{league}/...`
*Muchos de estos endpoints soportan los parámetros `?page=1` y `?limit=50`.*

### 📅 Temporadas y Calendario
* `/calendar`: Obtener las fechas inteligentes, grupos de partidos y semanas disponibles de la liga.
* `/seasons`: Temporadas jugadas en la liga.
* `/seasons/{season}/athletes`: Todos los atletas que participaron en esa temporada.
* `/seasons/{season}/freeagents`: Agentes libres.

### ⚽ Equipos y Atletas
* `/teams`: Equipos participantes.
* `/athletes`: Lista general de jugadores (puedes filtrar por `country`, `position`, `active`).

### 🏟️ Eventos (Partidos) y Estadísticas en Vivo
* `/events/{event}`: Datos de un partido (evento) en específico.
* `/events/{event}/competitions/{competition}`: Los datos del partido competitivo específico dentro del evento.
* `/events/{event}/competitions/{competition}/plays`: **Play-by-play** del partido (goles, tarjetas, sustituciones). *¡Usa limit=300!*
* `/events/{event}/competitions/{competition}/situation`: Situación del partido en tiempo real (quién tiene la posesión, contexto del partido).
* `/events/{event}/competitions/{competition}/probabilities`: Recurso opcional
  de probabilidades; su referencia no existe para muchas competiciones de
  fútbol y puede responder 400 aun con un evento válido.
* `/events/{event}/competitions/{competition}/odds`: Cuotas y apuestas (Odds).
* `/events/{event}/competitions/{competition}/officials`: Árbitros del encuentro.
* `/events/{event}/competitions/{competition}/broadcasts`: Información sobre la transmisión televisiva.

### Cobertura real de predictor, `probabilities` y `pickcenter`

La inspección DIKAMAHA de 2026-08-09 probó summaries de Colombia, Argentina,
Chile, Premier League, LaLiga, Champions League y Mundial, con y sin `ocp=1`.
En esa muestra no aparecieron `predictor`, `winprobability` ni SPI, y Core no
publicó una referencia utilizable a `probabilities`. Sí apareció `pickcenter`
en distintos eventos. Por tanto:

- las probabilidades analíticas son opcionales y deben tratarse como cobertura
  por fixture, no como garantía del endpoint;
- `ocp=1` no autoriza a asumir que el nodo existe;
- `pickcenter` contiene contexto de mercado y no equivale a SPI;
- DIKAMAHA sólo normaliza un 1X2 cuando local, empate y visitante vienen
  publicados explícitamente; en otro caso devuelve `not_published`;
- nunca se derivan probabilidades del predictor desde cuotas.

### 🥇 Tablas y Rankings
* `/standings`: Tabla de clasificación detallada.
* `/rankings`: Rankings oficiales de la liga.
* `/venues`: Estadios donde se juega la liga.

### 👑 Líderes y Goleadores (Stats)
* `/leaders`: Máximos goleadores, asistidores, etc. de la liga actual.
* `/seasons/{season}/leaders`: Líderes de una temporada pasada en específico.

### 🧬 Atletas Individuales (Core v2/v3)
La información de atletas individuales está algo limitada en `site.web.api`. Para ver a un jugador específico en fútbol de manera completa, usa:
* `https://sports.core.api.espn.com/v2/sports/soccer/leagues/{league}/athletes/{id}`
Para listar jugadores activos usa la **v3**:
* `https://sports.core.api.espn.com/v3/sports/soccer/{league}/athletes?limit=100&active=true`

---

## ⚡ Datos en Tiempo Real Optimizados (CDN)
Si necesitas consumir la información durante un partido en vivo de forma masiva (como en un widget), utiliza la CDN (requiere `?xhr=1`):

`GET https://cdn.espn.com/core/soccer/scoreboard?xhr=1&league=eng.1`

(Reemplaza `eng.1` con la liga que deseas). Esto devolverá un gran "paquete de juego" con los datos que consumen directamente desde el frontend de ESPN.

---

## 📊 Lista Completa de Datos Disponibles en la API

A continuación, un resumen estructurado de **todos los tipos de datos exactos** que puedes extraer si navegas por los distintos endpoints mencionados arriba:

### 1. Datos de Partidos (Eventos) y En Vivo
*   **Estado del Partido:** Programado, en curso (incluyendo minuto/reloj), medio tiempo, finalizado, pospuesto.
*   **Play-by-play (Jugada a Jugada):** Goles, tarjetas amarillas, tarjetas rojas, sustituciones, tiros de esquina, penales, fueras de juego, faltas.
*   **Alineaciones:** Titulares, suplentes, y formaciones tácticas (ej. 4-4-2, 4-3-3).
*   **Situación de Juego:** Quién tiene la posesión actual, en qué sector de la cancha están jugando.
*   **Probabilidades (cobertura opcional):** Porcentaje de victoria local,
    empate o visitante sólo cuando el fixture publica un nodo analítico
    explícito; no está disponible de forma universal.
*   **Cuotas (Odds):** Cuotas de apuestas de proveedores pre-partido y en vivo.
*   **Detalles del Evento:** Árbitros asignados, estadio/sede (nombre, capacidad, ciudad), información de la transmisión televisiva por país/región.

### 2. Datos de Equipos
*   **Información General:** Nombre completo, abreviatura, nombres alternativos, colores oficiales (hexadecimales).
*   **Multimedia:** URLs de los logotipos y escudos oficiales en alta resolución.
*   **Plantilla (Roster):** Lista completa de jugadores actuales del primer equipo.
*   **Estado del Equipo:** Lista de jugadores lesionados o suspendidos.
*   **Historial y Calendario:** Calendario de la temporada (partidos pasados y futuros), historial de enfrentamientos previos.

### 3. Datos de Jugadores (Atletas)
*   **Perfil:** Nombre, apellido, fecha de nacimiento, edad, país de nacimiento/nacionalidad, altura, peso, pie hábil (derecho/izquierdo).
*   **Multimedia:** URLs de fotos o "headshots" oficiales del jugador.
*   **Estado:** Activo, inactivo, lesionado (motivo de la lesión y fecha estimada de regreso).
*   **Estadísticas Acumuladas:** Goles totales, asistencias, minutos jugados, tarjetas acumuladas por temporada o torneo.
*   **Contratos/Transferencias:** Historial de transferencias o condición de agente libre.

### 4. Datos de Competiciones y Ligas
*   **Clasificación (Standings):** Tablas de posiciones detalladas con puntos, partidos jugados, ganados, empatados, perdidos, diferencia de goles (GF/GC), racha de últimos partidos (ej. W-W-L-D) y récord como local/visitante.
*   **Fases de Torneos:** Grupos de torneos (como grupos de Champions League) y rondas eliminatorias (bracket).
*   **Líderes Individuales:** Top goleadores del torneo, máximos asistidores, porteros con más vallas invictas.
*   **Información Histórica:** Acceso a temporadas pasadas y sus respectivos calendarios y estadísticas.

### 5. Contenido Editorial y Noticias
*   **Artículos:** Noticias recientes relevantes a una liga, un equipo o un jugador específico.
*   **Resúmenes:** Enlaces a reportes de partidos y metadatos de videos de resúmenes (highlights) si están disponibles regionalmente.

---

## Contrato live DIKAMAHA para la Mini App

La interfaz nunca consume estos recursos de ESPN directamente. `/v1/live`
consulta scoreboards D-1, D y D+1 cuando no se indica `date`, conserva una fecha
explícita sin ampliarla y deduplica fixtures por `match_id`. Esto evita vacíos
durante partidos que cruzan medianoche UTC.

`POST /v1/predict/live/fixture` localiza el día ESPN del encuentro, captura
scoreboard/event/plays/situation raw-first y devuelve de forma aditiva:

- `fixture`: equipos, logos PNG, marcador, periodo, reloj y timestamp;
- `observed_live_statistics`: goles autoritativos y conteos de tiros, córners,
  tarjetas, faltas, offsides, acciones detenidas y sustituciones;
- `recent_actions`: cronología relevante con equipo, minuto y texto proveedor;
- `match_dynamics`: matriz de 90 minutos, presión local positiva/visitante
  negativa, media móvil de cinco minutos y marcadores de gol;
- `experimental_markov_live`, `experimental_hawkes_residual` y
  `experimental_combined_live`, siempre separados y `shadow`;
- `automatic_refresh_recommended_seconds: 10`.

Las estadísticas son presentación derivada del play-by-play observado. No se
inyectan como features adicionales, no llaman probabilidades ESPN y no alteran
la salida oficial pre-match.

`GET /v1/provider/predictor?league={league}&event_id={id}&scope=pre_match|live`
consulta el summary detrás de la API DIKAMAHA y devuelve
`provider_match_context_v1`. El navegador sólo ve el BFF autenticado. Cuando
existe un triplete analítico explícito se muestra como benchmark adicional;
cuando no, responde `not_published`. `market_context` sólo informa si se
detectó `pickcenter`, con `consumed_by_models=false` y `odds_exposed=false`.
