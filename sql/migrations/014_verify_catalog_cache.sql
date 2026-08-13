BEGIN;
SET TRANSACTION READ ONLY;

SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'catalog_cache'
ORDER BY ordinal_position;

SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'catalog_cache'
ORDER BY indexname;

SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'catalog_cache'::regclass
ORDER BY conname;

-- Ninguna entrada puede declarar una ventana de validez vacía o invertida.
SELECT COUNT(*) AS invalid_window_rows
FROM catalog_cache
WHERE expires_at <= computed_at;

-- Fotografía operativa: cuántas entradas hay y cuántas siguen vigentes.
SELECT
    COUNT(*) AS total_rows,
    COUNT(*) FILTER (WHERE expires_at > now()) AS live_rows,
    MAX(computed_at) AS newest_computed_at
FROM catalog_cache;

ROLLBACK;
