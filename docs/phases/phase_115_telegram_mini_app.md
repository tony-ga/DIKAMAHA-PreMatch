# Fase 115 — Telegram Mini App híbrida

## Objetivo

Convertir la interfaz premium en un dashboard móvil dentro de Telegram sin
duplicar datos ni inferencia. La Mini App consume exclusivamente la API
DIKAMAHA autenticada; el bot nativo conserva comandos, long polling y fallback.

## Arquitectura congelada

- aplicación Next.js/TypeScript en un servicio Railway independiente;
- BFF server-side que valida `Telegram.WebApp.initData` y mantiene
  `DIKAMAHA_API_KEY` fuera del navegador;
- PostgreSQL Railway separado para usuarios, favoritos, suscripciones y
  entregas idempotentes;
- worker de alertas sin `getUpdates`, con polling de DIKAMAHA cada 30 segundos;
- bot premium con botón `web_app` y `setChatMenuButton`;
- refresco visual HTTP cada 25 segundos, sin WebSocket ni SSE en v1.

## Alcance visible

- dashboard, próximos, live, detalle pre-match, modelos, suscripciones y
  ajustes;
- Centro de datos con ligas, fechas, partidos históricos, contexto,
  play-by-play, estadísticas 1T/2T/total, equipos, plantillas y perfiles;
- Predicciones como destino principal, próximos multiliga en ventana de 14
  días, búsqueda global de equipos tolerante a acentos y filtros live;
- identidad de equipos preservada desde el catálogo hasta el detalle, con
  recuperación BFF para enlaces anteriores cuyo payload predictivo sólo trae
  IDs;
- detalle pre-match con gráfica 1X2, indicadores derivados explícitamente no
  calibrados, barras xG/mercados diferenciadas y tabla comparativa de
  probabilidad, goles esperados e intensidad;
- logos y retratos sólo cuando el proveedor publica PNG transparente,
  transportados por API/BFF y nunca usados como features;
- Markov Live, Hawkes residual y combinado separados y rotulados `shadow`;
- alertas de kickoff, marcador, estado, probabilidad, modelo y mercados shadow;
- tema Telegram claro/oscuro, safe areas y navegación inferior persistente.

## Controles

- no hay llamadas ESPN desde navegador, Mini App, bot o worker;
- `initDataUnsafe` nunca autoriza; la firma, fecha y usuario se validan en BFF;
- cookies `HttpOnly`, `Secure`, `SameSite=Lax` y CSRF en mutaciones;
- modo `private|public` y allowlist conservados;
- máximo 10 favoritos, 20 suscripciones activas y cooldown mínimo de 300 s;
- deduplicación `subscription_id + event_key`;
- sin cuotas, ROI, CLV, Kelly, stakes ni ejecución de apuestas;
- router, probabilidades, artefactos y promoción permanecen intactos.

## Gate de salida

1. Firma Telegram válida crea sesión; firma, fecha, chat o usuario inválidos
   fallan cerrados.
2. La clave DIKAMAHA no aparece en bundles, respuestas ni logs.
3. Las rutas BFF conservan los contratos API y manejan error/vacío/reintento.
4. PostgreSQL aplica límites, ownership y dedupe bajo concurrencia.
5. El worker no consume updates y no duplica alertas tras reinicio.
6. Botón Mini App, enlaces `startapp` y fallback nativo funcionan.
7. Pruebas TypeScript/Python, builds Docker y smoke Railway aprobados.
8. Las salidas live preservan separación Markov/Hawkes/combinado y etiquetas
   shadow.
9. La matriz bot/Mini App tiene paridad completa y toda consulta explorer pasa
   por una allowlist BFF autenticada.

## Estado operativo

`railway_deployed_private_bot_parity_ready`.

- Mini App y PostgreSQL están `Online`; health remoto `ready`.
- El acceso está habilitado únicamente para la allowlist privada.
- El worker está `Online` con alertas desactivadas y una sola réplica.
- El bot activo confirmó `telegram_miniapp_menu_configured`.
- La activación de alertas exige primero smoke interactivo y dedupe real.
- Los enlaces directos `startapp` requieren completar el short name en
  BotFather; el botón global y el botón `web_app` no dependen de ese paso.
- La extensión de paridad del bot está desplegada desde `95946d7` y validada
  con 536 pruebas Python, 16 Vitest, 7 Playwright, build Next y conexión real a
  cinco contratos DIKAMAHA.
- Railway confirmó deployment exitoso; health y Centro de datos responden 200,
  la ruta autenticada falla cerrada sin sesión y el worker permanece apagado
  lógicamente.
- La corrección DEC-150 está desplegada. El smoke real autenticado confirmó
  sesión 200, próximos 200 con 18 ligas y logos, Barnet en búsqueda global,
  live sobre 18 ligas sin fallos parciales, PNG transparente 200 y una
  predicción 200 con 1T, 2T, total y probabilidades oficiales.
- La regresión de identidad Cruzeiro–Mirassol y los recursos
  estadísticos pasaron 543 pruebas Python, 16 Vitest, 8 Playwright, typecheck y
  build Next. Este cambio es exclusivamente de presentación y no altera
  modelos, probabilidades ni promoción.
- El commit `8aa3aca` fue integrado mediante PR #13 y desplegado por Railway.
  Mini App, bot y worker reportaron `SUCCESS`; `/api/health`, `/predictions` y
  `/v1/health` respondieron HTTP 200 después del merge `525aab2`.
