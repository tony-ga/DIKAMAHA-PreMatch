"""Auditor de solo lectura para un rango de `match_id`.

Verifica integridad de la ingesta, hashes, FKs, estadísticas v3 e identidad del partido
sin ejecutar escrituras ni nuevas ingestas.

Requirements:
    pip install sqlalchemy python-dotenv psycopg2-binary
"""

from __future__ import annotations

import argparse
import json
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

LOGGER = logging.getLogger("audit_backfill_range")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)


@dataclass(slots=True)
class MatchAudit:
    """Resultado de auditoría por partido."""

    match_id: int
    critical_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def passed(self) -> bool:
        """Indica si el partido no tiene errores bloqueantes."""

        return not self.critical_errors


def fetch_all(engine: Engine, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Ejecuta un SELECT parametrizado y devuelve filas como diccionarios."""

    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        return [dict(row._mapping) for row in result.fetchall()]


def fetch_one(engine: Engine, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Ejecuta un SELECT parametrizado que devuelve una sola fila."""

    with engine.connect() as conn:
        row = conn.execute(text(sql), params).mappings().first()
        return dict(row) if row is not None else None


def build_engine() -> Engine:
    """Construye el motor SQLAlchemy en modo de solo lectura."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL no está definida.")
    return create_engine(database_url, future=True, pool_pre_ping=True)


def load_match_context(engine: Engine, match_id: int) -> dict[str, Any] | None:
    """Carga identidad del partido, equipos internos y mapeo ESPN."""

    return fetch_one(
        engine,
        """
        SELECT
            m.id AS match_id,
            m.match_date,
            m.season,
            m.home_score,
            m.away_score,
            m.status,
            m.home_team_id,
            m.away_team_id,
            ht.name AS home_team_name,
            ht.espn_team_id AS home_espn_team_id,
            at.name AS away_team_name,
            at.espn_team_id AS away_espn_team_id
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        WHERE m.id = :match_id
        """,
        {"match_id": match_id},
    )


def load_ingestion_run(engine: Engine, match_id: int, version: str) -> dict[str, Any] | None:
    """Carga la corrida de ingesta para el partido y versión solicitados."""

    return fetch_one(
        engine,
        """
        SELECT id, source, espn_event_id, match_id, league, competition_id, season,
               status, started_at, heartbeat_at, finished_at, error_code, error_message,
               reconciliation_version, raw_items, ledger_events, timeline_events, statistics_rows, created_at
        FROM ingestion_runs
        WHERE match_id = :match_id
          AND source = 'espn'
          AND reconciliation_version = :version
        ORDER BY CASE WHEN status = 'success' THEN 0 ELSE 1 END, id
        LIMIT 1
        """,
        {"match_id": match_id, "version": version},
    )


def count_rows(engine: Engine, table: str, where_sql: str, params: dict[str, Any]) -> int:
    """Cuenta filas usando una consulta SELECT parametrizada."""

    sql = f"SELECT COUNT(*) AS n FROM {table} WHERE {where_sql}"
    return int(fetch_one(engine, sql, params)["n"])


def load_duplicate_rows(engine: Engine, table: str, group_sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Carga agrupaciones duplicadas de una tabla."""

    return fetch_all(engine, f"SELECT * FROM ({group_sql}) dup", params)


def audit_match(engine: Engine, match_id: int, version: str) -> MatchAudit:
    """Audita un partido individual sin modificar datos."""

    audit = MatchAudit(match_id=match_id)
    context = load_match_context(engine, match_id)
    if context is None:
        audit.critical_errors.append("match_missing")
        audit.data["match"] = None
        return audit
    audit.data["match"] = context
    run = load_ingestion_run(engine, match_id, version)
    audit.data["ingestion_run"] = run
    if run is None:
        audit.critical_errors.append("missing_ingestion_run")
        return audit
    _audit_identity(audit, context, run)
    _audit_counts(audit, engine, match_id, run)
    _audit_hashes(audit, engine, match_id)
    _audit_fks(audit, engine, match_id)
    _audit_statistics(audit, engine, match_id, version, run)
    return audit


def _audit_identity(audit: MatchAudit, context: dict[str, Any], run: dict[str, Any]) -> None:
    """Verifica identidad, orientación, fecha, marcador y estado."""

    if run["status"] != "success":
        audit.critical_errors.append(f"ingestion_run_not_success:{run['status']}")
    if int(run["match_id"]) != int(context["match_id"]):
        audit.critical_errors.append("run_match_id_mismatch")
    if int(context["home_team_id"]) == int(context["away_team_id"]):
        audit.critical_errors.append("home_away_same_team")
    if context["home_espn_team_id"] is None or context["away_espn_team_id"] is None:
        audit.critical_errors.append("missing_team_mapping")
    if run.get("espn_event_id") is None:
        audit.critical_errors.append("missing_espn_event_id")
    audit.data["identity"] = {
        "match_id": context["match_id"],
        "espn_event_id": run.get("espn_event_id"),
        "home_team": {
            "id": context["home_team_id"],
            "name": context["home_team_name"],
            "espn_team_id": context["home_espn_team_id"],
        },
        "away_team": {
            "id": context["away_team_id"],
            "name": context["away_team_name"],
            "espn_team_id": context["away_espn_team_id"],
        },
        "match_date": context["match_date"],
        "season": context["season"],
        "score": {"home": context["home_score"], "away": context["away_score"]},
        "status": context["status"],
    }


def _audit_counts(audit: MatchAudit, engine: Engine, match_id: int, run: dict[str, Any]) -> None:
    """Compara contadores de control contra tablas físicas."""

    counts = {
        "raw_api_responses": count_rows(engine, "raw_api_responses", "match_id = :match_id", {"match_id": match_id}),
        "events_ledger": count_rows(engine, "events_ledger", "match_id = :match_id", {"match_id": match_id}),
        "events_timeline": count_rows(engine, "events_timeline", "match_id = :match_id", {"match_id": match_id}),
        "match_statistics_v3": count_rows(
            engine,
            "match_statistics",
            "match_id = :match_id AND reconciliation_version = :version",
            {"match_id": match_id, "version": audit.data["ingestion_run"]["reconciliation_version"]},
        ),
        "match_statistics_total": count_rows(engine, "match_statistics", "match_id = :match_id", {"match_id": match_id}),
    }
    audit.data["counts"] = counts
    comparisons = {
        "events_ledger": ("ledger_events", counts["events_ledger"]),
        "events_timeline": ("timeline_events", counts["events_timeline"]),
        "match_statistics_v3": ("statistics_rows", counts["match_statistics_v3"]),
    }
    for table, (field, actual) in comparisons.items():
        expected = int(run[field])
        if actual != expected:
            audit.critical_errors.append(f"{table}_count_mismatch expected={expected} actual={actual}")
    if counts["raw_api_responses"] != 1:
        audit.critical_errors.append(f"raw_not_equal_1 actual={counts['raw_api_responses']}")
    if counts["match_statistics_v3"] != 2:
        audit.critical_errors.append(f"v3_stats_not_equal_2 actual={counts['match_statistics_v3']}")
    audit.data["ingestion_run_reported"] = {
        "raw_items": run["raw_items"],
        "ledger_events": run["ledger_events"],
        "timeline_events": run["timeline_events"],
        "statistics_rows": run["statistics_rows"],
    }


def _audit_hashes(audit: MatchAudit, engine: Engine, match_id: int) -> None:
    """Verifica hashes nulos y duplicados en raw y ledger."""

    raw_nulls = count_rows(engine, "raw_api_responses", "match_id = :match_id AND response_hash IS NULL", {"match_id": match_id})
    raw_dups = fetch_all(
        engine,
        """
        SELECT response_hash, COUNT(*) AS n
        FROM raw_api_responses
        WHERE match_id = :match_id
        GROUP BY response_hash
        HAVING COUNT(*) > 1
        """,
        {"match_id": match_id},
    )
    ledger_nulls = count_rows(engine, "events_ledger", "match_id = :match_id AND content_hash IS NULL", {"match_id": match_id})
    ledger_dups = fetch_all(
        engine,
        """
        SELECT content_hash, COUNT(*) AS n
        FROM events_ledger
        WHERE match_id = :match_id
        GROUP BY content_hash
        HAVING COUNT(*) > 1
        """,
        {"match_id": match_id},
    )
    audit.data["hashes"] = {
        "raw_nulls": raw_nulls,
        "raw_duplicates": raw_dups,
        "ledger_nulls": ledger_nulls,
        "ledger_duplicates": ledger_dups,
    }
    if raw_nulls:
        audit.critical_errors.append(f"raw_response_hash_nulls:{raw_nulls}")
    if raw_dups:
        audit.critical_errors.append("raw_response_hash_duplicates")
    if ledger_nulls:
        audit.critical_errors.append(f"ledger_content_hash_nulls:{ledger_nulls}")
    if ledger_dups:
        audit.critical_errors.append("ledger_content_hash_duplicates")


def _audit_fks(audit: MatchAudit, engine: Engine, match_id: int) -> None:
    """Verifica integridad referencial de ledger y timeline."""

    invalid_raw_refs = count_rows(
        engine,
        "events_ledger",
        "match_id = :match_id AND raw_api_response_id IS NOT NULL AND raw_api_response_id NOT IN (SELECT id FROM raw_api_responses)",
        {"match_id": match_id},
    )
    invalid_team_refs = count_rows(
        engine,
        "events_ledger",
        "match_id = :match_id AND team_id IS NOT NULL AND team_id NOT IN (SELECT id FROM teams)",
        {"match_id": match_id},
    )
    timeline_null_team = count_rows(engine, "events_timeline", "match_id = :match_id AND team_id IS NULL", {"match_id": match_id})
    invalid_timeline_team = count_rows(
        engine,
        "events_timeline",
        "match_id = :match_id AND team_id IS NOT NULL AND team_id NOT IN (SELECT id FROM teams)",
        {"match_id": match_id},
    )
    invalid_event_ledger = count_rows(
        engine,
        "events_timeline",
        "match_id = :match_id AND event_ledger_id IS NOT NULL AND event_ledger_id NOT IN (SELECT id FROM events_ledger)",
        {"match_id": match_id},
    )
    duplicate_event_ledger = fetch_all(
        engine,
        """
        SELECT event_ledger_id, COUNT(*) AS n
        FROM events_timeline
        WHERE match_id = :match_id AND event_ledger_id IS NOT NULL
        GROUP BY event_ledger_id
        HAVING COUNT(*) > 1
        """,
        {"match_id": match_id},
    )
    audit.data["integrity"] = {
        "invalid_raw_refs": invalid_raw_refs,
        "invalid_team_refs": invalid_team_refs,
        "timeline_null_team": timeline_null_team,
        "invalid_timeline_team": invalid_timeline_team,
        "invalid_event_ledger": invalid_event_ledger,
        "duplicate_event_ledger": duplicate_event_ledger,
    }
    if invalid_raw_refs:
        audit.critical_errors.append(f"invalid_raw_api_response_refs:{invalid_raw_refs}")
    if invalid_team_refs:
        audit.critical_errors.append(f"invalid_events_ledger_team_refs:{invalid_team_refs}")
    if timeline_null_team:
        audit.critical_errors.append(f"timeline_null_team_id:{timeline_null_team}")
    if invalid_timeline_team:
        audit.critical_errors.append(f"invalid_timeline_team_refs:{invalid_timeline_team}")
    if invalid_event_ledger:
        audit.critical_errors.append(f"invalid_timeline_event_ledger_refs:{invalid_event_ledger}")
    if duplicate_event_ledger:
        audit.critical_errors.append("duplicate_timeline_event_ledger_refs")
    ledger_null_team = count_rows(engine, "events_ledger", "match_id = :match_id AND team_id IS NULL", {"match_id": match_id})
    if ledger_null_team:
        audit.warnings.append(f"ledger_team_id_nulls={ledger_null_team}")


def _audit_statistics(audit: MatchAudit, engine: Engine, match_id: int, version: str, run: dict[str, Any]) -> None:
    """Verifica las filas de match_statistics para la versión solicitada."""

    rows = fetch_all(
        engine,
        """
        SELECT id, match_id, team_id, source, reconciliation_version,
               source_confidence, reconciliation_confidence,
               needs_review, reconciliation_status, has_conflict,
               confidence, primary_source, fallback_source,
               conflict_details, source_event_id, espn_summary, derived_play_by_play
        FROM match_statistics
        WHERE match_id = :match_id AND reconciliation_version = :version
        ORDER BY team_id, source
        """,
        {"match_id": match_id, "version": version},
    )
    audit.data["statistics"] = {
        "rows": rows,
        "count": len(rows),
        "status_counts": _count_by(rows, "reconciliation_status"),
        "needs_review_count": sum(1 for row in rows if row.get("needs_review") is True),
        "accepted_count": sum(1 for row in rows if row.get("reconciliation_status") == "accepted"),
        "rejected_count": sum(1 for row in rows if row.get("reconciliation_status") == "rejected"),
        "has_conflict_count": sum(1 for row in rows if row.get("has_conflict") is True),
    }
    if len(rows) != 2:
        audit.critical_errors.append(f"stats_rows_not_equal_2 actual={len(rows)}")
    if any(row.get("source") != "espn_summary" for row in rows):
        audit.critical_errors.append("stats_source_not_espn_summary")
    if any(row.get("reconciliation_status") != "accepted" for row in rows):
        audit.critical_errors.append("stats_status_not_accepted")
    if any(row.get("needs_review") is True for row in rows):
        audit.critical_errors.append("stats_needs_review_true")
    if any(row.get("confidence") is None or not (0 <= float(row.get("confidence")) <= 1) for row in rows):
        audit.critical_errors.append("stats_confidence_out_of_range")
    if any(row.get("source_confidence") is None or not (0 <= float(row.get("source_confidence")) <= 1) for row in rows):
        audit.critical_errors.append("stats_source_confidence_out_of_range")
    if any(row.get("reconciliation_confidence") is None or not (0 <= float(row.get("reconciliation_confidence")) <= 1) for row in rows):
        audit.critical_errors.append("stats_reconciliation_confidence_out_of_range")
    if any(row.get("has_conflict") is not True for row in rows):
        audit.warnings.append("stats_has_conflict_not_true_for_all_rows")
    if any(not row.get("conflict_details") for row in rows):
        audit.critical_errors.append("stats_conflict_details_missing")
    if any(int(row.get("source_event_id") or 0) != int(run["espn_event_id"]) for row in rows):
        audit.critical_errors.append("stats_source_event_id_mismatch")


def _count_by(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    """Cuenta ocurrencias de un campo textual."""

    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field))
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_summary(
    audits: list[MatchAudit],
    match_id_from: int,
    match_id_to: int,
    reconciliation_version: str,
) -> dict[str, Any]:
    """Agrega el resumen final de la auditoría."""

    critical_errors = [error for audit in audits for error in audit.critical_errors]
    warnings = [warning for audit in audits for warning in audit.warnings]
    return {
        "range": {
            "match_id_from": match_id_from,
            "match_id_to": match_id_to,
            "reconciliation_version": reconciliation_version,
        },
        "summary": {
            "matches_total": len(audits),
            "matches_passed": sum(1 for audit in audits if audit.passed()),
            "matches_failed": sum(1 for audit in audits if not audit.passed()),
            "critical_error_count": len(critical_errors),
            "warning_count": len(warnings),
        },
        "critical_errors": critical_errors,
        "warnings": warnings,
        "matches": [audit.data | {"match_id": audit.match_id, "critical_errors": audit.critical_errors, "warnings": audit.warnings} for audit in audits],
    }


