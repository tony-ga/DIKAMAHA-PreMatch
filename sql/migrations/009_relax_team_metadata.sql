BEGIN;

-- Migración estructural 009.
-- Objetivo: permitir que `teams.altitude` y `teams.foundation_year` queden NULL
-- mientras se resuelve el enriquecimiento ESPN.
-- No modifica IDs, nombres, ESPN IDs ni FKs existentes.

ALTER TABLE teams
    ALTER COLUMN altitude DROP DEFAULT;

ALTER TABLE teams
    ALTER COLUMN altitude DROP NOT NULL;

ALTER TABLE teams
    ALTER COLUMN foundation_year DROP NOT NULL;

COMMIT;
