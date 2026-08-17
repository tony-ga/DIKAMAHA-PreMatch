-- Nivel de pago real: Free contra Premium, cobrado con suscripción mensual en
-- Telegram Stars.
--
-- Hasta aquí `plan` existía pero no gobernaba nada. Se creó en
-- `0002_user_accounts.sql` con el comentario "gancho de titularidad para un
-- futuro nivel de pago; hoy sólo se transporta": viajaba dentro de la cookie
-- firmada y **ninguna ruta lo consultaba**. A partir de esta migración `plan`
-- más `plan_expires_at` deciden qué se sirve.
--
-- Este runner reaplica todas las migraciones en cada despliegue
-- (`preDeployCommand = npm run db:migrate`) y no lleva tabla de ledger, así que
-- todo lo de aquí es idempotente y se puede ejecutar N veces sin cambiar el
-- resultado.

-- ---------------------------------------------------------------------------
-- 1. Columnas de plan sobre la cuenta
-- ---------------------------------------------------------------------------

ALTER TABLE "miniapp_users"
  ADD COLUMN IF NOT EXISTS "plan_expires_at" timestamptz,
  -- Procedencia del plan. Distingue a quien pagó de quien heredó el acceso en
  -- el corte y de quien es administrador. Sin esta columna un backfill no se
  -- puede repetir sin arriesgarse a pisar una compra real.
  ADD COLUMN IF NOT EXISTS "plan_source" text NOT NULL DEFAULT 'default',
  ADD COLUMN IF NOT EXISTS "plan_updated_at" timestamptz NOT NULL DEFAULT now();

-- Normaliza cualquier valor fuera del dominio ANTES de que exista el CHECK.
-- Acotado a lo que no es 'free'/'premium' para no reescribir filas ya válidas.
UPDATE "miniapp_users" SET "plan" = 'free' WHERE "plan" NOT IN ('free', 'premium');

DO $$
BEGIN
  -- `IF NOT EXISTS` no existe para constraints; sin la guarda la segunda
  -- ejecución del runner fallaría con "constraint already exists".
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'miniapp_users_plan_check'
  ) THEN
    ALTER TABLE "miniapp_users"
      ADD CONSTRAINT "miniapp_users_plan_check"
      CHECK ("plan" IN ('free', 'premium'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'miniapp_users_plan_source_check'
  ) THEN
    ALTER TABLE "miniapp_users"
      ADD CONSTRAINT "miniapp_users_plan_source_check"
      CHECK ("plan_source" IN
        ('default', 'stars', 'grandfathered', 'admin', 'refunded'));
  END IF;
  -- Un premium sin fecha de caducidad es una suscripción que no puede vencer
  -- nunca. Se permite sólo para 'admin', que es deliberadamente perpetuo.
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'miniapp_users_plan_expiry_check'
  ) THEN
    ALTER TABLE "miniapp_users"
      ADD CONSTRAINT "miniapp_users_plan_expiry_check"
      CHECK ("plan" = 'free'
             OR "plan_source" = 'admin'
             OR "plan_expires_at" IS NOT NULL);
  END IF;
END $$;

-- La consulta caliente -"¿este usuario sigue siendo premium?"- va por clave
-- primaria y ya está cubierta. Este índice es para el barrido de degradación
-- del worker, que busca por caducidad.
CREATE INDEX IF NOT EXISTS "miniapp_users_plan_expiry_idx"
  ON "miniapp_users" ("plan_expires_at")
  WHERE "plan" = 'premium';

-- ---------------------------------------------------------------------------
-- 2. Precio, ajustable sin redesplegar
-- ---------------------------------------------------------------------------

-- El precio en Stars tiene que poder moverse sin publicar una versión: la
-- economía de XTR puede cambiar y el punto de equilibrio del proyecto está en
-- ~15 suscriptores, así que un error de precio se paga en meses. La variable de
-- entorno sólo siembra la fila; la fuente de verdad en runtime es esta tabla.
CREATE TABLE IF NOT EXISTS "billing_plans" (
  "code"           text PRIMARY KEY,
  "stars_amount"   integer NOT NULL,
  "period_seconds" integer NOT NULL,
  "active"         boolean NOT NULL DEFAULT true,
  "updated_at"     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT "billing_plans_amount_check"
    CHECK ("stars_amount" BETWEEN 1 AND 10000),
  -- Telegram sólo acepta 2592000 segundos -30 días- como periodo de
  -- suscripción Stars. Cualquier otro valor falla en `createInvoiceLink`, y
  -- fallar aquí es mucho más barato que fallar frente al usuario.
  CONSTRAINT "billing_plans_period_check" CHECK ("period_seconds" = 2592000)
);

