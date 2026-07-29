"""Migración aditiva de staging v2 para conservar la liga ESPN.

La ejecución es de sólo lectura por defecto. ``--apply`` agrega la columna
``league_slug`` con valor compatible para las filas históricas ya existentes.

Requirements:
    SQLAlchemy==2.0.41
    psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import argparse
import logging
import os
from typing import Any

from sqlalchemy import create_engine, text

LOGGER = logging.getLogger(__name__)
SCHEMA = "prospective_staging_v2"


def _database_url() -> str:
    """Obtiene la conexión sin registrar su contenido."""

    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("missing_database_url")
    return value


def inspect(database_url: str) -> dict[str, Any]:
    """Comprueba la existencia de la columna mediante SELECT solamente."""

    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    query = text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema=:schema AND table_name='matches' "
        "AND column_name='league_slug')"
    )
    try:
        with engine.connect() as connection:
            present = bool(connection.execute(query, {"schema": SCHEMA}).scalar_one())
        return {"schema": SCHEMA, "table": "matches", "league_slug_present": present, "write_statements": 0}
    finally:
        engine.dispose()


def apply(database_url: str) -> dict[str, Any]:
    """Agrega ``league_slug`` de forma idempotente dentro de una transacción."""

    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    statement = text(
        f"ALTER TABLE {SCHEMA}.matches ADD COLUMN IF NOT EXISTS "
        "league_slug VARCHAR(80) NOT NULL DEFAULT 'esp.1'"
    )
    try:
        with engine.begin() as connection:
            connection.execute(statement)
        result = inspect(database_url)
        result["write_statements"] = 1
        return result
    finally:
        engine.dispose()


def main() -> int:
    """Ejecuta inspección o migración explícita."""

    parser = argparse.ArgumentParser(description="Migración staging v2 multi-liga")
    parser.add_argument("--apply", action="store_true", help="aplica la migración aditiva")
    args = parser.parse_args()
    result = apply(_database_url()) if args.apply else inspect(_database_url())
    LOGGER.info("Migración staging multi-liga: %s", result)
    return 0 if result["league_slug_present"] or not args.apply else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

