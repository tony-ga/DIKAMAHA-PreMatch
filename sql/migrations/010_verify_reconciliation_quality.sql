-- Verificación de la migración 010.
-- Solo lectura.

SELECT
  column_name,
  is_nullable,
  data_type,
  column_default
FROM information_schema.columns
WHERE table_name = 'match_statistics'
  AND column_name IN (
    'source_confidence',
    'reconciliation_confidence',
    'needs_review',
    'conflict_details',
    'reconciliation_status'
  )
ORDER BY column_name;

SELECT
  conname,
  pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conname IN (
  'chk_match_statistics_source_confidence',
  'chk_match_statistics_reconciliation_confidence',
  'chk_match_statistics_reconciliation_status'
)
ORDER BY conname;

SELECT
  count(*) AS total_rows,
  count(*) FILTER (WHERE reconciliation_version = 'v1') AS v1_rows,
  count(*) FILTER (WHERE reconciliation_version = 'v2') AS v2_rows,
  count(*) FILTER (WHERE source_confidence IS NOT NULL) AS rows_with_source_confidence,
  count(*) FILTER (WHERE reconciliation_confidence IS NOT NULL) AS rows_with_reconciliation_confidence,
  count(*) FILTER (WHERE needs_review IS TRUE) AS rows_needing_review
FROM match_statistics;

SELECT
  match_id,
  team_id,
  reconciliation_version,
  confidence,
  source_confidence,
  reconciliation_confidence,
  needs_review,
  reconciliation_status
FROM match_statistics
ORDER BY match_id, team_id, reconciliation_version;
