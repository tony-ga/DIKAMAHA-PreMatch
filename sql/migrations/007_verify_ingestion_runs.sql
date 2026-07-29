SELECT
    table_schema,
    table_name,
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'ingestion_runs'
ORDER BY ordinal_position;

SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.ingestion_runs'::regclass
ORDER BY conname;

SELECT
    status,
    COUNT(*) AS total_rows,
    COUNT(*) FILTER (WHERE heartbeat_at IS NULL) AS heartbeat_nulls
FROM ingestion_runs
GROUP BY status
ORDER BY status;

SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'ingestion_runs'
ORDER BY indexname;

SELECT COUNT(*) AS total_rows FROM ingestion_runs;
