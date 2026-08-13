SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = current_schema()
  AND tablename = 'high_probability_pick_freezes'
  AND indexname = 'idx_high_probability_pick_freezes_kickoff_ts';

-- Version: 1.0.0
-- Created: 2026-08-13
