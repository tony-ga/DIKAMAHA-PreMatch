BEGIN;

-- Seed preparado para `esp.1` / 2024-25.
-- Requiere que `teams` ya admita NULL en `altitude` y `foundation_year`.
-- No usa el nombre como identidad: la identidad externa es `espn_team_id`.
-- No inserta automáticamente si alguna de las ESPN IDs ya existe.

DO $$
DECLARE
    duplicate_count integer;
BEGIN
    SELECT count(*)
    INTO duplicate_count
    FROM teams
    WHERE espn_team_id IN (84, 85, 88, 89, 93, 94, 95, 96, 97, 98, 101, 102, 243, 244, 1068, 2922, 9812, 17534);

    IF duplicate_count > 0 THEN
        RAISE EXCEPTION
            'Seed 008 abortado: ya existen % equipos con espn_team_id en la lista propuesta',
            duplicate_count;
    END IF;
END
$$;

INSERT INTO teams (name, city, stadium, altitude, foundation_year, espn_team_id)
VALUES
    ('Mallorca', 'Palma de Mallorca', 'Estadi Mallorca Son Moix', NULL, NULL, 84),
    ('Celta Vigo', 'Vigo', 'Balaidos', NULL, NULL, 85),
    ('Espanyol', 'Barcelona', 'RCDE Stadium', NULL, NULL, 88),
    ('Real Sociedad', 'San Sebastian', 'Reale Arena', NULL, NULL, 89),
    ('Athletic Club', 'Bilbao', 'San Mamés', NULL, NULL, 93),
    ('Valencia', 'Manises', 'Mestalla Stadium', NULL, NULL, 94),
    ('Real Valladolid', 'Valladolid', 'Estadio José Zorrilla', NULL, NULL, 95),
    ('Alavés', 'Vitoria-Gasteiz', 'Mendizorrotza', NULL, NULL, 96),
    ('Osasuna', 'Pamplona', 'El Sadar', NULL, NULL, 97),
    ('Las Palmas', 'Las Palmas de Gran Canaria', 'Estadio Gran Canaria', NULL, NULL, 98),
    ('Rayo Vallecano', 'Madrid', 'Estadio de Vallecas', NULL, NULL, 101),
    ('Villarreal', 'Villarreal', 'Estadio de la Cerámica', NULL, NULL, 102),
    ('Sevilla', 'Sevilla', 'Ramón Sánchez Pizjuán Stadium', NULL, NULL, 243),
    ('Real Betis', 'Sevilla', 'Estadio La Cartuja', NULL, NULL, 244),
    ('Atlético Madrid', 'Madrid', 'Riyadh Air Metropolitano', NULL, NULL, 1068),
    ('Getafe', 'Getafe', 'Estadio Coliseum', NULL, NULL, 2922),
    ('Girona', 'Girona', 'Estadi Montilivi', NULL, NULL, 9812),
    ('Leganés', 'Leganés, Madrid', 'Estadio Municipal de Butarque', NULL, NULL, 17534);

COMMIT;
