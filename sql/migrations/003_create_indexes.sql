BEGIN;

-- =========================================================
-- 003_create_indexes.sql
-- Create indexes and constraints only after backfill validation.
-- =========================================================

-- teams.espn_team_id
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'teams'
          AND column_name = 'espn_team_id'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'teams'
              AND indexname = 'uq_teams_espn_team_id'
        ) THEN
            -- Create only if no duplicates are present.
            IF EXISTS (
                SELECT 1
                FROM teams
                WHERE espn_team_id IS NOT NULL
                GROUP BY espn_team_id
                HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION 'Cannot create unique index uq_teams_espn_team_id: duplicate espn_team_id values exist';
            END IF;
            CREATE UNIQUE INDEX uq_teams_espn_team_id
                ON teams (espn_team_id)
                WHERE espn_team_id IS NOT NULL;
        END IF;
    END IF;
END $$;

-- raw_api_responses response_hash unique index
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'raw_api_responses'
          AND column_name = 'response_hash'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM raw_api_responses
            WHERE response_hash IS NULL
        ) THEN
            RAISE EXCEPTION 'Cannot create unique index on raw_api_responses: response_hash still has NULL values';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM raw_api_responses
            GROUP BY match_id, endpoint, COALESCE(source_event_id, ''), COALESCE(source_competition_id, ''), page_number, response_hash
            HAVING COUNT(*) > 1
        ) THEN
            RAISE EXCEPTION 'Cannot create unique index on raw_api_responses: duplicate payloads detected';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'raw_api_responses'
              AND indexname = 'uq_raw_api_responses_dedup'
        ) THEN
            CREATE UNIQUE INDEX uq_raw_api_responses_dedup
                ON raw_api_responses (match_id, endpoint, COALESCE(source_event_id, ''), COALESCE(source_competition_id, ''), page_number, response_hash);
        END IF;
    END IF;
END $$;

-- events_ledger content_hash unique index
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'events_ledger'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM events_ledger
            WHERE content_hash IS NULL
        ) THEN
            RAISE EXCEPTION 'Cannot create unique index on events_ledger: content_hash still has NULL values';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM events_ledger
            GROUP BY match_id, content_hash
            HAVING COUNT(*) > 1
        ) THEN
            RAISE EXCEPTION 'Cannot create unique index on events_ledger: duplicate content_hash values detected';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'events_ledger'
              AND indexname = 'uq_events_ledger_dedup_content'
        ) THEN
            CREATE UNIQUE INDEX uq_events_ledger_dedup_content
                ON events_ledger (match_id, content_hash);
        END IF;
    END IF;
END $$;

-- events_timeline -> events_ledger FK only after validation.
DO $$
DECLARE
    missing_refs BIGINT;
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'events_timeline'
          AND column_name = 'event_ledger_id'
    ) THEN
        SELECT COUNT(*)
        INTO missing_refs
        FROM events_timeline
        WHERE event_ledger_id IS NULL;

        -- Keep NULLs for old records, but do not enforce NOT NULL yet.
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.table_constraints
            WHERE table_schema = 'public'
              AND table_name = 'events_timeline'
              AND constraint_name = 'fk_events_timeline_ledger'
        ) THEN
            IF EXISTS (
                SELECT 1
                FROM events_timeline et
                LEFT JOIN events_ledger el ON el.id = et.event_ledger_id
                WHERE et.event_ledger_id IS NOT NULL
                  AND el.id IS NULL
            ) THEN
                RAISE EXCEPTION 'Cannot add fk_events_timeline_ledger: invalid references exist';
            END IF;
            ALTER TABLE events_timeline
                ADD CONSTRAINT fk_events_timeline_ledger
                FOREIGN KEY (event_ledger_id) REFERENCES events_ledger (id)
                ON UPDATE CASCADE ON DELETE CASCADE;
        END IF;
    END IF;
END $$;

-- Basic indexes for new columns.
CREATE INDEX IF NOT EXISTS idx_raw_api_responses_source_event_id
    ON raw_api_responses (source_event_id);

CREATE INDEX IF NOT EXISTS idx_raw_api_responses_source_competition_id
    ON raw_api_responses (source_competition_id);

CREATE INDEX IF NOT EXISTS idx_raw_api_responses_page_number
    ON raw_api_responses (page_number);

CREATE INDEX IF NOT EXISTS idx_raw_api_responses_fetched_at
    ON raw_api_responses (fetched_at);

CREATE INDEX IF NOT EXISTS idx_events_ledger_match_id
    ON events_ledger (match_id);

CREATE INDEX IF NOT EXISTS idx_events_ledger_raw_api_response_id
    ON events_ledger (raw_api_response_id);

CREATE INDEX IF NOT EXISTS idx_events_ledger_team_id
    ON events_ledger (team_id);

CREATE INDEX IF NOT EXISTS idx_events_ledger_espn_play_id
    ON events_ledger (espn_play_id);

CREATE INDEX IF NOT EXISTS idx_events_ledger_event_type
    ON events_ledger (event_type);

CREATE INDEX IF NOT EXISTS idx_events_ledger_event_type_raw
    ON events_ledger (event_type_raw);

CREATE INDEX IF NOT EXISTS idx_events_ledger_minute
    ON events_ledger (minute);

CREATE INDEX IF NOT EXISTS idx_events_ledger_content_hash
    ON events_ledger (content_hash);

CREATE INDEX IF NOT EXISTS idx_events_timeline_event_ledger_id
    ON events_timeline (event_ledger_id);

COMMIT;
