-- Requirements:
-- PostgreSQL 13+ (recomendado)

BEGIN;

CREATE TABLE IF NOT EXISTS teams (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    city VARCHAR(150) NOT NULL,
    stadium VARCHAR(150) NOT NULL,
    altitude INTEGER NOT NULL DEFAULT 0,
    foundation_year INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    id BIGSERIAL PRIMARY KEY,
    home_team_id BIGINT NOT NULL,
    away_team_id BIGINT NOT NULL,
    match_date TIMESTAMP NOT NULL,
    season VARCHAR(20) NOT NULL,
    home_score INTEGER,
    away_score INTEGER,
    status VARCHAR(30) NOT NULL,
    CONSTRAINT fk_matches_home_team
        FOREIGN KEY (home_team_id) REFERENCES teams (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_matches_away_team
        FOREIGN KEY (away_team_id) REFERENCES teams (id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS players (
    id BIGSERIAL PRIMARY KEY,
    team_id BIGINT NOT NULL,
    name VARCHAR(200) NOT NULL,
    height_cm INTEGER NOT NULL DEFAULT 0,
    position VARCHAR(60) NOT NULL,
    birth_date DATE,
    CONSTRAINT fk_players_team
        FOREIGN KEY (team_id) REFERENCES teams (id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events_timeline (
    id BIGSERIAL PRIMARY KEY,
    match_id BIGINT NOT NULL,
    minute INTEGER NOT NULL,
    second INTEGER NOT NULL DEFAULT 0,
    team_id BIGINT,
    event_type VARCHAR(30) NOT NULL,
    description TEXT,
    player_name VARCHAR(200),
    assist_name VARCHAR(200),
    CONSTRAINT fk_events_match
        FOREIGN KEY (match_id) REFERENCES matches (id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_events_team
        FOREIGN KEY (team_id) REFERENCES teams (id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT chk_events_event_type
        CHECK (event_type IN (
            'goal',
            'shot_on_target',
            'shot_off_target',
            'corner',
            'foul',
            'yellow',
            'red',
            'substitution'
        ))
);

CREATE TABLE IF NOT EXISTS external_factors (
    id BIGSERIAL PRIMARY KEY,
    match_id BIGINT NOT NULL,
    temperature DOUBLE PRECISION,
    humidity DOUBLE PRECISION,
    wind_speed DOUBLE PRECISION,
    weather_condition VARCHAR(100),
    travel_distance_home INTEGER,
    travel_distance_away INTEGER,
    CONSTRAINT fk_external_factors_match
        FOREIGN KEY (match_id) REFERENCES matches (id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS state_transitions (
    id BIGSERIAL PRIMARY KEY,
    match_id BIGINT NOT NULL,
    minute INTEGER NOT NULL,
    home_state VARCHAR(20) NOT NULL,
    away_state VARCHAR(20) NOT NULL,
    home_goals_at_minute INTEGER NOT NULL DEFAULT 0,
    away_goals_at_minute INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT fk_state_transitions_match
        FOREIGN KEY (match_id) REFERENCES matches (id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT chk_state_transitions_home_state
        CHECK (home_state IN ('equilibrio', 'repliegue', 'asedio')),
    CONSTRAINT chk_state_transitions_away_state
        CHECK (away_state IN ('equilibrio', 'repliegue', 'asedio'))
);

CREATE TABLE IF NOT EXISTS raw_api_responses (
    id BIGSERIAL PRIMARY KEY,
    match_id BIGINT NOT NULL,
    endpoint VARCHAR(255) NOT NULL,
    response_json JSONB NOT NULL,
    fetched_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_raw_api_responses_match
        FOREIGN KEY (match_id) REFERENCES matches (id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS processed_matches_control (
    id BIGSERIAL PRIMARY KEY,
    match_id BIGINT NOT NULL,
    season VARCHAR(20) NOT NULL,
    processed_at TIMESTAMP NOT NULL DEFAULT NOW(),
    status VARCHAR(30) NOT NULL,
    error_message TEXT,
    CONSTRAINT fk_processed_matches_control_match
        FOREIGN KEY (match_id) REFERENCES matches (id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_matches_home_team_id ON matches (home_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_away_team_id ON matches (away_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_match_date ON matches (match_date);
CREATE INDEX IF NOT EXISTS idx_players_team_id ON players (team_id);
CREATE INDEX IF NOT EXISTS idx_events_timeline_match_id ON events_timeline (match_id);
CREATE INDEX IF NOT EXISTS idx_events_timeline_team_id ON events_timeline (team_id);
CREATE INDEX IF NOT EXISTS idx_external_factors_match_id ON external_factors (match_id);
CREATE INDEX IF NOT EXISTS idx_state_transitions_match_id ON state_transitions (match_id);
CREATE INDEX IF NOT EXISTS idx_raw_api_responses_match_id ON raw_api_responses (match_id);
CREATE INDEX IF NOT EXISTS idx_processed_matches_control_match_id ON processed_matches_control (match_id);

COMMENT ON TABLE teams IS 'Catálogo de equipos participantes en el sistema predictivo.';
COMMENT ON COLUMN teams.id IS 'Identificador único del equipo.';
COMMENT ON COLUMN teams.name IS 'Nombre oficial del equipo.';
COMMENT ON COLUMN teams.city IS 'Ciudad de origen del equipo.';
COMMENT ON COLUMN teams.stadium IS 'Estadio principal del equipo.';
COMMENT ON COLUMN teams.altitude IS 'Altitud de la ciudad o estadio en metros sobre el nivel del mar.';
COMMENT ON COLUMN teams.foundation_year IS 'Año de fundación del equipo.';

COMMENT ON TABLE matches IS 'Partidos programados o disputados entre dos equipos.';
COMMENT ON COLUMN matches.id IS 'Identificador único del partido.';
COMMENT ON COLUMN matches.home_team_id IS 'Equipo local del partido.';
COMMENT ON COLUMN matches.away_team_id IS 'Equipo visitante del partido.';
COMMENT ON COLUMN matches.match_date IS 'Fecha y hora del partido.';
COMMENT ON COLUMN matches.season IS 'Temporada o torneo asociado al partido.';
COMMENT ON COLUMN matches.home_score IS 'Goles anotados por el equipo local.';
COMMENT ON COLUMN matches.away_score IS 'Goles anotados por el equipo visitante.';
COMMENT ON COLUMN matches.status IS 'Estado del partido: programado, en curso, finalizado u otro.';

COMMENT ON TABLE players IS 'Plantilla de jugadores asociada a cada equipo.';
COMMENT ON COLUMN players.id IS 'Identificador único del jugador.';
COMMENT ON COLUMN players.team_id IS 'Equipo al que pertenece el jugador.';
COMMENT ON COLUMN players.name IS 'Nombre completo del jugador.';
COMMENT ON COLUMN players.height_cm IS 'Estatura del jugador en centímetros.';
COMMENT ON COLUMN players.position IS 'Posición nominal del jugador.';
COMMENT ON COLUMN players.birth_date IS 'Fecha de nacimiento del jugador.';

COMMENT ON TABLE events_timeline IS 'Secuencia cronológica de eventos del partido.';
COMMENT ON COLUMN events_timeline.id IS 'Identificador único del evento.';
COMMENT ON COLUMN events_timeline.match_id IS 'Partido al que pertenece el evento.';
COMMENT ON COLUMN events_timeline.minute IS 'Minuto en el que ocurrió el evento.';
COMMENT ON COLUMN events_timeline.second IS 'Segundo exacto dentro del minuto del evento.';
COMMENT ON COLUMN events_timeline.team_id IS 'Equipo asociado al evento.';
COMMENT ON COLUMN events_timeline.event_type IS 'Tipo de evento observado en el partido.';
COMMENT ON COLUMN events_timeline.description IS 'Descripción libre del evento.';
COMMENT ON COLUMN events_timeline.player_name IS 'Nombre del jugador principal involucrado.';
COMMENT ON COLUMN events_timeline.assist_name IS 'Nombre del jugador que asistió, si aplica.';

COMMENT ON TABLE external_factors IS 'Factores externos del partido, como clima y distancias de viaje.';
COMMENT ON COLUMN external_factors.id IS 'Identificador único del registro de factores externos.';
COMMENT ON COLUMN external_factors.match_id IS 'Partido al que corresponden los factores externos.';
COMMENT ON COLUMN external_factors.temperature IS 'Temperatura ambiente en grados Celsius.';
COMMENT ON COLUMN external_factors.humidity IS 'Humedad relativa porcentual.';
COMMENT ON COLUMN external_factors.wind_speed IS 'Velocidad del viento.';
COMMENT ON COLUMN external_factors.weather_condition IS 'Condición meteorológica resumida.';
COMMENT ON COLUMN external_factors.travel_distance_home IS 'Distancia de viaje del equipo local.';
COMMENT ON COLUMN external_factors.travel_distance_away IS 'Distancia de viaje del equipo visitante.';

COMMENT ON TABLE state_transitions IS 'Estados tácticos o de contexto a lo largo del partido.';
COMMENT ON COLUMN state_transitions.id IS 'Identificador único de la transición de estado.';
COMMENT ON COLUMN state_transitions.match_id IS 'Partido al que pertenece la transición.';
COMMENT ON COLUMN state_transitions.minute IS 'Minuto de referencia de la transición.';
COMMENT ON COLUMN state_transitions.home_state IS 'Estado contextual del equipo local.';
COMMENT ON COLUMN state_transitions.away_state IS 'Estado contextual del equipo visitante.';
COMMENT ON COLUMN state_transitions.home_goals_at_minute IS 'Goles del local acumulados en ese minuto.';
COMMENT ON COLUMN state_transitions.away_goals_at_minute IS 'Goles del visitante acumulados en ese minuto.';

COMMENT ON TABLE raw_api_responses IS 'Persistencia del JSON crudo recibido desde la API externa.';
COMMENT ON COLUMN raw_api_responses.id IS 'Identificador único de la respuesta cruda.';
COMMENT ON COLUMN raw_api_responses.match_id IS 'Partido asociado a la respuesta cruda.';
COMMENT ON COLUMN raw_api_responses.endpoint IS 'Endpoint de origen de la respuesta.';
COMMENT ON COLUMN raw_api_responses.response_json IS 'Payload JSONB original de la API.';
COMMENT ON COLUMN raw_api_responses.fetched_at IS 'Fecha y hora en que se obtuvo la respuesta.';

COMMENT ON TABLE processed_matches_control IS 'Control de backfill y estado de procesamiento de partidos.';
COMMENT ON COLUMN processed_matches_control.id IS 'Identificador único del control de procesamiento.';
COMMENT ON COLUMN processed_matches_control.match_id IS 'Partido procesado o en proceso.';
COMMENT ON COLUMN processed_matches_control.season IS 'Temporada asociada al procesamiento.';
COMMENT ON COLUMN processed_matches_control.processed_at IS 'Fecha y hora del procesamiento.';
COMMENT ON COLUMN processed_matches_control.status IS 'Estado del proceso: pendiente, procesado, fallido u otro.';
COMMENT ON COLUMN processed_matches_control.error_message IS 'Mensaje de error en caso de fallo.';

INSERT INTO teams (id, name, city, stadium, altitude, foundation_year)
VALUES
    (1, 'Real Madrid', 'Madrid', 'Santiago Bernabéu', 667, 1902),
    (2, 'Barcelona', 'Barcelona', 'Spotify Camp Nou', 12, 1899)
ON CONFLICT (id) DO NOTHING;

INSERT INTO matches (
    id,
    home_team_id,
    away_team_id,
    match_date,
    season,
    home_score,
    away_score,
    status
)
VALUES (
    1,
    1,
    2,
    TIMESTAMP '2026-07-14 20:00:00',
    '2026',
    0,
    0,
    'programado'
)
ON CONFLICT (id) DO NOTHING;

COMMIT;

-- Version: 1.0.0
-- Fecha de creación: 2026-07-14
