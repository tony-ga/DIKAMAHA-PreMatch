# Fase 115 — plan verificable de implementación

Implementar una Telegram Mini App híbrida sin modificar modelos ni salidas
oficiales. La aplicación Next.js debe consumir exclusivamente la API DIKAMAHA
mediante un BFF server-side que valide `Telegram.WebApp.initData`, conserve la
API key fuera del navegador y aplique sesiones seguras, CSRF, autorización y
rate limit por usuario.

La UI incluye dashboard, próximos, live, detalle de predicción, modelos,
suscripciones y ajustes. Debe ser mobile-first, usar el tema Telegram y mostrar
Markov Live, Hawkes residual y combinado como capas separadas `shadow`. El
refresco live es HTTP cada 25 segundos.

PostgreSQL persiste usuarios, favoritos, suscripciones y entregas. Se permiten
10 favoritos y 20 suscripciones activas por usuario, cooldown mínimo de 300 s
y dedupe por `subscription_id + event_key`. Un worker separado consulta sólo
DIKAMAHA, envía alertas con `sendMessage` y nunca usa `getUpdates`.

El bot existente añade un botón `web_app`, configura el menú global y conserva
todos sus comandos como fallback. No se añaden llamadas ESPN, cuotas, ROI,
Kelly, stakes ni recomendaciones. El gate exige pruebas TypeScript/Python,
migraciones, builds Docker, revisión de secretos y smoke Railway.
