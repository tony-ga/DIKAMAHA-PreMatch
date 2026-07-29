-- Verificación de la migración 006_match_statistics.
-- No modifica datos.

SELECT
    table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'match_statistics'
ORDER BY ordinal_position;

SELECT
    conname,
    contype
FROM pg_constraint
WHERE conrelid = 'public.match_statistics'::regclass
ORDER BY conname;

SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'match_statistics'
ORDER BY indexname;

