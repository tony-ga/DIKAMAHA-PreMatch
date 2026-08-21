-- Fase 136 — store propio del Constructor de Parlays (DEC-222).
--
-- Cuatro tablas: dos para las piernas y dos para los parlays de referencia.
-- La separación no es organizativa: un parlay sólo se puede validar como
-- conjunto, porque al multiplicar probabilidades el error de calibración se
-- compone en vez de sumarse. Sin registrar qué piernas formaron cada parlay no
-- se puede medir el ratio de entrega que Fase 135 promete.
--
-- Aditiva y no destructiva: no toca ninguna tabla existente. Reaplicarla no
-- tiene efecto gracias a IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS parlay_leg_freezes (
    leg_key            VARCHAR(220) PRIMARY KEY,
    fixture_key        VARCHAR(160) NOT NULL,
    league_slug        VARCHAR(100) NOT NULL,
    match_id           INTEGER      NOT NULL,
    kickoff_ts         TIMESTAMPTZ  NOT NULL,
    market             VARCHAR(80)  NOT NULL,
    direction          VARCHAR(20)  NOT NULL,
    metric             VARCHAR(40)  NOT NULL,
    team_side          VARCHAR(20)  NOT NULL,
    period             VARCHAR(20)  NOT NULL,
    line               DOUBLE PRECISION NOT NULL,
    model_probability  DOUBLE PRECISION NOT NULL,
    threshold          DOUBLE PRECISION NOT NULL,
    criteria_sha256    VARCHAR(64)  NOT NULL,
    frozen_at          TIMESTAMPTZ  NOT NULL
);

CREATE TABLE IF NOT EXISTS parlay_leg_settlements (
    leg_key            VARCHAR(220) PRIMARY KEY,
    fixture_key        VARCHAR(160) NOT NULL,
    hit                BOOLEAN      NOT NULL,
    observed_value     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    settlement_source  VARCHAR(40)  NOT NULL,
    settled_at         TIMESTAMPTZ  NOT NULL
);

CREATE TABLE IF NOT EXISTS parlay_freezes (
    parlay_key               VARCHAR(220) PRIMARY KEY,
    leg_count                INTEGER      NOT NULL,
    leg_keys                 JSONB        NOT NULL DEFAULT '[]'::jsonb,
    declared_probability     DOUBLE PRECISION NOT NULL,
    declared_delivery_ratio  DOUBLE PRECISION,
    earliest_kickoff_ts      TIMESTAMPTZ  NOT NULL,
    criteria_sha256          VARCHAR(64)  NOT NULL,
    frozen_at                TIMESTAMPTZ  NOT NULL
);

CREATE TABLE IF NOT EXISTS parlay_settlements (
    parlay_key   VARCHAR(220) PRIMARY KEY,
    hit          BOOLEAN      NOT NULL,
    legs_hit     INTEGER      NOT NULL,
    leg_count    INTEGER      NOT NULL,
    settled_at   TIMESTAMPTZ  NOT NULL
);

-- `unsettled_legs` filtra por kickoff y anti-join contra el veredicto.
CREATE INDEX IF NOT EXISTS idx_parlay_leg_freezes_kickoff
    ON parlay_leg_freezes (kickoff_ts);

-- `legs_frozen_today` agrupa la cosecha de un día para formar combinaciones.
CREATE INDEX IF NOT EXISTS idx_parlay_leg_freezes_frozen_at
    ON parlay_leg_freezes (frozen_at);

CREATE INDEX IF NOT EXISTS idx_parlay_freezes_earliest_kickoff
    ON parlay_freezes (earliest_kickoff_ts);
