BEGIN;
SET TRANSACTION READ ONLY;

SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'raw_responses'
ORDER BY ordinal_position;

SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'raw_responses'
ORDER BY indexname;

SELECT COUNT(*) AS invalid_prospective_rows
FROM raw_responses
WHERE capture_kind = 'prospective_snapshot'
  AND kickoff_ts IS NOT NULL
  AND fetched_at >= kickoff_ts;

ROLLBACK;
