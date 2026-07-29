BEGIN;

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(32) NOT NULL DEFAULT 'espn',
    espn_event_id BIGINT NOT NULL,
    match_id BIGINT NOT NULL,
    league VARCHAR(32) NOT NULL,
    competition_id BIGINT NOT NULL,
    season VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_code VARCHAR(64),
    error_message TEXT,
    reconciliation_version VARCHAR(32) NOT NULL,
    raw_items INTEGER NOT NULL DEFAULT 0,
    ledger_events INTEGER NOT NULL DEFAULT 0,
    timeline_events INTEGER NOT NULL DEFAULT 0,
    statistics_rows INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_ingestion_runs_source
        CHECK (source = 'espn'),
    CONSTRAINT chk_ingestion_runs_status
        CHECK (status IN ('pending', 'running', 'success', 'failed', 'skipped')),
    CONSTRAINT chk_ingestion_runs_heartbeat
        CHECK (
            (status = 'running' AND heartbeat_at IS NOT NULL)
            OR (status <> 'running')
        ),
    CONSTRAINT chk_ingestion_runs_non_negative
        CHECK (
            raw_items >= 0
            AND ledger_events >= 0
            AND timeline_events >= 0
            AND statistics_rows >= 0
        ),
    CONSTRAINT uq_ingestion_runs_source_event_version
        UNIQUE (source, espn_event_id, reconciliation_version),
    CONSTRAINT fk_ingestion_runs_match
        FOREIGN KEY (match_id) REFERENCES matches(id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_match_id ON ingestion_runs (match_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_espn_event_id ON ingestion_runs (espn_event_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_status ON ingestion_runs (status);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_reconciliation_version ON ingestion_runs (reconciliation_version);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_source_created_at ON ingestion_runs (source, created_at DESC);

COMMENT ON TABLE ingestion_runs IS 'Tabla de control para corridas de ingesta históricas y controladas.';
COMMENT ON COLUMN ingestion_runs.source IS 'Fuente de la corrida. Actualmente solo espn.';
COMMENT ON COLUMN ingestion_runs.espn_event_id IS 'Identificador ESPN del evento procesado.';
COMMENT ON COLUMN ingestion_runs.match_id IS 'Partido interno asociado a la ingesta.';
COMMENT ON COLUMN ingestion_runs.league IS 'Liga ESPN usada para obtener la fuente.';
COMMENT ON COLUMN ingestion_runs.competition_id IS 'Competencia ESPN usada para obtener la fuente.';
COMMENT ON COLUMN ingestion_runs.season IS 'Temporada interna o de negocio asociada a la corrida.';
COMMENT ON COLUMN ingestion_runs.status IS 'Estado de la corrida: pending, running, success, failed o skipped.';
COMMENT ON COLUMN ingestion_runs.started_at IS 'Momento UTC en que inició la corrida.';
COMMENT ON COLUMN ingestion_runs.heartbeat_at IS 'Momento UTC del último heartbeat; se usa para detectar corridas running expiradas.';
COMMENT ON COLUMN ingestion_runs.finished_at IS 'Momento UTC en que finalizó la corrida.';
COMMENT ON COLUMN ingestion_runs.error_code IS 'Código breve de error cuando la corrida falla o se omite.';
COMMENT ON COLUMN ingestion_runs.error_message IS 'Mensaje descriptivo del error o motivo de omisión.';
COMMENT ON COLUMN ingestion_runs.reconciliation_version IS 'Versión de reglas de reconciliación usada por la corrida.';
COMMENT ON COLUMN ingestion_runs.raw_items IS 'Cantidad de eventos crudos descargados.';
COMMENT ON COLUMN ingestion_runs.ledger_events IS 'Cantidad de eventos persistidos en events_ledger.';
COMMENT ON COLUMN ingestion_runs.timeline_events IS 'Cantidad de eventos relevantes persistidos en events_timeline.';
COMMENT ON COLUMN ingestion_runs.statistics_rows IS 'Cantidad de filas generadas en match_statistics.';
COMMENT ON COLUMN ingestion_runs.created_at IS 'Marca temporal UTC de creación del registro de control.';
COMMENT ON CONSTRAINT chk_ingestion_runs_source ON ingestion_runs IS 'La tabla de control solo registra corridas de ESPN. Para ampliar fuentes, crear una migración explícita que amplíe este CHECK.';
COMMENT ON CONSTRAINT chk_ingestion_runs_status ON ingestion_runs IS 'Estados permitidos para controlar reanudación, éxito, fallo y omisión.';
COMMENT ON CONSTRAINT chk_ingestion_runs_heartbeat ON ingestion_runs IS 'Una corrida running debe mantener heartbeat_at no nulo; la reclamación solo procede si el heartbeat expiró según la política del runner.';

COMMIT;