def audit_range(engine: Engine, match_id_from: int, match_id_to: int, reconciliation_version: str) -> dict[str, Any]:
    """Audita un rango de partidos de forma read-only."""

    audits = [audit_match(engine, match_id, reconciliation_version) for match_id in range(match_id_from, match_id_to + 1)]
    return build_summary(audits, match_id_from, match_id_to, reconciliation_version)


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser CLI."""

    parser = argparse.ArgumentParser(description="Auditor read-only de backfill por rango")
    parser.add_argument("--match-id-from", type=int, required=True)
    parser.add_argument("--match-id-to", type=int, required=True)
    parser.add_argument("--reconciliation-version", type=str, required=True)
    parser.add_argument("--json", action="store_true", help="Emite JSON en stdout.")
    return parser


def main() -> int:
    """Punto de entrada CLI."""

    parser = build_parser()
    args = parser.parse_args()
    if args.match_id_from > args.match_id_to:
        print(json.dumps({"error": "match-id-from must be <= match-id-to"}, ensure_ascii=False, indent=2))
        return 2
    try:
        engine = build_engine()
        result = audit_range(engine, args.match_id_from, args.match_id_to, args.reconciliation_version)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["summary"]["matches_failed"] == 0 else 1
    except (SQLAlchemyError, RuntimeError, ValueError) as exc:
        LOGGER.error("Fallo al auditar rango: %s", exc, exc_info=True)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-14
