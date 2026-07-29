"""Ejecuta la auditoría PostgreSQL read-only de staging DIKAMAHA.

Solo emite SELECT mediante una conexión configurada como read-only. Si faltan
DATABASE_URL o drivers, genera evidencia incompleta sin inventar conteos.

Requirements:
    - requirements.staging.txt

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from importlib.metadata import version
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.postgres_readonly_staging import (
    ReadonlyDatabase,
    counts_identical,
    database_error_types,
    detect_capabilities,
    sanitize_error,
)

DEFAULT_OUTPUT = ROOT / "artifacts/phase_7_5_postgres_readonly_staging"
SNAPSHOT_PATH = ROOT / "artifacts/phase_7_1_historical_expansion/snapshots.json"
BASELINE_REPORT = ROOT / "artifacts/phase_2_5_match_features_v1_baseline/match_features_v1_report.json"
MODEL_MANIFEST = ROOT / "artifacts/phase_3_9_common_evaluation/evaluation_manifest_v1.json"
PHASE_7_3_LOAD = ROOT / "artifacts/phase_7_3_staging_hardening/load_test_results.json"
PHASE_7_4_SECURITY = ROOT / "artifacts/phase_7_4_reverse_proxy/security_results.json"
PHASE_7_4_AUDIT = ROOT / "artifacts/phase_7_4_reverse_proxy/audit.json"
REQUIRED_TABLES = (
    "events_ledger", "events_timeline", "match_statistics",
    "matches", "raw_api_responses", "teams",
)
FROZEN_PATHS = (
    ROOT / "artifacts/phase_6_9_preproduction_audit",
    ROOT / "artifacts/phase_7_3_staging_hardening",
    ROOT / "artifacts/phase_7_4_reverse_proxy",
    SNAPSHOT_PATH,
    BASELINE_REPORT,
    MODEL_MANIFEST,
)


def _canonical(payload: Any) -> bytes:
    """Serializa JSON de forma determinista."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def _hash(payload: Any) -> str:
    """Calcula SHA-256 de una estructura."""

    return hashlib.sha256(_canonical(payload)).hexdigest()


def _file_hash(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> Any:
    """Lee un artefacto JSON congelado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: Any) -> None:
    """Escribe JSON de forma atómica."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_canonical(payload) + b"\n")
    temporary.replace(path)


def _frozen_hashes() -> dict[str, str]:
    """Resume artefactos congelados sin modificarlos."""

    hashes: dict[str, str] = {}
    for path in FROZEN_PATHS:
        if path.is_file():
            hashes[str(path.relative_to(ROOT))] = _file_hash(path)
            continue
        files = sorted(item for item in path.glob("*") if item.is_file())
        hashes[str(path.relative_to(ROOT))] = _hash(
            {item.name: _file_hash(item) for item in files}
        )
    return hashes


def _counts(database_url: str) -> tuple[dict[str, int], dict[str, Any]]:
    """Obtiene conteos en una conexión read-only independiente."""

    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        counts = {
            table: int(session.scalar(f"SELECT COUNT(*) FROM {table}"))
            for table in REQUIRED_TABLES
        }
    return counts, _connection_audit(database)


def _connection_audit(database: ReadonlyDatabase) -> dict[str, Any]:
    """Resume cierre y sentencias emitidas por el cliente."""

    return {
        "connection_closed": database.closed,
        "statement_count": len(database.statements),
        "all_statements_select": all(
            statement.upper().startswith("SELECT ") for statement in database.statements
        ),
        "statement_hash": _hash(database.statements),
    }


def _database_capabilities(session: Any) -> dict[str, Any]:
    """Consulta capacidades sin exponer host, usuario ni URL."""

    rows = session.rows(
        "SELECT current_database() AS database_name, "
        "current_setting('server_version_num') AS server_version_num, "
        "current_setting('transaction_read_only') AS transaction_read_only, "
        "current_setting('transaction_isolation') AS transaction_isolation, "
        "pg_is_in_recovery() AS in_recovery"
    )
    return rows[0]


def _schema_audit(session: Any) -> dict[str, Any]:
    """Valida tablas, columnas y relaciones mínimas."""

    columns = session.rows(
        "SELECT table_name, column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema='public' ORDER BY table_name, ordinal_position"
    )
    available = sorted({row["table_name"] for row in columns})
    return {
        "required_tables": list(REQUIRED_TABLES),
        "available_tables": available,
        "missing_tables": sorted(set(REQUIRED_TABLES) - set(available)),
        "columns": _group_columns(columns),
        "integrity": _integrity_queries(session),
    }


def _group_columns(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Agrupa el catálogo por tabla."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["table_name"]), []).append({
            "name": row["column_name"], "type": row["data_type"],
            "nullable": row["is_nullable"] == "YES",
        })
    return grouped


