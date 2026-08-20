-- Cobro web con Stripe (Fase 133, DEC-220).
--
-- Telegram Stars sólo existe dentro de Telegram: en un navegador
-- `openInvoice` no tiene a quién llamar. Esta migración añade la segunda
-- pasarela **sin** duplicar el modelo de titularidad: el plan sigue siendo
-- `plan` + `plan_expires_at` sobre `miniapp_users`, y `effective_plan()` no se
-- toca. Lo único que se amplía es la procedencia.
--
-- Como el resto, este runner reaplica todas las migraciones en cada despliegue
-- (`preDeployCommand = npm run db:migrate`) y no lleva tabla de ledger: todo lo
-- de aquí es idempotente y se puede ejecutar N veces sin cambiar el resultado.

-- ---------------------------------------------------------------------------
-- 1. Procedencia del plan: aparece 'stripe'
-- ---------------------------------------------------------------------------

-- Sin esto, el primer pago web violaría el CHECK de
-- `0004_premium_plan_and_star_billing.sql` y el usuario pagaría sin recibir
-- nada. `DROP ... IF EXISTS` seguido de `ADD` es idempotente y conserva el
-- dominio anterior íntegro: no se retira ningún valor, sólo se suma uno.
DO $$
BEGIN
  ALTER TABLE "miniapp_users"
    DROP CONSTRAINT IF EXISTS "miniapp_users_plan_source_check";
  ALTER TABLE "miniapp_users"
    ADD CONSTRAINT "miniapp_users_plan_source_check"
    CHECK ("plan_source" IN
      ('default', 'stars', 'stripe', 'grandfathered', 'admin', 'refunded'));
END $$;

-- ---------------------------------------------------------------------------
-- 2. Identidad del cliente en Stripe
-- ---------------------------------------------------------------------------

-- Una fila por usuario. Existe para no crear un cliente nuevo en Stripe cada
-- vez que alguien abre el checkout: sin esta tabla, un usuario que empieza y
-- abandona tres veces deja tres clientes distintos y su historial de pagos
-- queda partido en tres.
CREATE TABLE IF NOT EXISTS "stripe_customers" (
  "user_id"            bigint PRIMARY KEY
                         REFERENCES "miniapp_users" ("telegram_user_id")
                         ON DELETE CASCADE,
  "stripe_customer_id" text NOT NULL UNIQUE,
  "created_at"         timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 3. Estado de la suscripción Stripe
-- ---------------------------------------------------------------------------

-- Espejo deliberado de `star_subscriptions`: mismas columnas de estado y de
-- periodo, para que el panel de administración y el barrido de vencimiento
-- puedan tratar las dos pasarelas con la misma forma. A diferencia de Stars,
-- aquí el identificador de suscripción **no** cambia en cada cobro, así que no
-- hace falta perseguir el último cargo.
CREATE TABLE IF NOT EXISTS "stripe_subscriptions" (
  "user_id"                bigint PRIMARY KEY
                             REFERENCES "miniapp_users" ("telegram_user_id")
                             ON DELETE CASCADE,
  "stripe_subscription_id" text NOT NULL UNIQUE,
  "stripe_customer_id"     text NOT NULL,
  "price_id"               text NOT NULL,
  "current_period_end"     timestamptz NOT NULL,
  "status"                 text NOT NULL DEFAULT 'active',
  "cancel_requested_at"    timestamptz,
  "charge_count"           integer NOT NULL DEFAULT 1,
  "created_at"             timestamptz NOT NULL DEFAULT now(),
  "updated_at"             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT "stripe_subscriptions_status_check"
    CHECK ("status" IN ('active', 'canceled', 'expired', 'refunded'))
);

CREATE INDEX IF NOT EXISTS "stripe_subscriptions_period_idx"
  ON "stripe_subscriptions" ("current_period_end")
  WHERE "status" = 'active';

-- ---------------------------------------------------------------------------
-- 4. Libro mayor de eventos: lo que hace idempotente el webhook
-- ---------------------------------------------------------------------------

-- Mismo papel que `star_payments` para Stars, y por la misma razón: Stripe
-- **reintenta** los webhooks hasta recibir un 2xx, y puede entregar el mismo
-- evento más de una vez incluso sin fallos. Con la clave primaria puesta en el
-- identificador de evento de Stripe, aplicar un pago es idempotente por
-- construcción y no por cuidado del que escribe el manejador.
CREATE TABLE IF NOT EXISTS "stripe_events" (
  "event_id"     text PRIMARY KEY,
  "user_id"      bigint REFERENCES "miniapp_users" ("telegram_user_id")
                   ON DELETE CASCADE,
  "event_type"   text NOT NULL,
  "raw_payload"  jsonb NOT NULL DEFAULT '{}'::jsonb,
  "applied_at"   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS "stripe_events_user_idx"
  ON "stripe_events" ("user_id", "applied_at" DESC);
