"""Inventario SELECT-only de ``prospective_staging_v2``.

Requirements:
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-16
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

from src.evaluate_prospective_espn import _excluded_ids
from src.postgres_readonly_staging import ReadonlyDatabase, counts_identical, database_error_types, detect_capabilities, sanitize_error

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_16_prospective_evaluation/staging_inventory.json"
SCHEMA = "prospective_staging_v2"
TABLES = ("matches", "events", "raw_payloads", "rejected_raw_events", "ingestion_runs", "write_audit")
LOGGER = logging.getLogger(__name__)
if load_dotenv:
    load_dotenv(ROOT / ".env")


def _hash(value: Any) -> str:
    """Calcula un hash estable sin conservar datos sensibles."""

    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _write(value: dict[str, Any]) -> None:
    """Escribe el inventario atómicamente."""

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(OUTPUT)


def _existing_tables(session: Any) -> list[str]:
    """Obtiene sólo nombres de tablas staging v2 mediante information_schema."""

    rows = session.rows("SELECT table_name FROM information_schema.tables WHERE table_schema='prospective_staging_v2' AND table_type='BASE TABLE' ORDER BY table_name")
    return [str(row["table_name"]) for row in rows if str(row["table_name"]) in TABLES]


def _counts(session: Any, existing: list[str]) -> dict[str, int]:
    """Cuenta tablas conocidas sin interpolar nombres externos."""

    output: dict[str, int] = {}
    for table in TABLES:
        output[table] = int(session.scalar(f"SELECT COUNT(*) FROM prospective_staging_v2.{table}")) if table in existing else 0
    return output


def _match_rows(session: Any, existing: list[str]) -> list[dict[str, Any]]:
    """Lee identidad y cierre de partidos, nunca payloads."""

    if "matches" not in existing:
        return []
    return session.rows("SELECT provider_match_id::bigint AS match_id, kickoff_ts, provider_status, complete, home_score, away_score FROM prospective_staging_v2.matches WHERE provider='espn' ORDER BY kickoff_ts, provider_match_id")


def _rejection_rows(session: Any, existing: list[str]) -> list[dict[str, Any]]:
    """Agrupa rechazos por razón sin incluir raw_data."""

    if "rejected_raw_events" not in existing:
        return []
    return session.rows("SELECT provider_match_id::bigint AS match_id, reason, COUNT(*) AS count FROM prospective_staging_v2.rejected_raw_events WHERE provider='espn' GROUP BY provider_match_id, reason ORDER BY provider_match_id, reason")


def _run_rows(session: Any, existing: list[str]) -> list[dict[str, Any]]:
    """Obtiene conteos por ingestion_run usando auditoría, sin payloads."""

    if "ingestion_runs" not in existing:
        return []
    return session.rows("SELECT run_hash, status, created_at FROM prospective_staging_v2.ingestion_runs ORDER BY created_at, run_hash")


def _received_dates(session: Any, existing: list[str]) -> list[str]:
    """Lee timestamps de recepción sin devolver el payload JSON."""

    if "raw_payloads" not in existing:
        return []
    rows = session.rows("SELECT fetched_at FROM prospective_staging_v2.raw_payloads WHERE provider='espn' ORDER BY fetched_at")
    return [_date(row["fetched_at"]) for row in rows]


def _run_counts(session: Any, existing: list[str], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Relaciona cada corrida con filas auditadas y evita secretos."""

    if "write_audit" not in existing:
        return [{**row, "row_counts": {}} for row in runs]
    audits = session.rows("SELECT run_hash, table_name, SUM(inserted_rows) AS inserted_rows FROM prospective_staging_v2.write_audit GROUP BY run_hash, table_name ORDER BY run_hash, table_name")
    grouped: dict[str, dict[str, int]] = {}
    for row in audits:
        grouped.setdefault(str(row["run_hash"]), {})[str(row["table_name"])] = int(row["inserted_rows"])
    return [{**row, "run_hash": str(row["run_hash"]), "row_counts": grouped.get(str(row["run_hash"]), {})} for row in runs]


def _classification(counts: dict[str, int], eligible: list[int], incomplete: list[int], audit: dict[str, Any]) -> str:
    """Clasifica el inventario sin alterar el staging."""

    if not audit["select_only"] or not audit["identical"]:
        return "staging_inventory_rejected_for_revision"
    if not counts.get("matches") and not counts.get("events"):
        return "staging_empty"
    if eligible and incomplete:
        return "staging_contains_incomplete_matches"
    if eligible:
        return "staging_contains_eligible_matches"
    return "staging_contains_only_excluded_matches"


def _lifecycle_state(row: dict[str, Any]) -> str:
    """Clasifica un partido staging por estado ESPN y completitud."""

    status = str(row.get("provider_status") or "").lower()
    if bool(row.get("complete")) or status in {"post", "final", "finished", "completed", "full_time"}:
        return "completed"
    if status in {"in", "in_progress", "live"}:
        return "live"
    if status in {"pre", "scheduled"}:
        return "scheduled"
    return "incomplete"


