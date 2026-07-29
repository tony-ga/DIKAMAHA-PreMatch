"""Preflight read-only validation before running migrations.

This script performs only SELECT queries and never writes to PostgreSQL.
It is phase-aware:
- FASE 0 validates the base schema required to start migration 001.
- Optional phase-1 columns are reported as PENDING when they do not yet exist.
- It reports:
  - OK for passing checks,
  - WARNING for non-blocking issues,
  - PENDING for not-yet-created migration columns,
  - ERROR for blocking issues.

Exit codes:
- 0 when migration can proceed,
- non-zero when execution must stop.

Requirements:
    pip install sqlalchemy python-dotenv psycopg2-binary
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()

LOGGER = logging.getLogger("migration_preflight")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)


@dataclass(slots=True)
class PreflightResult:
    """Aggregated preflight findings."""

    ok: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    _seen: set[tuple[str, str]] = field(default_factory=set, init=False, repr=False)

    def _add_unique(self, bucket: list[str], level: str, message: str) -> None:
        """Append a message once per severity."""

        key = (level, message)
        if key in self._seen:
            return
        self._seen.add(key)
        bucket.append(message)

    def add_ok(self, message: str) -> None:
        """Register a passing check."""

        self._add_unique(self.ok, "ok", message)

    def add_warning(self, message: str) -> None:
        """Register a warning."""

        self._add_unique(self.warnings, "warning", message)

    def add_pending(self, message: str) -> None:
        """Register a pending migration item."""

        self._add_unique(self.pending, "pending", message)

    def add_error(self, message: str) -> None:
        """Register a blocking error."""

        self._add_unique(self.errors, "error", message)

    def has_errors(self) -> bool:
        """Return True when blocking issues exist."""

        return bool(self.errors)


EXPECTED_TABLES = {"teams", "matches", "events_timeline", "raw_api_responses"}
EXPECTED_COLUMNS = {
    "teams": {"id", "name", "city", "stadium", "altitude", "foundation_year"},
    "matches": {"id", "home_team_id", "away_team_id", "match_date", "season", "home_score", "away_score", "status"},
    "raw_api_responses": {"id", "match_id", "endpoint", "response_json", "fetched_at"},
    "events_timeline": {"id", "match_id", "minute", "second", "team_id", "event_type", "description", "player_name", "assist_name"},
}
OPTIONAL_PHASE_COLUMNS = {
    "teams": {"espn_team_id"},
    "raw_api_responses": {"source", "source_event_id", "source_competition_id", "page_number", "page_count", "total_count", "http_status", "response_hash"},
    "events_timeline": {"event_ledger_id", "event_type_raw", "athlete_ref", "raw_data", "created_at"},
}


def fetch_rows(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Fetch rows using a read-only SELECT."""

    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result.fetchall()]


def fetch_scalar(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> Any:
    """Fetch one scalar value using a read-only SELECT."""

    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar_one()


def table_exists(engine: Engine, table_name: str) -> bool:
    """Check whether a table exists."""

    return bool(
        fetch_scalar(
            engine,
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = :table_name
            )
            """,
            {"table_name": table_name},
        )
    )


def column_exists(engine: Engine, table_name: str, column_name: str) -> bool:
    """Check whether a column exists."""

    return bool(
        fetch_scalar(
            engine,
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = :table_name
                  AND column_name = :column_name
            )
            """,
            {"table_name": table_name, "column_name": column_name},
        )
    )


def validate_required_tables(engine: Engine, result: PreflightResult) -> None:
    """Verify that mandatory tables exist."""

    missing = [table for table in sorted(EXPECTED_TABLES) if not table_exists(engine, table)]
    if missing:
        result.add_error(f"Missing required tables: {missing}")
    else:
        result.add_ok("All required tables exist.")