-- `DO NOTHING` y no `DO UPDATE`: una reejecución del runner no puede pisar un
-- precio que se haya ajustado en caliente desde el panel de administración.
INSERT INTO "billing_plans" ("code", "stars_amount", "period_seconds")
VALUES ('premium_monthly', 250, 2592000)
ON CONFLICT ("code") DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3. Estado de la suscripción Stars
-- ---------------------------------------------------------------------------

-- Una fila por usuario. `telegram_payment_charge_id` cambia en **cada** cobro
-- recurrente: es la clave del cargo, no de la suscripción. Como
-- `editUserStarSubscription` exige el cargo más reciente para cancelar, la fila
-- lo persigue en `latest_charge_id` y conserva el primero sólo para auditoría.
CREATE TABLE IF NOT EXISTS "star_subscriptions" (
  "user_id"             bigint PRIMARY KEY
                          REFERENCES "miniapp_users" ("telegram_user_id")
                          ON DELETE CASCADE,
  "plan_code"           text NOT NULL DEFAULT 'premium_monthly',
  "latest_charge_id"    text NOT NULL,
  "first_charge_id"     text NOT NULL,
  "stars_amount"        integer NOT NULL,
  "current_period_end"  timestamptz NOT NULL,
  "status"              text NOT NULL DEFAULT 'active',
  "cancel_requested_at" timestamptz,
  "charge_count"        integer NOT NULL DEFAULT 1,
  "created_at"          timestamptz NOT NULL DEFAULT now(),
  "updated_at"          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT "star_subscriptions_status_check"
    CHECK ("status" IN ('active', 'canceled', 'expired', 'refunded'))
);

CREATE UNIQUE INDEX IF NOT EXISTS "star_subscriptions_latest_charge_uidx"
  ON "star_subscriptions" ("latest_charge_id");

-- ---------------------------------------------------------------------------
-- 4. Libro mayor de cargos: auditoría y deduplicación
-- ---------------------------------------------------------------------------

-- Esta tabla es la que hace converger al mismo estado tres caminos que pueden
-- ocurrir en cualquier orden: el reenvío del bot, su reintento, y la
-- reconciliación por `getStarTransactions`. La clave primaria es la natural de
-- Telegram, así que "aplicar un pago" es idempotente por construcción.
CREATE TABLE IF NOT EXISTS "star_payments" (
  "telegram_payment_charge_id" text PRIMARY KEY,
  "user_id"                    bigint NOT NULL
                                 REFERENCES "miniapp_users" ("telegram_user_id")
                                 ON DELETE CASCADE,
  "kind"                       text NOT NULL,
  "stars_amount"               integer NOT NULL,
  "invoice_payload"            text,
  "is_recurring"               boolean NOT NULL DEFAULT false,
  "is_first_recurring"         boolean NOT NULL DEFAULT false,
  "subscription_expiration"    timestamptz,
  -- Para saber si el camino primario sigue vivo: un `reconcile` significa que
  -- el reenvío del bot se perdió.
  "ingest_source"              text NOT NULL,
  "raw_payload"                jsonb NOT NULL DEFAULT '{}'::jsonb,
  "applied_at"                 timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT "star_payments_kind_check" CHECK ("kind" IN ('charge', 'refund')),
  CONSTRAINT "star_payments_source_check"
    CHECK ("ingest_source" IN ('bot_forward', 'reconcile', 'manual'))
);

CREATE INDEX IF NOT EXISTS "star_payments_user_idx"
  ON "star_payments" ("user_id", "applied_at" DESC);

-- Facturas emitidas. Ata un `invoice_payload` a un usuario y a un precio
-- concretos: sin ella el payload es sólo una afirmación firmada y no hay forma
-- de auditar qué se le ofreció a quién ni a cuánto.
CREATE TABLE IF NOT EXISTS "star_invoices" (
  "nonce"        text PRIMARY KEY,
  "user_id"      bigint NOT NULL
                   REFERENCES "miniapp_users" ("telegram_user_id")
                   ON DELETE CASCADE,
  "plan_code"    text NOT NULL,
  "stars_amount" integer NOT NULL,
  "origin"       text NOT NULL,
  "created_at"   timestamptz NOT NULL DEFAULT now(),
  "consumed_at"  timestamptz,
  CONSTRAINT "star_invoices_origin_check" CHECK ("origin" IN ('miniapp', 'bot'))
);

CREATE INDEX IF NOT EXISTS "star_invoices_user_idx"
  ON "star_invoices" ("user_id", "created_at" DESC);

-- ---------------------------------------------------------------------------
-- 5. Cuota diaria de predicciones del plan gratuito
-- ---------------------------------------------------------------------------

-- Dos tablas, por una razón concreta: la Mini App tiene
-- `refetchOnWindowFocus: true` global (`components/providers.tsx`), de modo que
-- una cuota contada **por petición** se consumiría sola al recuperar el foco de
-- la WebView. El contador hace la aritmética atómica; las concesiones hacen que
-- reabrir un partido ya desbloqueado sea gratis.
--
-- La promesa del plan gratuito es "3 predicciones al día de tu elección", no
-- "3 peticiones HTTP".

