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

## Estado inicial

`implementation_authorized`.