def validate_required_columns(engine: Engine, result: PreflightResult) -> None:
    """Verify that mandatory columns exist, while allowing extra columns."""

    for table, expected in EXPECTED_COLUMNS.items():
        rows = fetch_rows(
            engine,
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :table_name
            ORDER BY ordinal_position
            """,
            {"table_name": table},
        )
        if not rows:
            result.add_error(f"Table missing or inaccessible: {table}")
            continue
        existing = {row["column_name"] for row in rows}
        missing = expected - existing
        if missing:
            result.add_error(f"Missing required columns in {table}: {sorted(missing)}")
        else:
            result.add_ok(f"Required columns present in {table}.")


def validate_optional_phase_columns(engine: Engine, result: PreflightResult) -> None:
    """Report phase-1 columns as pending if they do not exist yet."""

    for table, optional_columns in OPTIONAL_PHASE_COLUMNS.items():
        rows = fetch_rows(
            engine,
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :table_name
            """,
            {"table_name": table},
        )
        existing = {row["column_name"] for row in rows}
        for column in sorted(optional_columns):
            if column in existing:
                result.add_ok(f"{table}.{column} present.")
            else:
                result.add_pending(f"{table}.{column} pending — será creada por la migración 001.")


def validate_team_id_mapping(engine: Engine, result: PreflightResult) -> None:
    """Validate internal match FKs and optional ESPN team mappings."""

    invalid_internal_refs = fetch_rows(
        engine,
        """
        SELECT m.id AS match_id,
               m.home_team_id,
               m.away_team_id
        FROM matches m
        LEFT JOIN teams th ON th.id = m.home_team_id
        LEFT JOIN teams ta ON ta.id = m.away_team_id
        WHERE (m.home_team_id IS NOT NULL AND th.id IS NULL)
           OR (m.away_team_id IS NOT NULL AND ta.id IS NULL)
        ORDER BY m.id
        """,
    )
    if invalid_internal_refs:
        result.add_error(f"Broken internal match team FKs: {invalid_internal_refs}")
    else:
        result.add_ok("matches.home_team_id and matches.away_team_id reference teams.id correctly.")

    if not column_exists(engine, "teams", "espn_team_id"):
        result.add_pending("teams.espn_team_id pending — será creada por la migración 001.")
        result.add_warning("External ESPN team mapping is not available yet.")
        return

    dup_rows = fetch_rows(
        engine,
        """
        SELECT espn_team_id, COUNT(*) AS n
        FROM teams
        WHERE espn_team_id IS NOT NULL
        GROUP BY espn_team_id
        HAVING COUNT(*) > 1
        """,
    )
    if dup_rows:
        result.add_error(f"Duplicate teams.espn_team_id values: {dup_rows}")
    else:
        result.add_ok("No duplicate teams.espn_team_id values.")

    unmapped_matches = fetch_rows(
        engine,
        """
        SELECT m.id AS match_id,
               'home' AS side,
               m.home_team_id AS internal_team_id,
               th.espn_team_id AS espn_team_id
        FROM matches m
        LEFT JOIN teams th ON th.id = m.home_team_id
        WHERE m.home_team_id IS NOT NULL
          AND th.espn_team_id IS NULL
        UNION ALL
        SELECT m.id AS match_id,
               'away' AS side,
               m.away_team_id AS internal_team_id,
               ta.espn_team_id AS espn_team_id
        FROM matches m
        LEFT JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.away_team_id IS NOT NULL
          AND ta.espn_team_id IS NULL
        ORDER BY match_id, side
        """,
    )
    if unmapped_matches:
        result.add_pending(
            "External ESPN mapping missing for some match teams: "
            f"{unmapped_matches}"
        )
    else:
        result.add_ok("All match home/away teams have optional ESPN ids when available.")