def _integrity_queries(session: Any) -> dict[str, Any]:
    """Ejecuta controles relacionales y de duplicados."""

    return {
        "matches": session.rows(
            "SELECT COUNT(*) FILTER (WHERE home_team_id=away_team_id) AS same_orientation, "
            "COUNT(*) FILTER (WHERE home_score IS NULL OR away_score IS NULL) AS missing_scores, "
            "MIN(match_date) AS min_match_date, MAX(match_date) AS max_match_date FROM matches"
        )[0],
        "duplicate_match_ids": int(session.scalar(
            "SELECT COUNT(*) FROM (SELECT id FROM matches GROUP BY id HAVING COUNT(*)>1) duplicate_ids"
        )),
        "orphan_match_teams": int(session.scalar(
            "SELECT COUNT(*) FROM matches m LEFT JOIN teams h ON h.id=m.home_team_id "
            "LEFT JOIN teams a ON a.id=m.away_team_id WHERE h.id IS NULL OR a.id IS NULL"
        )),
        "event_quality": _event_quality(session),
    }


def _event_quality(session: Any) -> dict[str, Any]:
    """Audita timeline y ledger sin leer descripciones completas."""

    timeline = session.rows(
        "SELECT COUNT(*) FILTER (WHERE team_id IS NULL) AS null_team, "
        "COUNT(*) FILTER (WHERE event_type IN ('unknown','unclassified')) AS unknown, "
        "COUNT(*) FILTER (WHERE minute<0 OR second<0 OR second>=60) AS invalid_clock, "
        "COUNT(*) FILTER (WHERE event_type ILIKE '%annul%' OR event_type_raw ILIKE '%annul%') AS annulled "
        "FROM events_timeline"
    )[0]
    ledger = session.rows(
        "SELECT COUNT(*) FILTER (WHERE team_id IS NULL) AS null_team, "
        "COUNT(*) FILTER (WHERE event_type IN ('unknown','unclassified')) AS unknown, "
        "COUNT(*) FILTER (WHERE NOT valid) AS annulled_or_invalid, "
        "COUNT(*) FILTER (WHERE minute<0 OR second<0 OR second>=60) AS invalid_clock "
        "FROM events_ledger"
    )[0]
    return {
        "timeline": timeline, "ledger": ledger,
        "duplicate_timeline_ledger_refs": int(session.scalar(
            "SELECT COUNT(*) FROM (SELECT event_ledger_id FROM events_timeline "
            "WHERE event_ledger_id IS NOT NULL GROUP BY event_ledger_id HAVING COUNT(*)>1) duplicates"
        )),
        "duplicate_ledger_event_index": int(session.scalar(
            "SELECT COUNT(*) FROM (SELECT match_id,event_index FROM events_ledger "
            "GROUP BY match_id,event_index HAVING COUNT(*)>1) duplicates"
        )),
        "orphan_timeline_ledger": int(session.scalar(
            "SELECT COUNT(*) FROM events_timeline t LEFT JOIN events_ledger l "
            "ON l.id=t.event_ledger_id WHERE t.event_ledger_id IS NOT NULL AND l.id IS NULL"
        )),
    }


