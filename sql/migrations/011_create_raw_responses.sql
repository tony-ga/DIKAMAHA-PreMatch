BEGIN;

-- Contrato raw-first genérico para contexto pre-match v2.
CREATE TABLE IF NOT EXISTS raw_responses (
    id BIGSERIAL PRIMARY KEY,
    provider VARCHAR(50) NOT NULL,
    entity_type VARCHAR(30) NOT NULL,
    entity_id VARCHAR(100),
    league_slug VARCHAR(100),
    season VARCHAR(30),
    endpoint VARCHAR(500) NOT NULL,
    request_params JSONB NOT NULL,
    request_hash CHAR(64) NOT NULL,
    response_json JSONB NOT NULL,
    response_hash CHAR(64) NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ,
    cutoff_ts TIMESTAMPTZ,
    kickoff_ts TIMESTAMPTZ,
    http_status INTEGER NOT NULL,
    parser_version VARCHAR(50) NOT NULL,
    capture_kind VARCHAR(40) NOT NULL,
    CONSTRAINT chk_raw_responses_http_status
        CHECK (http_status BETWEEN 100 AND 599),
    CONSTRAINT chk_raw_responses_capture_kind
        CHECK (capture_kind IN (
            'historical_reconstruction',
            'prospective_snapshot'
        )),
    CONSTRAINT chk_raw_responses_prospective_time
        CHECK (
            capture_kind <> 'prospective_snapshot'
            OR kickoff_ts IS NULL
            OR fetched_at < kickoff_ts
        )
);

CREATE INDEX IF NOT EXISTS ix_raw_responses_request_hash
    ON raw_responses (request_hash);

CREATE INDEX IF NOT EXISTS ix_raw_responses_entity_cutoff
    ON raw_responses (entity_type, entity_id, cutoff_ts);

COMMENT ON TABLE raw_responses IS
    'Payloads externos persistidos antes del parseo de dominio pre-match.';
COMMENT ON COLUMN raw_responses.available_at IS
    'Timestamp publicado por la fuente; NULL cuando ESPN no lo proporciona.';
COMMENT ON COLUMN raw_responses.capture_kind IS
    'Distingue reconstrucción histórica de snapshot prospectivo real.';

COMMIT;
