-- Verificación de la migración 016 (Fase 136).
-- Debe devolver 4 tablas y 3 índices.

SELECT tablename
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
      'parlay_leg_freezes', 'parlay_leg_settlements',
      'parlay_freezes', 'parlay_settlements')
ORDER BY tablename;

SELECT indexname
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'idx_parlay_leg_freezes_kickoff',
      'idx_parlay_leg_freezes_frozen_at',
      'idx_parlay_freezes_earliest_kickoff')
ORDER BY indexname;

-- Un parlay nunca debe referenciar menos piernas que su `leg_count`.
SELECT COUNT(*) AS inconsistent_parlays
FROM parlay_freezes
WHERE jsonb_array_length(leg_keys) <> leg_count;
