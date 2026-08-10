# Fase 115 — plan verificable de implementación

Implementar una Telegram Mini App híbrida sin modificar modelos ni salidas
oficiales. La aplicación Next.js debe consumir exclusivamente la API DIKAMAHA
mediante un BFF server-side que valide `Telegram.WebApp.initData`, conserve la
API key fuera del navegador y aplique sesiones seguras, CSRF, autorización y
rate limit por usuario.

La UI incluye dashboard, próximos, live, detalle de predicción, modelos,
suscripciones, ajustes y un Centro de datos con paridad completa del bot:
ligas, fechas, partidos históricos, contexto, play-by-play, estadísticas por
periodo, equipos, plantillas y perfiles de jugador. Debe ser mobile-first, usar
el tema Telegram y mostrar Markov Live, Hawkes residual y combinado como capas
separadas `shadow`. El catálogo live refresca por HTTP cada 20 segundos y el
detalle cada 10 segundos. DIKAMAHA inspecciona D-1/D/D+1 cuando no existe fecha
explícita, y la vista coloca equipos/logos, estadísticas y cronología antes de
las predicciones en tiempo real.

PostgreSQL persiste usuarios, favoritos, suscripciones y entregas. Se permiten
10 favoritos y 20 suscripciones activas por usuario, cooldown mínimo de 300 s
y dedupe por `subscription_id + event_key`. Un worker separado consulta sólo
DIKAMAHA, envía alertas con `sendMessage` y nunca usa `getUpdates`.

El bot existente añade un botón `web_app`, configura el menú global y conserva
todos sus comandos como fallback. Las capacidades de consulta se reflejan en
la Mini App mediante una allowlist cerrada de rutas BFF `/v1/explorer/*`. No se
añaden llamadas ESPN, cuotas, ROI, Kelly, stakes ni recomendaciones. El gate
exige pruebas TypeScript/Python, navegación E2E, conexión BFF/API, migraciones,
builds Docker, revisión de secretos y smoke Railway.
