BEGIN;

-- Caché compartida de catálogos ESPN (`/v1/upcoming`, `/v1/live`).
--
-- No guarda evidencia ni entra en ningún contrato causal: es exclusivamente
-- una copia derivada, reconstruible en cualquier momento repitiendo el
-- barrido. Existe porque ese barrido cuesta ~30 s contra 63 ligas x 3 días y
-- la caché en memoria del proceso muere en cada despliegue o reinicio, de modo
-- que el primer usuario posterior volvía a pagarlo entero. Persistirla deja
-- que el servicio arranque con el catálogo ya calculado.
--
-- Puede truncarse sin consecuencias: el peor efecto es un barrido de más.
CREATE TABLE IF NOT EXISTS catalog_cache (
    cache_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT chk_catalog_cache_window
        CHECK (expires_at > computed_at)
);

-- Sirve tanto la limpieza periódica de entradas agotadas como el descarte de
-- las vencidas en la lectura.
CREATE INDEX IF NOT EXISTS ix_catalog_cache_expires_at
    ON catalog_cache (expires_at);

COMMENT ON TABLE catalog_cache IS
    'Copia derivada y reconstruible de catálogos ESPN; truncable sin pérdida.';
COMMENT ON COLUMN catalog_cache.cache_key IS
    'JSON canónico de los filtros del barrido (ligas y fecha), sin limit.';
COMMENT ON COLUMN catalog_cache.computed_at IS
    'Momento del barrido real; de aquí sale la edad publicada al cliente.';
COMMENT ON COLUMN catalog_cache.expires_at IS
    'Fin de la ventana en que la entrada puede seguir sirviéndose.';

COMMIT;
