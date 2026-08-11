CREATE TABLE IF NOT EXISTS "prediction_settlements" (
  "fixture_key" text PRIMARY KEY,
  "league_slug" text NOT NULL,
  "match_id" bigint NOT NULL,
  "competition_id" text NOT NULL,
  "kickoff_ts" timestamptz NOT NULL,
  "settled_at" timestamptz NOT NULL DEFAULT now(),
  "home_team_name" text NOT NULL,
  "away_team_name" text NOT NULL,
  "score_home" integer NOT NULL CHECK ("score_home" >= 0),
  "score_away" integer NOT NULL CHECK ("score_away" >= 0),
  "prediction_hash" text NOT NULL,
  "official_verdicts" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "shadow_verdicts" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "contract_version" text
);

CREATE INDEX IF NOT EXISTS "prediction_settlements_kickoff_idx"
  ON "prediction_settlements" ("kickoff_ts" DESC);
