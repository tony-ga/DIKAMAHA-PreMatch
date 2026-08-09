# Fase 115 — informe de implementación

## Alcance entregado

- Mini App Next.js 16/TypeScript mobile-first con dashboard, live, próximos,
  predicciones, modelos, alertas y ajustes.
- BFF same-origin con validación HMAC de Telegram, expiración de `auth_date`,
  rechazo de grupos, allowlist/modo público, sesión firmada y CSRF.
- PostgreSQL con migración versionada, ownership, límites concurrentes por
  usuario y dedupe de entregas.
- worker de alertas con polling DIKAMAHA, backoff acotado y sólo `sendMessage`.
- integración del bot mediante `web_app`, `setChatMenuButton` y enlaces
  `startapp` a fixtures.
- Markov Live, residual Hawkes y combinado separados y rotulados shadow; no se
  alteraron modelos, probabilidades, snapshots ni router oficial.
- Centro de datos con paridad completa para ayuda/estado, ligas, fechas,
  partidos históricos, contexto, play-by-play, estadísticas por periodo,
  equipos, plantillas y perfiles de jugador.
- BFF explorer con allowlist cerrada; el navegador no puede elegir rutas
  upstream ni recibir la clave DIKAMAHA.

## Evidencia local

- `npm audit`: 0 vulnerabilidades.
- TypeScript: aprobado.
- Vitest: 16 aprobadas.
- Playwright móvil: 7 aprobadas.
- Python integral: 536 aprobadas y 8 integraciones opcionales omitidas.
- build Next.js producción: aprobado.
- conexión real: readiness, modelos, ligas, fechas y próximos respondieron con
  contrato válido; prueba BFF autenticada confirmó el header sólo servidor.
- build Docker: 258,170,601 bytes, aprobado como usuario `node`.
- smoke de contenedor: HTTP 200, sin advertencias de arranque.
- PostgreSQL 17: migración idempotente, límites 10/20 y dedupe aprobados.
- bundle cliente: cero secretos y cero URLs ESPN.

## Evidencia Railway

- PostgreSQL, `telegram-miniapp` y `telegram-alert-worker`: `Online`.
- URL pública: `https://telegram-miniapp-production-cbab.up.railway.app`.
- `/api/health`: `{"status":"ready","database":true}`.
- acceso privado habilitado; `initData` ausente falla cerrado con HTTP 400.
- bot premium activo sobre el commit de Fase 115 y log
  `telegram_miniapp_menu_configured` confirmado.
- worker activo con log `telegram_alert_worker_started` y `enabled: false`.
- una colisión `getUpdates` apareció durante el rolling deploy del bot y no se
  repitió después de retirar el contenedor anterior.

Estado: `railway_deployed_private_bot_parity_ready`. El commit `95946d7` quedó
activo en el deployment `975c34b7-da2c-4cc2-8b07-eb00c67764bb` de la Mini App.
Railway reportó éxito; health devolvió `ready` con base conectada, `/explore`
respondió 200 y readiness sin sesión respondió 401. Todos los servicios
quedaron `Online`; el worker reconstruido confirmó `enabled: false`. Quedan el
smoke interactivo de un usuario permitido, el short name de BotFather para
enlaces `startapp` y la prueba real de una suscripción antes de habilitar
alertas.
