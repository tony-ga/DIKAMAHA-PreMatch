-- Optimiza auditorías SELECT-only que recorren el timeline en orden causal.
CREATE INDEX IF NOT EXISTS idx_events_timeline_causal_order
ON events_timeline (match_id, minute, second, id);

-- Version: 1.0.0
-- Created: 2026-07-28