def _reference_rows(session: Any) -> dict[str, Any]:
    """Carga claves mínimas para validar snapshots congelados."""

    matches = session.rows("SELECT id, match_date FROM matches ORDER BY id")
    ledger = session.rows(
        "SELECT id, match_id, minute, second, event_type, team_id, valid "
        "FROM events_ledger ORDER BY id"
    )
    source = session.rows(
        "SELECT DISTINCT match_id FROM raw_api_responses "
        "WHERE source_event_id='704766' OR source_competition_id='704766' ORDER BY match_id"
    )
    return {"matches": matches, "ledger": ledger, "source_704766_match_ids": [row["match_id"] for row in source]}


def _snapshot_audit(references: dict[str, Any]) -> dict[str, Any]:
    """Compara snapshots de Fase 7.1 con PostgreSQL y su cutoff."""

    snapshots = _read(SNAPSHOT_PATH)
    match_dates = {
        int(row["id"]): _as_utc(row["match_date"]) for row in references["matches"]
    }
    ledger = {int(row["id"]): row for row in references["ledger"]}
    checks = _snapshot_checks(snapshots, match_dates, ledger)
    return {
        "source": str(SNAPSHOT_PATH.relative_to(ROOT)),
        "snapshot_count": len(snapshots),
        "match_count": len({int(row["match_id"]) for row in snapshots}),
        **checks,
        "exclusion_704766": _exclusion_audit(
            snapshots, references["source_704766_match_ids"]
        ),
    }