def _inventory(database_url: str) -> dict[str, Any]:
    """Ejecuta todas las consultas del inventario con conexión efímera."""

    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        existing = _existing_tables(session)
        before = _counts(session, existing)
        matches = _match_rows(session, existing)
        rejected = _rejection_rows(session, existing)
        runs = _run_rows(session, existing)
        run_counts = _run_counts(session, existing, runs)
        received_dates = _received_dates(session, existing)
        after = _counts(session, existing)
    audit = {"status": "postgres_readonly_verified", "source": SCHEMA, "allowlist": [f"{SCHEMA}.{table}" for table in TABLES], "before": before, "after": after, "identical": counts_identical(before, after), "connection_closed": database.closed, "statements": database.statements, "write_statements": 0, "select_only": all(item.lstrip().upper().startswith("SELECT ") for item in database.statements)}
    excluded = _excluded_ids()
    eligible = [int(row["match_id"]) for row in matches if int(row["match_id"]) not in excluded]
    complete = [int(row["match_id"]) for row in matches if bool(row["complete"]) and int(row["match_id"]) in eligible]
    incomplete = [match_id for match_id in eligible if match_id not in complete]
    dates = [_date(row["kickoff_ts"]) for row in matches]
    lifecycle = Counter(_lifecycle_state(row) for row in matches)
    payload = {"database_connection_verified": True, "staging_tables": {"schema": SCHEMA, "existing": existing, "expected": list(TABLES)}, "counts": {**before, "matches_complete": len([row for row in matches if bool(row["complete"])]), "matches_incomplete": len(matches) - len([row for row in matches if bool(row["complete"])]), "scheduled": lifecycle["scheduled"], "live": lifecycle["live"], "incomplete": lifecycle["incomplete"], "completed": lifecycle["completed"], "rejected": before.get("rejected_raw_events", 0), "payload_received": before.get("raw_payloads", 0), "normalized_rows": before.get("matches", 0) + before.get("events", 0), "rejected_rows": before.get("rejected_raw_events", 0)}, "date_range": {"min": min(dates) if dates else None, "max": max(dates) if dates else None}, "eligible_matches": {"match_ids": eligible, "complete_match_ids": complete, "incomplete_match_ids": incomplete}, "excluded_matches": {"historical_match_ids": sorted(excluded - {704766}), "blocked_704766": 704766 in {int(row["match_id"]) for row in matches}, "excluded_present_match_ids": sorted(set(int(row["match_id"]) for row in matches) & excluded)}, "rejected_records": {"total": before.get("rejected_raw_events", 0), "by_reason": dict(Counter(str(row["reason"]) for row in rejected)), "by_match_reason": rejected}, "ingestion_runs": run_counts, "diagnosis": _diagnosis(before, matches, runs, run_counts, received_dates), "postgres_readonly_audit": audit}
    payload["classification"] = _classification(before, eligible, incomplete, audit)
    payload["hashes"] = {key: _hash(payload[key]) for key in ("staging_tables", "counts", "date_range", "eligible_matches", "excluded_matches", "rejected_records", "ingestion_runs", "diagnosis", "postgres_readonly_audit")}
    return payload


def _date(value: Any) -> str:
    """Normaliza una fecha sin revelar configuración de conexión."""

    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).isoformat()


def _diagnosis(counts: dict[str, int], matches: list[dict[str, Any]], runs: list[dict[str, Any]], run_counts: list[dict[str, Any]], received_dates: list[str]) -> dict[str, Any]:
    """Explica 20260714, 20260717 y divergencias entre capas de ingesta."""

    dates = [_date(row["kickoff_ts"]) for row in matches]
    inserted_0714 = any("20260714" in str(row.get("created_at", "")) and sum(row.get("row_counts", {}).values()) > 0 for row in run_counts)
    data_0717 = any(item.startswith("2026-07-17") for item in received_dates)
    return {"ingestion_20260714_inserted_rows": inserted_0714, "data_20260717_present": data_0717, "payload_received_rows": counts.get("raw_payloads", 0), "normalized_match_rows": counts.get("matches", 0), "normalized_event_rows": counts.get("events", 0), "rejected_rows": counts.get("rejected_raw_events", 0), "payload_to_normalized_difference": counts.get("raw_payloads", 0) - counts.get("matches", 0) - counts.get("events", 0), "normalization_to_rejection_difference": counts.get("matches", 0) + counts.get("events", 0) - counts.get("rejected_raw_events", 0), "run_count": len(runs)}


def _incomplete(reason: str) -> dict[str, Any]:
    """Genera un inventario honesto cuando no puede abrirse PostgreSQL."""

    audit = {"status": "postgres_readonly_unavailable", "source": SCHEMA, "allowlist": [f"{SCHEMA}.{table}" for table in TABLES], "before": None, "after": None, "identical": False, "connection_closed": False, "statements": [], "write_statements": 0, "select_only": True, "reason": reason}
    payload = {"database_connection_verified": False, "staging_tables": {"schema": SCHEMA, "existing": [], "expected": list(TABLES)}, "counts": {}, "date_range": {"min": None, "max": None}, "eligible_matches": {"match_ids": [], "complete_match_ids": [], "incomplete_match_ids": []}, "excluded_matches": {"historical_match_ids": [], "blocked_704766": False, "excluded_present_match_ids": []}, "rejected_records": {"total": 0, "by_reason": {}, "by_match_reason": []}, "ingestion_runs": [], "diagnosis": {"reason": reason, "ingestion_20260714_inserted_rows": None, "data_20260717_present": None, "payload_received_rows": None, "normalized_match_rows": None, "normalized_event_rows": None, "rejected_rows": None}, "postgres_readonly_audit": audit, "classification": "staging_inventory_rejected_for_revision"}
    payload["hashes"] = {key: _hash(payload[key]) for key in payload if key != "hashes"}
    return payload


def main() -> int:
    """Ejecuta y persiste el inventario SELECT-only."""

    capabilities = detect_capabilities()
    if not capabilities.ready:
        result = _incomplete(f"missing:{','.join(capabilities.missing())}")
    else:
        try:
            result = _inventory(os.environ["DATABASE_URL"])
        except (ValueError, OSError, *database_error_types()) as error:
            result = _incomplete(sanitize_error(error, os.environ.get("DATABASE_URL")))
    _write(result)
    LOGGER.info("Inventario staging v2: %s", result["classification"])
    return 1 if result["classification"] == "staging_inventory_rejected_for_revision" else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
