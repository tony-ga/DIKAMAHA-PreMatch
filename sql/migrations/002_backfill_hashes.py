"""Backfill hashes for migration safety.

This script is intentionally non-destructive. It:
- computes canonical SHA-256 hashes in Python;
- detects duplicates before writing;
- stops on conflicts instead of auto-merging;

Requirements:
    pip install sqlalchemy python-dotenv psycopg2-binary
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

load_dotenv()

LOGGER = logging.getLogger("migration_002")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)


@dataclass(frozen=True)
class DuplicateConflict:
    """Represents a duplicate payload conflict."""

    key: tuple[Any, ...]
    count: int


EXPECTED_TABLE_COLUMNS: dict[str, set[str]] = {
    "teams": {"id", "espn_team_id", "name", "city", "stadium", "altitude", "foundation_year"},
    "matches": {"id", "home_team_id", "away_team_id", "match_date", "season", "home_score", "away_score", "status"},
    "raw_api_responses": {
        "id",
        "match_id",
        "endpoint",
        "response_json",
        "fetched_at",
        "source",
        "source_event_id",
        "source_competition_id",
        "page_number",
        "page_count",
        "total_count",
        "http_status",
        "response_hash",
    },
    "events_ledger": {
        "id",
        "match_id",
        "raw_api_response_id",
        "espn_play_id",
        "espn_event_uid",
        "event_index",
        "minute",
        "second",
        "period_number",
        "clock_value",
        "team_id",
        "athlete_ref",
        "event_type_raw",
        "event_type",
        "description",
        "player_name",
        "assist_name",
        "scoring_play",
        "penalty_kick",
        "valid",
        "raw_data",
        "content_hash",
        "created_at",
    },
    "events_timeline": {"id", "match_id", "minute", "second", "team_id", "event_type", "description", "player_name", "assist_name", "event_ledger_id", "event_type_raw", "athlete_ref", "raw_data", "created_at"},
}


def canonical_json(payload: Any) -> str:
    """Return a canonical JSON string.

    Args:
        payload: JSON-serializable object.

    Returns:
        Canonical JSON string.
    """

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(payload: Any) -> str:
    """Hash a payload using canonical JSON and SHA-256."""

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def fetch_rows(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Fetch rows as dictionaries."""

    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result.fetchall()]


def fetch_scalar(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> Any:
    """Fetch a scalar value with parameters."""

    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar_one()


def detect_duplicates(rows: Iterable[dict[str, Any]], key_fields: tuple[str, ...]) -> list[DuplicateConflict]:
    """Detect duplicate rows based on selected fields."""

    counts: dict[tuple[Any, ...], int] = {}
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        counts[key] = counts.get(key, 0) + 1
    return [DuplicateConflict(key=key, count=count) for key, count in counts.items() if count > 1]


def validate_required_schema(engine: Engine) -> None:
    """Abort if required tables or columns are missing."""

    missing: list[str] = []
    with engine.connect() as conn:
        for table, required_columns in EXPECTED_TABLE_COLUMNS.items():
            exists = conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = :table_name
                    )
                    """
                ),
                {"table_name": table},
            ).scalar_one()
            if not exists:
                missing.append(f"table:{table}")
                continue
            existing = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = :table_name
                        """
                    ),
                    {"table_name": table},
                ).fetchall()
            }
            absent = required_columns - existing
            missing.extend(f"{table}.{column}" for column in sorted(absent))
    if missing:
        raise RuntimeError(f"Missing required schema objects: {missing}")


def validate_teams_uniqueness(engine: Engine) -> None:
    """Abort if espn_team_id duplicates exist."""

    duplicates = fetch_rows(
        engine,
        """
        SELECT espn_team_id, COUNT(*) AS n
        FROM teams
        WHERE espn_team_id IS NOT NULL
        GROUP BY espn_team_id
        HAVING COUNT(*) > 1
        """,
    )
    if duplicates:
        raise RuntimeError(f"Duplicate espn_team_id values found: {duplicates}")


