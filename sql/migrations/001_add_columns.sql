BEGIN;

-- =========================================================
-- 001_add_columns.sql
-- Estructura incremental, sin borrar datos.
-- =========================================================

-- 1) teams: agregar id externo de ESPN si no existe.
ALTER TABLE teams
    ADD COLUMN IF NOT EXISTS espn_team_id BIGINT;

COMMENT ON COLUMN teams.espn_team_id IS
    'Identificador del equipo en ESPN; distinto del id interno local.';

-- 2) raw_api_responses: solo columnas nuevas.
ALTER TABLE raw_api_responses
    ADD COLUMN IF NOT EXISTS source VARCHAR(50),
    ADD COLUMN IF NOT EXISTS source_event_id VARCHAR(50),
    ADD COLUMN IF NOT EXISTS source_competition_id VARCHAR(50),
    ADD COLUMN IF NOT EXISTS page_number INTEGER,
    ADD COLUMN IF NOT EXISTS page_count INTEGER,
    ADD COLUMN IF NOT EXISTS total_count INTEGER,
    ADD COLUMN IF NOT EXISTS http_status INTEGER,
    ADD COLUMN IF NOT EXISTS response_hash CHAR(64);

COMMENT ON COLUMN raw_api_responses.source IS 'Source system, usually ESPN.';
COMMENT ON COLUMN raw_api_responses.source_event_id IS 'ESPN event id, if available.';
COMMENT ON COLUMN raw_api_responses.source_competition_id IS 'ESPN competition id, if available.';
COMMENT ON COLUMN raw_api_responses.page_number IS 'Downloaded page number.';
COMMENT ON COLUMN raw_api_responses.page_count IS 'Total pages reported by ESPN.';
COMMENT ON COLUMN raw_api_responses.total_count IS 'Total items reported by ESPN.';
COMMENT ON COLUMN raw_api_responses.http_status IS 'HTTP status code from the API call.';
COMMENT ON COLUMN raw_api_responses.response_hash IS 'SHA-256 hash of the canonical JSON payload.';

-- 3) events_ledger: create only if it does not exist.
CREATE TABLE IF NOT EXISTS events_ledger (
    id BIGSERIAL PRIMARY KEY,
    match_id BIGINT NOT NULL,
    raw_api_response_id BIGINT,
    espn_play_id VARCHAR(50),
    espn_event_uid VARCHAR(80),
    event_index INTEGER NOT NULL,
    minute INTEGER NOT NULL DEFAULT 0,
    second INTEGER NOT NULL DEFAULT 0,
    period_number INTEGER,
    clock_value NUMERIC(12,3),
    team_id BIGINT,
    athlete_ref TEXT,
    event_type_raw VARCHAR(80) NOT NULL,
    event_type VARCHAR(40) NOT NULL DEFAULT 'unclassified',
    description TEXT,
    player_name VARCHAR(200),
    assist_name VARCHAR(200),
    scoring_play BOOLEAN NOT NULL DEFAULT FALSE,
    penalty_kick BOOLEAN NOT NULL DEFAULT FALSE,
    valid BOOLEAN NOT NULL DEFAULT TRUE,
    raw_data JSONB NOT NULL,
    content_hash CHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_events_ledger_match
        FOREIGN KEY (match_id) REFERENCES matches (id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_events_ledger_raw_response
        FOREIGN KEY (raw_api_response_id) REFERENCES raw_api_responses (id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT fk_events_ledger_team
        FOREIGN KEY (team_id) REFERENCES teams (id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT chk_events_ledger_minute
        CHECK (minute >= 0),
    CONSTRAINT chk_events_ledger_second
        CHECK (second >= 0 AND second < 60),
    CONSTRAINT chk_events_ledger_event_type
        CHECK (event_type IS NOT NULL AND length(trim(event_type)) > 0)
);

COMMENT ON TABLE events_ledger IS
    'Full normalized ESPN event layer, including raw_data and raw event type.';
COMMENT ON COLUMN events_ledger.match_id IS 'Local match id.';
COMMENT ON COLUMN events_ledger.raw_api_response_id IS 'Source raw response row id.';
COMMENT ON COLUMN events_ledger.espn_play_id IS 'ESPN play id, if present.';
COMMENT ON COLUMN events_ledger.espn_event_uid IS 'ESPN play uid, if present.';
COMMENT ON COLUMN events_ledger.event_index IS 'Stable event order within the match.';
COMMENT ON COLUMN events_ledger.minute IS 'Minute derived from clock.value.';
COMMENT ON COLUMN events_ledger.second IS 'Second derived from clock.value.';
COMMENT ON COLUMN events_ledger.period_number IS 'Match period number.';
COMMENT ON COLUMN events_ledger.clock_value IS 'Original clock value in seconds.';
COMMENT ON COLUMN events_ledger.team_id IS 'Internal team id, never ESPN id.';
COMMENT ON COLUMN events_ledger.athlete_ref IS 'ESPN athlete reference URL.';
COMMENT ON COLUMN events_ledger.event_type_raw IS 'Original ESPN event type.';
COMMENT ON COLUMN events_ledger.event_type IS 'Normalized event type or unclassified.';
COMMENT ON COLUMN events_ledger.description IS 'Event description.';
COMMENT ON COLUMN events_ledger.player_name IS 'Structured player name if available.';
COMMENT ON COLUMN events_ledger.assist_name IS 'Structured assist name if available.';
COMMENT ON COLUMN events_ledger.scoring_play IS 'ESPN scoringPlay flag.';
COMMENT ON COLUMN events_ledger.penalty_kick IS 'ESPN penaltyKick flag.';
COMMENT ON COLUMN events_ledger.valid IS 'ESPN validity flag.';
COMMENT ON COLUMN events_ledger.raw_data IS 'Canonical original JSON for the play.';
COMMENT ON COLUMN events_ledger.content_hash IS 'Canonical SHA-256 hash for deduplication.';
COMMENT ON COLUMN events_ledger.created_at IS 'Insertion timestamp.';

-- 4) events_timeline: add only missing columns.
ALTER TABLE events_timeline
    ADD COLUMN IF NOT EXISTS event_ledger_id BIGINT,
    ADD COLUMN IF NOT EXISTS event_type_raw VARCHAR(80),
    ADD COLUMN IF NOT EXISTS athlete_ref TEXT,
    ADD COLUMN IF NOT EXISTS raw_data JSONB,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();

COMMENT ON COLUMN events_timeline.event_ledger_id IS 'FK to events_ledger.id when available.';
COMMENT ON COLUMN events_timeline.event_type_raw IS 'Original ESPN event type.';
COMMENT ON COLUMN events_timeline.athlete_ref IS 'ESPN athlete reference URL.';
COMMENT ON COLUMN events_timeline.raw_data IS 'Original JSON for the relevant event.';
COMMENT ON COLUMN events_timeline.created_at IS 'Insertion timestamp.';

COMMIT;
