BEGIN;

ALTER TABLE raw_responses
    ADD COLUMN IF NOT EXISTS scope_event_id VARCHAR(100),
    ADD COLUMN IF NOT EXISTS snapshot_bucket VARCHAR(20);

CREATE UNIQUE INDEX IF NOT EXISTS ix_raw_responses_snapshot_scope
    ON raw_responses (scope_event_id, snapshot_bucket, request_hash);

COMMENT ON COLUMN raw_responses.scope_event_id IS
    'Fixture cuyo contexto pre-match motivó la captura.';
COMMENT ON COLUMN raw_responses.snapshot_bucket IS
    'Bucket objetivo T-168h, T-72h, T-24h, T-6h o T-90m.';

COMMIT;
