BEGIN;

-- Migración estructural 010.
-- Objetivo: añadir trazabilidad de calidad para reconciliación v2
-- sin sobrescribir `confidence` ni recalcular históricos.

ALTER TABLE match_statistics
    ADD COLUMN IF NOT EXISTS source_confidence NUMERIC(5,4),
    ADD COLUMN IF NOT EXISTS reconciliation_confidence NUMERIC(5,4),
    ADD COLUMN IF NOT EXISTS needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS conflict_details JSONB,
    ADD COLUMN IF NOT EXISTS reconciliation_status VARCHAR(16);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_match_statistics_source_confidence'
    ) THEN
        ALTER TABLE match_statistics
            ADD CONSTRAINT chk_match_statistics_source_confidence
            CHECK (source_confidence IS NULL OR (source_confidence >= 0 AND source_confidence <= 1));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_match_statistics_reconciliation_confidence'
    ) THEN
        ALTER TABLE match_statistics
            ADD CONSTRAINT chk_match_statistics_reconciliation_confidence
            CHECK (reconciliation_confidence IS NULL OR (reconciliation_confidence >= 0 AND reconciliation_confidence <= 1));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_match_statistics_reconciliation_status'
    ) THEN
        ALTER TABLE match_statistics
            ADD CONSTRAINT chk_match_statistics_reconciliation_status
            CHECK (reconciliation_status IS NULL OR reconciliation_status IN ('accepted', 'needs_review', 'rejected'));
    END IF;
END;
$$;

COMMENT ON COLUMN match_statistics.source_confidence IS
    'Confianza intrínseca del summary ESPN o fuente primaria utilizada.';
COMMENT ON COLUMN match_statistics.reconciliation_confidence IS
    'Confianza de la reconciliación final entre summary y play-by-play.';
COMMENT ON COLUMN match_statistics.needs_review IS
    'Marca filas que requieren revisión o bloqueo antes de persistencia.';
COMMENT ON COLUMN match_statistics.conflict_details IS
    'JSONB con detalles de conflicto, cobertura y decisión de reconciliación.';
COMMENT ON COLUMN match_statistics.reconciliation_status IS
    'Estado de la reconciliación: accepted, needs_review o rejected.';
COMMENT ON CONSTRAINT chk_match_statistics_source_confidence ON match_statistics IS
    '0..1. Para ampliar o cambiar la política, crear una migración explícita posterior.';
COMMENT ON CONSTRAINT chk_match_statistics_reconciliation_confidence ON match_statistics IS
    '0..1. Para ampliar o cambiar la política, crear una migración explícita posterior.';
COMMENT ON CONSTRAINT chk_match_statistics_reconciliation_status ON match_statistics IS
    'Estados válidos de reconciliación. La ampliación requiere migración explícita posterior.';

COMMIT;
