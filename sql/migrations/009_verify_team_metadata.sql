-- Verificación de la migración 009.
-- Solo lectura.

SELECT
  column_name,
  is_nullable,
  column_default,
  data_type
FROM information_schema.columns
WHERE table_name = 'teams'
  AND column_name IN ('altitude', 'foundation_year')
ORDER BY column_name;

SELECT
  count(*) AS teams_total,
  count(*) FILTER (WHERE id IN (1, 2)) AS canonical_teams,
  count(*) FILTER (WHERE id IN (3, 4, 5, 6)) AS duplicate_teams,
  count(*) FILTER (WHERE altitude IS NULL OR foundation_year IS NULL) AS teams_with_null_metadata
FROM teams;

SELECT
  id,
  name,
  city,
  stadium,
  altitude,
  foundation_year,
  espn_team_id
FROM teams
WHERE id IN (1, 2, 3, 4, 5, 6)
ORDER BY id;