def validate_raw_api_responses(engine: Engine, result: PreflightResult) -> None:
    """Validate raw_api_responses presence and hash status."""

    if not table_exists(engine, "raw_api_responses"):
        result.add_error("raw_api_responses table does not exist.")
        return
    result.add_ok("raw_api_responses exists.")

    if column_exists(engine, "raw_api_responses", "response_hash"):
        null_hashes = fetch_scalar(
            engine,
            "SELECT COUNT(*) FROM raw_api_responses WHERE response_hash IS NULL",
        )
        if null_hashes:
            result.add_error(f"raw_api_responses has {null_hashes} NULL response_hash values.")
        else:
            result.add_ok("No NULL response_hash values.")

        duplicates = fetch_rows(
            engine,
            """
            SELECT match_id,
                   endpoint,
                   COALESCE(source_event_id, '') AS source_event_id,
                   COALESCE(source_competition_id, '') AS source_competition_id,
                   page_number,
                   response_hash,
                   COUNT(*) AS n
            FROM raw_api_responses
            GROUP BY 1,2,3,4,5,6
            HAVING COUNT(*) > 1
            """,
        )
        if duplicates:
            result.add_error(f"Duplicate raw_api_responses detected: {duplicates}")
        else:
            result.add_ok("No duplicate raw_api_responses detected.")
    else:
        result.add_pending("raw_api_responses.response_hash pending — será creada por la migración 001.")


def validate_matches(engine: Engine, result: PreflightResult) -> None:
    """Validate that match foreign keys reference teams.id."""

    invalid_rows = fetch_rows(
        engine,
        """
        SELECT m.id AS match_id, m.home_team_id, m.away_team_id
        FROM matches m
        LEFT JOIN teams th ON th.id = m.home_team_id
        LEFT JOIN teams ta ON ta.id = m.away_team_id
        WHERE (m.home_team_id IS NOT NULL AND th.id IS NULL)
           OR (m.away_team_id IS NOT NULL AND ta.id IS NULL)
        ORDER BY m.id
        """,
    )
    if invalid_rows:
        result.add_error(f"Matches with unmapped ESPN team ids: {invalid_rows}")
    else:
        result.add_ok("All matches have valid internal team foreign keys.")


def validate_team_id_compatibility(engine: Engine, result: PreflightResult) -> None:
    """Validate consistency between ESPN team ids and internal team ids."""

    if not column_exists(engine, "teams", "espn_team_id"):
        result.add_pending("teams.espn_team_id pending — será creada por la migración 001.")
        return
    rows = fetch_rows(
        engine,
        """
        SELECT t.id AS internal_team_id, t.espn_team_id
        FROM teams t
        WHERE t.espn_team_id IS NOT NULL
        ORDER BY t.espn_team_id
        """,
    )
    if not rows:
        result.add_warning("No teams.espn_team_id values present yet; external mapping remains unavailable.")
        return
    result.add_ok(f"Found {len(rows)} mapped internal team records.")


def validate_preflight(engine: Engine) -> PreflightResult:
    """Run all read-only checks."""

    result = PreflightResult()
    validate_required_tables(engine, result)
    validate_required_columns(engine, result)
    validate_optional_phase_columns(engine, result)
    validate_raw_api_responses(engine, result)
    validate_team_id_mapping(engine, result)
    validate_matches(engine, result)
    validate_team_id_compatibility(engine, result)
    return result


def print_report(result: PreflightResult) -> None:
    """Print a machine-friendly summary."""

    for message in result.ok:
        print(f"OK: {message}")
    for message in result.warnings:
        print(f"WARNING: {message}")
    for message in result.pending:
        print(f"PENDING: {message}")
    for message in result.errors:
        print(f"ERROR: {message}")


def main() -> int:
    """Entry point for the read-only preflight."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is required.")
        return 2
    try:
        engine = create_engine(database_url, future=True)
        result = validate_preflight(engine)
        print_report(result)
        return 0 if not result.has_errors() else 1
    except SQLAlchemyError as exc:
        print(f"ERROR: database connection or query failed: {exc}")
        return 3
    except Exception as exc:
        print(f"ERROR: unexpected preflight failure: {exc}")
        return 4


if __name__ == "__main__":
    sys.exit(main())
