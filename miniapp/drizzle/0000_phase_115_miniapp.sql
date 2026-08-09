CREATE TABLE IF NOT EXISTS "miniapp_users" (
  "telegram_user_id" bigint PRIMARY KEY,
  "username" text,
  "first_name" text NOT NULL,
  "last_name" text,
  "language_code" text,
  "first_seen_at" timestamptz NOT NULL DEFAULT now(),
  "last_seen_at" timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "miniapp_favorites" (
  "user_id" bigint NOT NULL REFERENCES "miniapp_users"("telegram_user_id") ON DELETE CASCADE,
  "entity_type" text NOT NULL CHECK ("entity_type" IN ('fixture', 'team', 'league')),
  "entity_id" text NOT NULL,
  "label" text NOT NULL,
  "metadata" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "created_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("user_id", "entity_type", "entity_id")
);

CREATE TABLE IF NOT EXISTS "alert_subscriptions" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "user_id" bigint NOT NULL REFERENCES "miniapp_users"("telegram_user_id") ON DELETE CASCADE,
  "rule_type" text NOT NULL CHECK ("rule_type" IN (
    'kickoff', 'score_change', 'status_change', 'probability_delta',
    'model_status', 'market_threshold', 'fixture_presence'
  )),
  "fixture_id" text,
  "league_slug" text NOT NULL,
  "market_key" text,
  "period" text,
  "selection" text,
  "comparator" text CHECK ("comparator" IS NULL OR "comparator" IN ('gte', 'lte', 'delta')),
  "threshold" numeric(8, 6),
  "cooldown_seconds" integer NOT NULL DEFAULT 300 CHECK ("cooldown_seconds" >= 300),
  "enabled" boolean NOT NULL DEFAULT true,
  "last_observation" jsonb,
  "last_evaluated_at" timestamptz,
  "last_triggered_at" timestamptz,
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  CHECK (
    "rule_type" NOT IN ('market_threshold', 'probability_delta') OR
    ("market_key" IS NOT NULL AND "comparator" IS NOT NULL AND "threshold" IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS "alert_subscriptions_enabled_idx"
  ON "alert_subscriptions" ("enabled", "league_slug", "fixture_id");

CREATE TABLE IF NOT EXISTS "alert_deliveries" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "subscription_id" uuid NOT NULL REFERENCES "alert_subscriptions"("id") ON DELETE CASCADE,
  "event_key" text NOT NULL,
  "status" text NOT NULL CHECK ("status" IN ('pending', 'sent', 'failed')),
  "error_code" text,
  "delivered_at" timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS "alert_delivery_subscription_event_uidx"
  ON "alert_deliveries" ("subscription_id", "event_key");

CREATE OR REPLACE FUNCTION enforce_miniapp_favorite_limit()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
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

DROP TRIGGER IF EXISTS miniapp_favorite_limit_trigger ON miniapp_favorites;
CREATE TRIGGER miniapp_favorite_limit_trigger
BEFORE INSERT ON miniapp_favorites
FOR EACH ROW EXECUTE FUNCTION enforce_miniapp_favorite_limit();

CREATE OR REPLACE FUNCTION enforce_alert_subscription_limit()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE active_count integer;
BEGIN
  IF NEW.enabled IS NOT TRUE THEN RETURN NEW; END IF;
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

DROP TRIGGER IF EXISTS alert_subscription_limit_trigger ON alert_subscriptions;
CREATE TRIGGER alert_subscription_limit_trigger
BEFORE INSERT OR UPDATE OF enabled ON alert_subscriptions
FOR EACH ROW EXECUTE FUNCTION enforce_alert_subscription_limit();
