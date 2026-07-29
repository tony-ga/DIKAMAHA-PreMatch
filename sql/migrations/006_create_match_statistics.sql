BEGIN;

CREATE TABLE IF NOT EXISTS match_statistics (
    id BIGSERIAL PRIMARY KEY,
    match_id BIGINT NOT NULL,
    team_id BIGINT NOT NULL,
    source VARCHAR(32) NOT NULL,
    reconciliation_version VARCHAR(32) NOT NULL,
    shots_total INTEGER NOT NULL DEFAULT 0,
    shots_on_target INTEGER NOT NULL DEFAULT 0,
    fouls INTEGER NOT NULL DEFAULT 0,
    yellow_cards INTEGER NOT NULL DEFAULT 0,
    red_cards INTEGER NOT NULL DEFAULT 0,
    corners INTEGER NOT NULL DEFAULT 0,
    saves INTEGER NOT NULL DEFAULT 0,
    possession_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
    goals INTEGER NOT NULL DEFAULT 0,
    var_annulled_events INTEGER NOT NULL DEFAULT 0,
    source_event_id BIGINT,
    source_fetched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reconciled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    has_conflict BOOLEAN NOT NULL DEFAULT FALSE,
    primary_source VARCHAR(64) NOT NULL,
    fallback_source VARCHAR(64),
    confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
    derived_play_by_play JSONB NOT NULL DEFAULT '{}'::jsonb,
    espn_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_match_statistics_source
        CHECK (source IN ('espn_summary', 'derived_play_by_play')),
    CONSTRAINT fk_match_statistics_match
        FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
    CONSTRAINT fk_match_statistics_team
        FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE RESTRICT,
    CONSTRAINT chk_match_statistics_possession
        CHECK (possession_pct >= 0 AND possession_pct <= 100),
    CONSTRAINT chk_match_statistics_non_negative
        CHECK (
            shots_total >= 0 AND
            shots_on_target >= 0 AND
            fouls >= 0 AND
            yellow_cards >= 0 AND
            red_cards >= 0 AND
            corners >= 0 AND
            saves >= 0 AND
            goals >= 0 AND
            var_annulled_events >= 0
        ),
    CONSTRAINT uq_match_statistics_scope
        UNIQUE (match_id, team_id, source, reconciliation_version)
);

CREATE INDEX IF NOT EXISTS idx_match_statistics_match_id ON match_statistics (match_id);
CREATE INDEX IF NOT EXISTS idx_match_statistics_team_id ON match_statistics (team_id);
CREATE INDEX IF NOT EXISTS idx_match_statistics_source ON match_statistics (source);
CREATE INDEX IF NOT EXISTS idx_match_statistics_reconciliation_version ON match_statistics (reconciliation_version);

COMMENT ON TABLE match_statistics IS 'Estadísticas reconciliadas por partido, equipo, fuente y versión de reglas.';
COMMENT ON COLUMN match_statistics.match_id IS 'Partido interno de la base de datos.';
COMMENT ON COLUMN match_statistics.team_id IS 'Equipo interno de la base de datos.';
COMMENT ON COLUMN match_statistics.source IS 'Fuente principal del registro: espn_summary o derived_play_by_play.';
COMMENT ON CONSTRAINT chk_match_statistics_source ON match_statistics IS 'Fuente controlada. Para ampliar, crear una migración explícita que actualice este CHECK tras validar el nuevo origen.';
COMMENT ON COLUMN match_statistics.reconciliation_version IS 'Versión de la política de reconciliación aplicada.';
COMMENT ON COLUMN match_statistics.shots_total IS 'Tiros totales reconciliados.';
COMMENT ON COLUMN match_statistics.shots_on_target IS 'Tiros a puerta reconciliados.';
COMMENT ON COLUMN match_statistics.fouls IS 'Faltas reconciliadas.';
COMMENT ON COLUMN match_statistics.yellow_cards IS 'Tarjetas amarillas reconciliadas.';
COMMENT ON COLUMN match_statistics.red_cards IS 'Tarjetas rojas reconciliadas.';
COMMENT ON COLUMN match_statistics.corners IS 'Tiros de esquina reconciliados.';
COMMENT ON COLUMN match_statistics.saves IS 'Atajadas reconciliadas.';
COMMENT ON COLUMN match_statistics.possession_pct IS 'Posesión porcentual entre 0 y 100.';
COMMENT ON COLUMN match_statistics.goals IS 'Goles reconciliados.';
COMMENT ON COLUMN match_statistics.var_annulled_events IS 'Eventos anulados por VAR.';
COMMENT ON COLUMN match_statistics.source_event_id IS 'Identificador del evento ESPN fuente, cuando exista.';
COMMENT ON COLUMN match_statistics.source_fetched_at IS 'Marca temporal de obtención de la fuente.';
COMMENT ON COLUMN match_statistics.created_at IS 'Marca temporal de creación del registro.';
COMMENT ON COLUMN match_statistics.reconciled_at IS 'Marca temporal de reconciliación.';
COMMENT ON COLUMN match_statistics.has_conflict IS 'Indica si existió conflicto entre summary y play-by-play.';
COMMENT ON COLUMN match_statistics.primary_source IS 'Fuente prioritaria usada como valor final.';
COMMENT ON COLUMN match_statistics.fallback_source IS 'Fuente secundaria usada como respaldo o auditoría.';
COMMENT ON COLUMN match_statistics.confidence IS 'Confianza de la reconciliación para este registro.';
COMMENT ON COLUMN match_statistics.derived_play_by_play IS 'Detalle JSONB de métricas derivadas desde play-by-play.';
COMMENT ON COLUMN match_statistics.espn_summary IS 'Detalle JSONB del summary ESPN usado como referencia.';

COMMIT;
