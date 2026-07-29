-- =========================================================
-- 004_verify.sql
-- Read-only verification queries.
-- =========================================================

-- teams
SELECT
    table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'teams'
ORDER BY ordinal_position;

SELECT COUNT(*) AS teams_with_espn_team_id
FROM teams
WHERE espn_team_id IS NOT NULL;

SELECT espn_team_id, COUNT(*) AS n
FROM teams
WHERE espn_team_id IS NOT NULL
GROUP BY espn_team_id
HAVING COUNT(*) > 1;

-- Mapping query: ESPN team ID -> teams.espn_team_id -> teams.id
-- Replace :espn_team_id with a real value.
SELECT
    id AS internal_team_id,
    espn_team_id,
    name
FROM teams
WHERE espn_team_id = :espn_team_id;

-- raw_api_responses
SELECT COUNT(*) AS null_response_hashes
FROM raw_api_responses
WHERE response_hash IS NULL;

SELECT
    match_id,
    endpoint,
    COALESCE(source_event_id, '') AS source_event_id,
    COALESCE(source_competition_id, '') AS source_competition_id,
    page_number,
    response_hash,
    COUNT(*) AS n
FROM raw_api_responses
GROUP BY 1,2,3,4,5,6
HAVING COUNT(*) > 1;

-- events_ledger
SELECT COUNT(*) AS ledger_rows
FROM events_ledger;

SELECT COUNT(*) AS null_content_hashes
FROM events_ledger
WHERE content_hash IS NULL;

SELECT
    match_id,
    content_hash,
    COUNT(*) AS n
FROM events_ledger
GROUP BY 1,2
HAVING COUNT(*) > 1;

-- events_timeline
SELECT COUNT(*) AS null_event_ledger_id
FROM events_timeline
WHERE event_ledger_id IS NULL;

SELECT
    COUNT(*) AS invalid_event_ledger_refs
FROM events_timeline et
LEFT JOIN events_ledger el ON el.id = et.event_ledger_id
WHERE et.event_ledger_id IS NOT NULL
  AND el.id IS NULL;

-- Distribution checks
SELECT event_type, COUNT(*) AS n
FROM events_ledger
GROUP BY event_type
ORDER BY n DESC, event_type;

SELECT event_type_raw, COUNT(*) AS n
FROM events_ledger
GROUP BY event_type_raw
ORDER BY n DESC, event_type_raw;
