BEGIN;
SET TRANSACTION READ ONLY;

SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'raw_responses'
  AND column_name IN ('scope_event_id', 'snapshot_bucket')
ORDER BY column_name;

SELECT COUNT(*) AS invalid_bucket_rows
FROM raw_responses
WHERE snapshot_bucket IS NOT NULL
  AND snapshot_bucket NOT IN ('T-168h', 'T-72h', 'T-24h', 'T-6h', 'T-90m');

SELECT scope_event_id, snapshot_bucket, request_hash, COUNT(*) AS duplicates
FROM raw_responses
WHERE snapshot_bucket IS NOT NULL
GROUP BY scope_event_id, snapshot_bucket, request_hash
HAVING COUNT(*) > 1;

ROLLBACK;