def backfill_raw_api_response_hashes(engine: Engine) -> None:
    """Backfill raw_api_responses.response_hash."""

    rows = fetch_rows(
        engine,
        """
        SELECT id, response_json
        FROM raw_api_responses
        ORDER BY id
        """,
    )
    updates = [{"id": row["id"], "response_hash": sha256_hex(row["response_json"])} for row in rows]
    with engine.begin() as conn:
        for row in updates:
            conn.execute(text("UPDATE raw_api_responses SET response_hash = :response_hash WHERE id = :id"), row)
    LOGGER.info("Backfilled raw_api_responses.response_hash for %s rows", len(updates))


def backfill_events_ledger_hashes(engine: Engine) -> None:
    """Backfill events_ledger.content_hash."""

    rows = fetch_rows(
        engine,
        """
        SELECT id, raw_data
        FROM events_ledger
        ORDER BY id
        """,
    )
    updates = [{"id": row["id"], "content_hash": sha256_hex(row["raw_data"])} for row in rows]
    with engine.begin() as conn:
        for row in updates:
            conn.execute(text("UPDATE events_ledger SET content_hash = :content_hash WHERE id = :id"), row)
    LOGGER.info("Backfilled events_ledger.content_hash for %s rows", len(updates))


def validate_no_null_hashes(engine: Engine) -> None:
    """Fail if any hash is still NULL."""

    checks = {
        "raw_api_responses": "SELECT COUNT(*) FROM raw_api_responses WHERE response_hash IS NULL",
        "events_ledger": "SELECT COUNT(*) FROM events_ledger WHERE content_hash IS NULL",
    }
    with engine.connect() as conn:
        for table, sql in checks.items():
            count = conn.execute(text(sql)).scalar_one()
            if count:
                raise RuntimeError(f"{table} still has {count} NULL hash values")


def validate_duplicates(engine: Engine) -> None:
    """Stop if duplicates are found."""

    raw_conflicts = fetch_rows(
        engine,
        """
        SELECT match_id, endpoint, COALESCE(source_event_id, '') AS source_event_id,
               COALESCE(source_competition_id, '') AS source_competition_id,
               page_number, response_hash, COUNT(*) AS n
        FROM raw_api_responses
        GROUP BY 1,2,3,4,5,6
        HAVING COUNT(*) > 1
        """,
    )
    ledger_conflicts = fetch_rows(
        engine,
        """
        SELECT match_id, content_hash, COUNT(*) AS n
        FROM events_ledger
        GROUP BY 1,2
        HAVING COUNT(*) > 1
        """,
    )
    if raw_conflicts or ledger_conflicts:
        raise RuntimeError(
            f"Duplicate conflicts detected: raw={raw_conflicts[:5]}, ledger={ledger_conflicts[:5]}"
        )


def resolve_internal_team_id(engine: Engine, espn_team_id: int) -> int:
    """Resolve an ESPN team id to an internal teams.id."""

    rows = fetch_rows(
        engine,
        """
        SELECT id
        FROM teams
        WHERE espn_team_id = :espn_team_id
        """,
        {"espn_team_id": espn_team_id},
    )
    if not rows:
        raise RuntimeError(f"Unmapped ESPN team id: {espn_team_id}")
    return int(rows[0]["id"])


def main() -> None:
    """Run the backfill workflow."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    engine = create_engine(database_url, future=True)
    validate_required_schema(engine)
    validate_teams_uniqueness(engine)
    with engine.begin():
        backfill_raw_api_response_hashes(engine)
        if table_exists(engine, "events_ledger"):
            backfill_events_ledger_hashes(engine)
    validate_no_null_hashes(engine)
    validate_duplicates(engine)


def table_exists(engine: Engine, table_name: str) -> bool:
    """Check whether a table exists."""

    sql = """
    SELECT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = :table_name
    )
    """
    with engine.connect() as conn:
        return bool(conn.execute(text(sql), {"table_name": table_name}).scalar_one())


if __name__ == "__main__":
    main()
