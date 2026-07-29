-- Verificación de catálogo y FKs asociadas a `teams`.
-- Solo lectura.

SELECT
  'teams_total' AS check_name,
  count(*)::bigint AS value
FROM teams
UNION ALL
SELECT
  'canonical_team_ids' AS check_name,
  count(*)::bigint AS value
FROM teams
WHERE id IN (1, 2)
UNION ALL
SELECT
  'duplicate_team_ids' AS check_name,
  count(*)::bigint AS value
FROM teams
WHERE id IN (3, 4, 5, 6)
UNION ALL
SELECT
  'duplicate_fk_usage' AS check_name,
  sum(cnt)::bigint AS value
FROM (
  SELECT count(*) AS cnt FROM matches WHERE home_team_id IN (3, 4, 5, 6)
  UNION ALL SELECT count(*) FROM matches WHERE away_team_id IN (3, 4, 5, 6)
  UNION ALL SELECT count(*) FROM players WHERE team_id IN (3, 4, 5, 6)
  UNION ALL SELECT count(*) FROM events_ledger WHERE team_id IN (3, 4, 5, 6)
  UNION ALL SELECT count(*) FROM events_timeline WHERE team_id IN (3, 4, 5, 6)
  UNION ALL SELECT count(*) FROM match_statistics WHERE team_id IN (3, 4, 5, 6)
) s;

SELECT
  id,
  name,
  city,
  stadium,
  altitude,
  foundation_year,
  espn_team_id
FROM teams
ORDER BY id;

