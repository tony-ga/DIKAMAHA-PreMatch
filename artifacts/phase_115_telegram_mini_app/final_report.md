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

## Evidencia local

- `npm audit`: 0 vulnerabilidades.
- TypeScript: aprobado.
- Vitest: 12 aprobadas.
- Playwright móvil: 4 aprobadas.
- Python integral: 535 aprobadas y 8 integraciones opcionales omitidas.
- build Next.js producción: aprobado.
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

Estado: `railway_deployed_private_smoke_ready`. Quedan el smoke interactivo de
un usuario permitido, el short name de BotFather para enlaces `startapp` y la
prueba real de una suscripción antes de habilitar alertas.