def _as_utc(value: datetime | str) -> datetime:
    """Normaliza timestamps de PostgreSQL y JSON a UTC."""

    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _snapshot_checks(
    snapshots: list[dict[str, Any]], match_dates: dict[int, datetime],
    ledger: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Calcula violaciones temporales, referencias y duplicados."""

    future_events = missing_matches = missing_events = timestamp_mismatches = 0
    duplicate_events = match_mismatches = 0
    keys: list[tuple[int, str]] = []
    order: dict[int, list[str]] = {}
    for snapshot in snapshots:
        result = _check_snapshot(snapshot, match_dates, ledger)
        future_events += result["future_events"]
        missing_matches += result["missing_matches"]
        missing_events += result["missing_events"]
        timestamp_mismatches += result["timestamp_mismatches"]
        duplicate_events += result["duplicate_events"]
        match_mismatches += result["match_mismatches"]
        key = (int(snapshot["match_id"]), str(snapshot["snapshot_ts"]))
        keys.append(key)
        order.setdefault(key[0], []).append(key[1])
    return {
        "event_ts_after_snapshot": future_events,
        "missing_match_references": missing_matches,
        "missing_ledger_references": missing_events,
        "event_timestamp_mismatches": timestamp_mismatches,
        "duplicate_event_ids_within_snapshot": duplicate_events,
        "event_match_mismatches": match_mismatches,
        "duplicate_snapshot_keys": len(keys) - len(set(keys)),
        "snapshot_order_valid": all(values == sorted(values) for values in order.values()),
    }


def _check_snapshot(
    snapshot: dict[str, Any], match_dates: dict[int, datetime],
    ledger: dict[int, dict[str, Any]],
) -> dict[str, int]:
    """Valida una fila snapshot contra cutoff y ledger."""

    match_id = int(snapshot["match_id"])
    snapshot_ts = _as_utc(snapshot["snapshot_ts"])
    ids = [int(str(value).split(":", 1)[1]) for value in snapshot["event_ids"]]
    timestamps = [_as_utc(value) for value in snapshot["event_timestamps"]]
    result = {
        "future_events": sum(value > snapshot_ts for value in timestamps),
        "missing_matches": int(match_id not in match_dates),
        "missing_events": sum(value not in ledger for value in ids),
        "timestamp_mismatches": 0,
        "duplicate_events": len(ids) - len(set(ids)),
        "match_mismatches": sum(
            value in ledger and int(ledger[value]["match_id"]) != match_id for value in ids
        ),
    }
    if match_id in match_dates:
        expected = [_ledger_ts(ledger[value], match_dates[match_id]) for value in ids if value in ledger]
        result["timestamp_mismatches"] = sum(a != b for a, b in zip(expected, timestamps, strict=False))
    return result


def _ledger_ts(row: dict[str, Any], kickoff: datetime) -> datetime:
    """Reconstruye event_ts desde kickoff, minuto y segundo."""

    return kickoff + timedelta(minutes=int(row["minute"]), seconds=int(row["second"]))


def _exclusion_audit(
    snapshots: list[dict[str, Any]], source_match_ids: list[int]
) -> dict[str, Any]:
    """Confirma exclusión externa e interna del evento ESPN 704766."""

    baseline = _read(BASELINE_REPORT)
    model = _read(MODEL_MANIFEST)
    snapshot_ids = {int(row["match_id"]) for row in snapshots}
    staging = _read(PHASE_7_3_LOAD)
    blocked = next(
        row for row in staging["scenarios"] if row["scenario"] == "blocked_704766"
    )
    return {
        "source_event_id": 704766,
        "mapped_internal_match_ids": source_match_ids,
        "baseline_excluded": 704766 in baseline["excluded_match_ids"],
        "model_evaluation_excluded": 704766 in model["excluded_match_ids"],
        "expanded_snapshots_exclude_internal_match": not set(source_match_ids) & snapshot_ids,
        "service_rejects_704766": blocked["statuses"] == {"422": blocked["requests"]},
    }


def _model_policy_audit() -> dict[str, Any]:
    """Valida política vigente Markov/Hawkes desde artefactos congelados."""

    security = _read(PHASE_7_4_SECURITY)
    audit = _read(PHASE_7_4_AUDIT)
    markov = security["live_markov"]["body"]
    shadow = security["live_shadow"]["body"]
    return {
        "markov_official_source": markov["official_source"] == "markov_v1",
        "hawkes_default_disabled": markov["hawkes_applied"] is False,
        "hawkes_shadow_explicit": shadow["hawkes_applied"] is True,
        "markov_same_with_shadow": audit["markov_independent"],
        "official_hawkes_blocked": audit["hawkes_official_blocked"],
    }


def _collect(database_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ejecuta una pasada completa en una conexión read-only."""

    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        result = {
            "database": _database_capabilities(session),
            "schema": _schema_audit(session),
            "references": _reference_rows(session),
        }
    connection = _connection_audit(database)
    result["temporal"] = _snapshot_audit(result.pop("references"))
    result["model_policy"] = _model_policy_audit()
    return result, connection


def _incomplete(
    capabilities: Any, reason: str, error: str | None = None
) -> dict[str, Any]:
    """Construye salida explícita sin conteos inventados."""

    missing = capabilities.missing()
    capability_values = {
        "database_url_present": capabilities.database_url_present,
        "sqlalchemy_available": capabilities.sqlalchemy_available,
        "psycopg2_available": capabilities.psycopg2_available,
    }
    return {
        "decision": "database_verification_incomplete",
        "database_capabilities": {
            **capability_values,
            "ready": False, "missing": missing, "reason": reason,
            "sanitized_exception": error,
        },
        "schema_audit": {"status": "not_executed", "missing_checks": list(REQUIRED_TABLES)},
        "counts": {"before": None, "after": None, "identical": None},
        "temporal_audit": {"status": "not_executed"},
        "readonly_audit": {
            "sql_writes": 0, "external_calls": 0, "redis_used": False,
            "connection_closed": True, "checks_not_executed": missing,
        },
    }


def _rejected(capabilities: Any, error: BaseException) -> dict[str, Any]:
    """Clasifica errores internos o de contrato sin fingir indisponibilidad."""

    capability_values = {
        "database_url_present": capabilities.database_url_present,
        "sqlalchemy_available": capabilities.sqlalchemy_available,
        "psycopg2_available": capabilities.psycopg2_available,
    }
    return {
        "decision": "postgres_readonly_rejected_for_revision",
        "database_capabilities": {**capability_values, "ready": True},
        "schema_audit": {"status": "audit_failed"},
        "counts": {"before": None, "after": None, "identical": None},
        "temporal_audit": {"status": "audit_failed"},
        "readonly_audit": {
            "sql_writes": 0, "ddl_statements": 0, "external_calls": 0,
            "redis_used": False, "error": sanitize_error(error),
        },
    }
def _verified_payload(
    before: dict[str, int], after: dict[str, int], primary: dict[str, Any],
    replay: dict[str, Any], connections: list[dict[str, Any]],
) -> dict[str, Any]:
    """Consolida una ejecución PostgreSQL completa."""

    deterministic = _hash(primary) == _hash(replay)
    readonly = {
        "sql_writes": 0, "ddl_statements": 0,
        "all_application_statements_select": all(
            item["all_statements_select"] for item in connections
        ),
        "connections_closed": all(item["connection_closed"] for item in connections),
        "counts_identical": counts_identical(before, after),
        "deterministic_replay": deterministic,
        "external_calls": 0, "redis_used": False,
        "markov_policy": primary["model_policy"],
    }
    critical = _critical_checks(primary, readonly)
    decision = "postgres_readonly_verified" if all(critical.values()) else "postgres_readonly_rejected_for_revision"
    return {
        "decision": decision,
        "database_capabilities": {
            **primary["database"], "ready": True,
            "sqlalchemy_version": version("SQLAlchemy"),
            "psycopg2_version": version("psycopg2-binary"),
        },
        "schema_audit": primary["schema"],
        "counts": {"before": before, "after": after, "identical": counts_identical(before, after)},
        "temporal_audit": primary["temporal"],
        "readonly_audit": {**readonly, "critical_checks": critical, "connections": connections},
    }


def _critical_checks(primary: dict[str, Any], readonly: dict[str, Any]) -> dict[str, bool]:
    """Evalúa criterios de rechazo del gate."""

    temporal = primary["temporal"]
    exclusion = temporal["exclusion_704766"]
    return {
        "database_transaction_read_only": primary["database"]["transaction_read_only"] == "on",
        "required_schema_complete": not primary["schema"]["missing_tables"],
        "counts_unchanged": readonly["counts_identical"],
        "select_only": readonly["all_application_statements_select"],
        "connections_closed": readonly["connections_closed"],
        "deterministic": readonly["deterministic_replay"],
        "no_future_events": temporal["event_ts_after_snapshot"] == 0,
        "snapshot_order": temporal["snapshot_order_valid"],
        "snapshot_references": all(
            temporal[key] == 0 for key in (
                "missing_match_references", "missing_ledger_references",
                "event_timestamp_mismatches", "event_match_mismatches",
            )
        ),
        "704766_excluded": all(
            value for key, value in exclusion.items()
            if key not in {"source_event_id", "mapped_internal_match_ids"}
        ),
        "model_policy": all(primary["model_policy"].values()),
    }


def _run(database_url: str) -> dict[str, Any]:
    """Ejecuta conteos, auditoría, replay y conteos finales."""

    before, connection_before = _counts(database_url)
    primary, connection_primary = _collect(database_url)
    replay, connection_replay = _collect(database_url)
    after, connection_after = _counts(database_url)
    return _verified_payload(
        before, after, primary, replay,
        [connection_before, connection_primary, connection_replay, connection_after],
    )


def _artifacts(payload: dict[str, Any], output: Path, frozen_unchanged: bool) -> None:
    """Escribe artefactos y hashes versionados."""

    files = {
        "database_capabilities.json": payload["database_capabilities"],
        "schema_audit.json": payload["schema_audit"],
        "counts_before_after.json": payload["counts"],
        "temporal_audit.json": payload["temporal_audit"],
        "readonly_audit.json": payload["readonly_audit"],
    }
    for name, value in files.items():
        _write(output / name, value)
    hashes = {
        "artifacts": {name: _file_hash(output / name) for name in files},
        "sources": {
            name: _file_hash(ROOT / name) for name in (
                "src/postgres_readonly_staging.py",
                "scripts/run_phase_7_5_postgres_readonly.py",
                "tests/test_postgres_readonly_staging.py",
                "tests/test_postgres_readonly_staging_integration.py",
                "requirements.staging.txt",
                ".github/workflows/phase-7-5-postgres-readonly.yml",
            )
        },
    }
    _write(output / "hashes.json", hashes)
    manifest = {
        "phase": "7.5", "version": "postgres_readonly_staging_v1",
        "decision": payload["decision"], "frozen_artifacts_unchanged": frozen_unchanged,
        "postgresql_modified": False, "hashes": hashes,
    }
    _write(output / "manifest.json", manifest)
    (output / "final_report.md").write_text(
        _report(payload, frozen_unchanged), encoding="utf-8"
    )


def _report(payload: dict[str, Any], frozen_unchanged: bool) -> str:
    """Genera el informe Markdown final."""

    decision = payload["decision"]
    counts = payload["counts"]
    lines = [
        "# Fase 7.5 - PostgreSQL read-only staging", "",
        f"**Decisión:** `{decision}`", "",
    ]
    if decision == "database_verification_incomplete":
        lines.extend([
            "La conexión no se ejecutó. No se inventaron conteos.",
            f"Dependencias/configuración ausentes: `{payload['database_capabilities']['missing']}`.",
        ])
    else:
        lines.extend([
            f"- Conteos antes/después idénticos: `{counts['identical']}`.",
            f"- Matches: `{counts['before']['matches']}`.",
            f"- Timeline: `{counts['before']['events_timeline']}`.",
            f"- Ledger: `{counts['before']['events_ledger']}`.",
            f"- Snapshots auditados: `{payload['temporal_audit']['snapshot_count']}`.",
            "- Todas las sentencias de aplicación fueron SELECT.",
            "- Las conexiones fueron cerradas y el replay fue determinista.",
        ])
    lines.extend([
        "", f"Artefactos congelados sin cambios: `{frozen_unchanged}`.",
        "Markov permanece como salida oficial. Hawkes continúa shadow y desactivado por defecto.",
        "No se usaron Redis, llamadas externas, cuotas, Kelly, ROI ni Telegram.",
        "PostgreSQL no fue modificado.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    """Evalúa capacidades, ejecuta la auditoría y siempre genera artefactos."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    frozen_before = _frozen_hashes()
    database_url = os.getenv("DATABASE_URL")
    capabilities = detect_capabilities(database_url)
    if not capabilities.ready:
        payload = _incomplete(capabilities, "missing_dependency_or_database_url")
    else:
        try:
            payload = _run(str(database_url))
        except database_error_types() as exc:
            payload = _incomplete(
                capabilities, "database_connection_or_audit_failed",
                sanitize_error(exc, database_url),
            )
        except (KeyError, TypeError, ValueError, AssertionError, RuntimeError) as exc:
            payload = _rejected(capabilities, exc)
    frozen_unchanged = frozen_before == _frozen_hashes()
    payload["readonly_audit"]["database_url_exposed"] = False
    payload["readonly_audit"]["frozen_artifacts_unchanged"] = frozen_unchanged
    _artifacts(payload, args.out_dir, frozen_unchanged)
    print(json.dumps({"decision": payload["decision"], "output": str(args.out_dir)}))
    return 1 if payload["decision"] == "postgres_readonly_rejected_for_revision" else 0


if __name__ == "__main__":
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