CREATE TABLE IF NOT EXISTS "prediction_quota_days" (
  "user_id"     bigint NOT NULL
                  REFERENCES "miniapp_users" ("telegram_user_id")
                  ON DELETE CASCADE,
  "usage_date"  date NOT NULL,
  "used"        integer NOT NULL DEFAULT 0,
  -- El tope se congela en la fila al crearla. Si mañana se sube de 3 a 5, el
  -- día en curso conserva el que se le prometió al usuario y ningún cambio de
  -- configuración puede encoger retroactivamente un día ya empezado.
  "daily_limit" integer NOT NULL,
  "updated_at"  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("user_id", "usage_date"),
  CONSTRAINT "prediction_quota_used_check" CHECK ("used" >= 0)
);

CREATE TABLE IF NOT EXISTS "prediction_grants" (
  "user_id"     bigint NOT NULL
                  REFERENCES "miniapp_users" ("telegram_user_id")
                  ON DELETE CASCADE,
  "usage_date"  date NOT NULL,
  -- `league_slug:match_id`, idéntico a lo que produce `shareFixtureKey`
  -- (`lib/share-card.ts`), para que la Mini App, el bot y la tarjeta
  -- compartida cuenten exactamente lo mismo. Un humano, un presupuesto.
  "fixture_key" text NOT NULL,
  "surface"     text NOT NULL,
  "granted_at"  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("user_id", "usage_date", "fixture_key"),
  CONSTRAINT "prediction_grants_surface_check"
    CHECK ("surface" IN ('miniapp', 'bot', 'share'))
);

CREATE INDEX IF NOT EXISTS "prediction_grants_day_idx"
  ON "prediction_grants" ("user_id", "usage_date");

-- ---------------------------------------------------------------------------
-- 6. Plan efectivo, en SQL
-- ---------------------------------------------------------------------------

-- Definición canónica para que el trigger y la aplicación no puedan discrepar.
--
-- Un premium caducado es free, y la caducidad se evalúa **al leer**, no por un
-- barrido: un barrido que no corra dejaría premium a quien ya no paga. Esto
-- además hace que la cancelación más común -el propio panel de Telegram, que no
-- nos notifica nada- no necesite código alguno: el cobro no vuelve,
-- `plan_expires_at` vence, y la siguiente lectura devuelve 'free'.
CREATE OR REPLACE FUNCTION effective_plan(p_user_id bigint)
RETURNS text LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN u.plan = 'premium'
     AND (u.plan_source = 'admin' OR u.plan_expires_at > now()) THEN 'premium'
    ELSE 'free'
  END
  FROM miniapp_users u
  WHERE u.telegram_user_id = p_user_id
$$;

-- ---------------------------------------------------------------------------
-- 7. Topes de favoritos y alertas, conscientes del plan
-- ---------------------------------------------------------------------------

-- Los topes viven duplicados desde `0000`: en el trigger y en la ruta. Relajar
-- sólo la ruta para premium dejaría al trigger rechazando igual, y el
-- suscriptor recibiría un 500 en lugar de un 409 -el peor de los dos mundos-.
-- Se reescriben ambas funciones; los triggers en sí no cambian.
--
-- `CREATE OR REPLACE FUNCTION` es idempotente por definición, y `effective_plan`
-- queda declarada más arriba en este mismo archivo: `sql.unsafe()` ejecuta el
-- fichero completo como un script, así que basta el orden textual.

CREATE OR REPLACE FUNCTION enforce_miniapp_favorite_limit()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF effective_plan(NEW.user_id) = 'premium' THEN RETURN NEW; END IF;
  PERFORM pg_advisory_xact_lock(NEW.user_id);
  IF NOT EXISTS (
    SELECT 1 FROM miniapp_favorites
     WHERE user_id = NEW.user_id
       AND entity_type = NEW.entity_type
       AND entity_id = NEW.entity_id
  ) AND (
    SELECT count(*) FROM miniapp_favorites WHERE user_id = NEW.user_id
  ) >= 10 THEN
    RAISE EXCEPTION 'favorite_limit_reached' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_alert_subscription_limit()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE active_count integer;
BEGIN
  IF NEW.enabled IS NOT TRUE THEN RETURN NEW; END IF;
  IF effective_plan(NEW.user_id) = 'premium' THEN RETURN NEW; END IF;
  PERFORM pg_advisory_xact_lock(NEW.user_id);
  SELECT count(*) INTO active_count
    FROM alert_subscriptions
   WHERE user_id = NEW.user_id
     AND enabled = true
     AND (TG_OP = 'INSERT' OR id <> NEW.id);
  IF active_count >= 20 THEN
    RAISE EXCEPTION 'subscription_limit_reached' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;
