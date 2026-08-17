-- Acceso heredado para las cuentas aprobadas antes del corte de cobro.
--
-- Va en su propia migración, separada de `0004`, para que se pueda revisar y
-- deshacer sola: `0004` es infraestructura y es permanente, esto es una
-- decisión comercial con fecha.
--
-- Quien ya usaba DIKAMAHA cuando todo era gratis no puede despertarse un día
-- con menos producto del que tenía. Se le concede premium **hasta una fecha
-- fija y explícita**, no indefinido: un premium sin caducidad es una
-- suscripción que no puede vencer nunca, y regalarla de por vida a la base
-- instalada haría imposible el nivel de pago desde el primer día.
--
-- El aviso del vencimiento es obligatorio y va aparte, con al menos 14 días de
-- antelación (ver `docs/runbooks/telegram_stars_subscriptions.md`): pasar a
-- todo el mundo de premium a free en una fecha que nunca se les comunicó es la
-- forma más rápida de perder la base de usuarios actual.
--
-- Idempotente por `plan_source = 'default'`. Una segunda ejecución del runner:
--   * no toca a quien ya pagó        (`plan_source = 'stars'`),
--   * no reextiende a quien ya heredó (`plan_source = 'grandfathered'`),
--   * no resucita a quien se degradó después (ya no cumple `plan = 'free'`
--     junto a `plan_source = 'default'` si se le tocó a mano),
--   * no toca a los administradores  (`plan_source = 'admin'`).

UPDATE "miniapp_users"
   SET "plan" = 'premium',
       "plan_source" = 'grandfathered',
       "plan_expires_at" = TIMESTAMPTZ '2026-11-01 00:00:00+00',
       "plan_updated_at" = now()
 WHERE "status" = 'active'
   AND "plan" = 'free'
   AND "plan_source" = 'default'
   AND "approved_at" IS NOT NULL
   AND "approved_at" < TIMESTAMPTZ '2026-09-01 00:00:00+00';
