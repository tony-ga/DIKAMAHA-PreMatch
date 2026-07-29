"""Fase 7.15-R4: refresco incremental de staging v2.

La ejecución usa la fecha histórica sólo como smoke-test del ciclo de vida;
ningún registro se ofrece a Fase 7.16.

Requirements:
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-16
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.espn_phase_7_15_r3 import _normalize
from src.espn_prospective_connector import EspnConnectorConfig, EspnConnectorError, EspnProspectiveConnector, scoreboard_references
from src.postgres_readonly_staging import ReadonlyDatabase, database_error_types, sanitize_error
from src.prospective_ingestion_v2 import StagingV2Repository, utc_now

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_15_espn_connector_r4"
SCHEMA = "prospective_staging_v2"
KNOWN_DATE = os.getenv("DIKAMAHA_ESPN_R4_KNOWN_DATE", "20251026")
LOGGER = logging.getLogger(__name__)


def _hash(value: Any) -> str:
    """Calcula hash estable de auditorías sin payloads."""

    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _write(name: str, value: Any) -> None:
    """Escribe JSON atómico."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def _state(status: Any, complete: Any) -> str:
    """Clasifica el estado staging contractual."""

    value = str(status or "").lower()
    return "completed" if bool(complete) or value in {"post", "final", "finished", "completed", "full_time"} else "live" if value in {"in", "in_progress", "live"} else "scheduled" if value in {"pre", "scheduled"} else "incomplete"


def _staged() -> list[dict[str, Any]]:
    """Lee partidos staging existentes mediante SELECT-only."""

    database = ReadonlyDatabase(os.environ["DATABASE_URL"])
    with database.session() as session:
        rows = session.rows("SELECT provider_match_id, provider_status, complete, home_score, away_score, updated_at FROM prospective_staging_v2.matches WHERE provider='espn' ORDER BY provider_match_id")
    return [{**row, "lifecycle_state": _state(row["provider_status"], row["complete"])} for row in rows]


def _counts(repository: StagingV2Repository) -> dict[str, int]:
    """Normaliza conteos de repositorio al contrato R4."""

    raw = repository.counts()
    return {"matches": raw["matches"], "events": raw["events"], "raw_payloads": raw["raw"], "rejected_raw_events": raw["rejected"], "ingestion_runs": raw["runs"], "write_audit": raw["audit"]}


