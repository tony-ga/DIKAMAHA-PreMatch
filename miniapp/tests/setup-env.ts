/**
 * Secretos de infraestructura para toda la suite.
 *
 * `MINIAPP_INTERNAL_API_KEY` y `MINIAPP_BILLING_SECRET` no tienen valor por
 * defecto en `lib/env.ts` -igual que `MINIAPP_SESSION_SECRET`-, porque un
 * servicio que sirve endpoints internos con una clave adivinable debe negarse
 * a arrancar. Eso hace que sin ellos falle cualquier prueba que toque `env()`,
 * aunque no tenga nada que ver con el cobro.
 *
 * Se fijan aquí y no en cada archivo porque son una precondición del proceso,
 * no el sujeto de ninguna prueba. Las que sí prueban el cobro sobrescriben lo
 * que necesiten con `vi.stubEnv`.
 *
 * El cobro queda **apagado** por defecto, que es el estado en el que el resto
 * de la suite debe seguir comportándose exactamente como antes de la Fase 125.
 */
process.env.MINIAPP_INTERNAL_API_KEY ??= "test-internal-key-0123456789abcdef";
process.env.MINIAPP_BILLING_SECRET ??= "test-billing-secret-0123456789abcdef";
process.env.MINIAPP_BILLING_ENABLED ??= "false";
