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

La validación Railway y el smoke Telegram real se agregan después del primer
despliegue controlado. Hasta entonces, alertas y acceso permanecen apagados por
defecto mediante variables de entorno.