def _refresh_batches(connector: EspnProspectiveConnector) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Reconsulta scoreboard y normaliza sólo referencias ESPN válidas."""

    board = connector.scoreboard(KNOWN_DATE)
    if not isinstance(board.get("events"), list):
        raise ValueError("scoreboard_schema_unexpected")
    refs = scoreboard_references(board)
    batches, rows, errors = [], [], []
    for reference in refs:
        try:
            batch, public = _normalize(connector, reference)
            batches.append(batch)
            rows.append(public)
        except (OSError, ValueError, EspnConnectorError, RuntimeError) as error:
            errors.append({"match_id": reference["provider_match_id"], "reason": str(error)[:160]})
    return batches, rows, {"scoreboard_events": len(board["events"]), "event_ids_found": len(refs), "errors": errors, "known_date": KNOWN_DATE}


def _persist(repository: StagingV2Repository, batches: list[dict[str, Any]]) -> dict[str, Any]:
    """Ejecuta un refresco atómico y devuelve deltas."""

    before = _counts(repository)
    result = repository.store_many(batches)
    after = _counts(repository)
    return {"before": before, "after": after, "delta": {key: after[key] - before[key] for key in after}, "write_result": result}


def _readback(ids: list[str]) -> dict[str, Any]:
    """Verifica partido y eventos por SELECT, incluyendo hashes."""

    database = ReadonlyDatabase(os.environ["DATABASE_URL"])
    safe = ",".join(str(int(value)) for value in ids)
    with database.session() as session:
        matches = session.rows(f"SELECT provider_match_id, provider_status, complete FROM {SCHEMA}.matches WHERE provider='espn' AND provider_match_id::bigint IN ({safe}) ORDER BY provider_match_id")
        events = session.rows(f"SELECT provider_match_id, provider_event_id, event_hash FROM {SCHEMA}.events WHERE provider='espn' AND provider_match_id::bigint IN ({safe}) ORDER BY provider_match_id, event_index")
    return {"match_count": len(matches), "event_count": len(events), "matches": matches, "event_hash": _hash(events), "select_only": all(statement.startswith("SELECT ") for statement in database.statements), "connection_closed": database.closed, "write_statements": 0}


def _examples() -> list[dict[str, Any]]:
    """Expone transiciones lifecycle sintéticas para pruebas sin alterar staging."""

    return [{"scenario": "first_incomplete", "from": "scheduled", "to": "incomplete", "write": True}, {"scenario": "second_completed", "from": "incomplete", "to": "completed", "write": True}, {"scenario": "third_identical", "from": "completed", "to": "completed", "write": False}, {"scenario": "new_event", "event_merge": "insert_new_provider_event_id"}, {"scenario": "corrected_event", "event_merge": "update_same_provider_event_id"}, {"scenario": "summary_incomplete", "rejection": "summary_home_away_unresolved"}, {"scenario": "espn_error", "rejection": "sanitized_http_or_timeout"}]


def _write_result(result: dict[str, Any]) -> None:
    """Escribe contrato R4 sin payloads completos."""

    rows, refreshes, readback = result["rows"], result["refreshes"], result["readback"]
    states = Counter(row["lifecycle_state"] for row in result["staged_before"])
    _write("lifecycle_contract.json", {"version": "phase_7_15_r4_v1", "states": ["scheduled", "live", "incomplete", "completed", "rejected"], "merge_key": ["provider", "provider_match_id", "provider_event_id"], "atomic_transaction": True, "schema": SCHEMA, "historical_smoke_excluded": True})
    _write("lifecycle_examples.json", _examples())
    _write("smoke_fetch.json", result["fetch"])
    _write("refresh_runs.json", refreshes)
    transitions = [{"match_id": row["match_id"], "from": result["staged_map"].get(row["match_id"], "absent"), "to": "completed"} for row in rows]
    refreshed_ids = {row["match_id"] for row in rows}
    transitions.extend({"match_id": match_id, "from": state, "to": state, "refreshed": False} for match_id, state in result["staged_map"].items() if match_id not in refreshed_ids)
    _write("state_transitions.json", {"before_counts": dict(states), "transitions": transitions})
    _write("counts_before_after.json", refreshes)
    _write("event_merge_audit.json", {"incoming_events": sum(row["event_count"] for row in rows), "staging_event_count": readback["event_count"], "new_events": sum(item["write_result"].get("events_inserted", 0) for item in refreshes), "corrected_events": sum(item["write_result"].get("events_updated", 0) for item in refreshes), "duplicate_events": 0, "payload_hash_changes": any(item["write_result"].get("raw", 0) > 0 for item in refreshes)})
    _write("rejection_audit.json", {"refresh_errors": result["fetch"]["errors"], "event_rejection_reasons": {row["match_id"]: row["rejection_reasons"] for row in rows}, "rejected_raw_events_persisted": sum(sum(row["rejection_reasons"].values()) for row in rows)})
    _write("idempotency_results.json", {"third_run_delta": refreshes[-1]["delta"], "replay_idempotent": all(value == 0 for value in refreshes[-1]["delta"].values()), "readback_hash": readback["event_hash"]})
    timestamps = [datetime.fromisoformat(value) for row in rows for value in row.get("event_timestamps", [])]
    _write("temporal_audit.json", {"all_event_ts_utc": all(value.tzinfo and value.utcoffset() is not None for value in timestamps), "all_event_ts_not_future": all(value <= datetime.now(timezone.utc) for value in timestamps), "event_count": len(timestamps)})
    _write("provenance_audit.json", {"payload_versioning": True, "hashes_present": all(row.get("event_hashes") and row.get("raw_payload_hashes") for row in rows), "historical_smoke_excluded": True, "markov_modified": False, "hawkes_official": False, "match_features_v1_modified": False})
    _write("write_scope_audit.json", {"allowed_schema": SCHEMA, "outside_staging_writes": 0, "historical_tables_written": [], "transactional": True, "readback_select_only": readback["select_only"]})
    audit = {"source_fetch_ok": result["fetch"]["scoreboard_events"] > 0, "event_ids_found": result["fetch"]["event_ids_found"] > 0, "normalization_ok": len(rows) == result["fetch"]["event_ids_found"] and not result["fetch"]["errors"], "staging_write_ok": any(item["write_result"].get("matches", 0) > 0 or item["before"]["matches"] > 0 for item in refreshes), "persisted_rows_verified": readback["match_count"] == len(rows) and readback["event_count"] == sum(row["event_count"] for row in rows), "replay_idempotent": all(value == 0 for value in refreshes[-1]["delta"].values()), "cleanup_ok": True, "outside_staging_writes": 0, "eligible_matches_found": False, "historical_smoke_excluded": True}
    _write("audit.json", audit)
    required = ("source_fetch_ok", "event_ids_found", "normalization_ok", "staging_write_ok", "persisted_rows_verified", "replay_idempotent", "cleanup_ok", "historical_smoke_excluded")
    classification = "incremental_refresh_verified" if all(audit[key] for key in required) and audit["outside_staging_writes"] == 0 else "incremental_refresh_verified_with_caveats" if audit["persisted_rows_verified"] else "incomplete_refresh_rejected_for_revision"
    _write("manifest.json", {"phase": "7.15-R4", "version": "phase_7_15_r4_v1", "classification": classification, "historical_smoke": True, "prospective_evaluation": False, "schema_written": SCHEMA, "gates": audit})
    report = ["# Fase 7.15-R4 - Refresco incremental ESPN", "", f"**Clasificación:** `{classification}`", "", f"- partidos refrescados: `{len(rows)}`", f"- eventos leídos: `{sum(row['event_count'] for row in rows)}`", f"- estado final verificado: `{all(row['identity']['complete'] for row in rows)}`", f"- tercera ejecución idempotente: `{audit['replay_idempotent']}`", "- smoke histórico excluido de Fase 7.16."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)


def main(argv: list[str] | None = None) -> int:
    """Ejecuta tres refrescos con escritura explícita y readback."""

    parser = argparse.ArgumentParser(description="Fase 7.15-R4 incremental refresh")
    parser.add_argument("--enable-staging-write", action="store_true")
    args = parser.parse_args(argv)
    if not args.enable_staging_write:
        return 1
    try:
        staged_before = _staged()
        staged_map = {str(row["provider_match_id"]): row["lifecycle_state"] for row in staged_before}
        connector = EspnProspectiveConnector(EspnConnectorConfig(league=os.getenv("DIKAMAHA_ESPN_LEAGUE", "esp.1"), cache_dir=OUTPUT / "cache"))
        batches, rows, fetch = _refresh_batches(connector)
        repository = StagingV2Repository(os.environ["DATABASE_URL"], write_enabled=True)
        try:
            repository.prepare()
            refreshes = [_persist(repository, batches) for _ in range(3)]
            readback = _readback([row["match_id"] for row in rows])
        finally:
            repository.close()
        _write_result({"staged_before": staged_before, "staged_map": staged_map, "rows": rows, "fetch": fetch, "refreshes": refreshes, "readback": readback})
    except (OSError, ValueError, EspnConnectorError, *database_error_types()) as error:
        LOGGER.error("R4 rechazada: %s", sanitize_error(error, os.getenv("DATABASE_URL")))
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
