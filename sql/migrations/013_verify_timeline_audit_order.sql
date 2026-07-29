SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = current_schema()
  AND tablename = 'events_timeline'
  AND indexname = 'idx_events_timeline_causal_order';

-- Version: 1.0.0
-- Created: 2026-07-28
