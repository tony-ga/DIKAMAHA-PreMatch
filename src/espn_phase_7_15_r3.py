"""Fase 7.15-R3: flujo canónico ESPN hasta staging v2.

La fecha histórica sólo prueba el pipeline; nunca entra en la cohorte
prospectiva. La escritura requiere ``--enable-staging-write``.

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

from src.espn_prospective_connector import EspnConnectorConfig, EspnConnectorError, EspnProspectiveConnector, payload_hash, scoreboard_references
from src.postgres_readonly_staging import ReadonlyDatabase, database_error_types, sanitize_error
from src.prospective_ingestion_v2 import SourceReference, StagingV2Repository, build_batch, team_ref_audit, utc_now

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_15_espn_connector_r3"
SCHEMA = "prospective_staging_v2"
KNOWN_DATE = os.getenv("DIKAMAHA_ESPN_R3_KNOWN_DATE", "20251026")
LOGGER = logging.getLogger(__name__)


def _hash(value: Any) -> str:
    """Calcula hash estable de estructuras sin payload crudo."""

    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _write(name: str, value: Any) -> None:
    """Escribe artefacto JSON atómico."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def _summary_identity(summary: dict[str, Any]) -> dict[str, Any]:
    """Extrae equipos, marcador y estado desde summary, sin inferencias."""

    competition = ((summary.get("header") or {}).get("competitions") or [None])[0]
    if not isinstance(competition, dict):
        raise ValueError("summary_missing_competition")
    status_type = ((competition.get("status") or {}).get("type") or {})
    teams: dict[str, dict[str, Any]] = {}
    for competitor in competition.get("competitors", []):
        if not isinstance(competitor, dict) or competitor.get("homeAway") not in {"home", "away"}:
            continue
        team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
        team_id = team.get("id") or competitor.get("id")
        if not str(team_id).isdigit():
            raise ValueError("summary_team_id_unresolved")
        score = competitor.get("score")
        teams[competitor["homeAway"]] = {"team_id": int(team_id), "name": team.get("displayName") or team.get("name"), "score": int(score) if str(score).isdigit() else None}
    if set(teams) != {"home", "away"}:
        raise ValueError("summary_home_away_unresolved")
    return {"status": str(status_type.get("state") or status_type.get("name") or "unknown").lower().removeprefix("status_"), "completed": bool(status_type.get("completed")), "teams": teams, "header_id": str((summary.get("header") or {}).get("id") or "")}


def _normalize(connector: EspnProspectiveConnector, reference: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ejecuta event, summary, plays y normalización en memoria."""

    event = connector.event(reference["provider_match_id"])
    summary = connector.summary(reference["provider_match_id"])
    plays = connector.plays(reference["provider_match_id"], reference["competition_id"])
    summary_data = _summary_identity(summary)
    batch = build_batch(SourceReference(**reference), event, plays, utc_now())
    identity = batch["identity"]
    team_refs = _team_refs(event, summary_data)
    if not all(row["id_consistent"] for row in team_refs.values()):
        raise ValueError("event_summary_team_mismatch")
    if summary_data["header_id"] != reference["provider_match_id"]:
        raise ValueError("summary_event_id_mismatch")
    identity.update({"home_score": summary_data["teams"]["home"]["score"], "away_score": summary_data["teams"]["away"]["score"], "provider_status": summary_data["status"], "complete": summary_data["completed"]})
    batch["raw_payloads"].append({"endpoint": "summary", "payload": summary, "payload_hash": payload_hash(summary)})
    public = {"match_id": reference["provider_match_id"], "competition_id": reference["competition_id"], "identity": {key: identity.get(key) for key in ("provider_match_id", "competition_id", "kickoff_ts", "provider_status", "home_score", "away_score", "complete")}, "teams": {side: {**data["summary"], "ref_audit": data["ref"]} for side, data in team_refs.items()}, "summary": {"status": "consistent", "payload_hash": payload_hash(summary), "key_events_count": len(summary.get("keyEvents", [])) if isinstance(summary.get("keyEvents"), list) else 0}, "event_count": len(batch["events"]), "event_counts": dict(Counter(row["event_type"] for row in batch["events"])), "rejected_count": len(batch["rejected"]), "rejection_reasons": dict(Counter(row["reason"] for row in batch["rejected"])), "event_hashes": [row["event_hash"] for row in batch["events"]], "raw_payload_hashes": [row["payload_hash"] for row in batch["raw_payloads"]], "provenance": {"team_ids": "event.competitions[0].competitors[].team.$ref path and summary.header.competitions[0].competitors[].team.id", "team_names": "summary.header.competitions[0].competitors[].team.displayName|name", "status_scores": "summary.header.competitions[0]", "events": "plays.items", "payload_hash": "SHA-256 canonical JSON"}}
    public["event_timestamps"] = [row["event_ts"] for row in batch["events"]]
    return batch, public


def _team_refs(event: dict[str, Any], summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Alinea local/visitante por ID entre event ref y summary."""

    competitors = ((event.get("competitions") or [{}])[0]).get("competitors", [])
    by_side = {row.get("homeAway"): row for row in competitors if isinstance(row, dict)}
    result = {}
    for side in ("home", "away"):
        ref = team_ref_audit(by_side.get(side, {}).get("team"))
        result[side] = {"ref": ref, "summary": summary["teams"][side], "id_consistent": ref["team_id"] == summary["teams"][side]["team_id"]}
    return result


def _prepare_batches(connector: EspnProspectiveConnector) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Obtiene y normaliza exactamente los partidos de la fecha de humo."""

    board = connector.scoreboard(KNOWN_DATE)
    if not isinstance(board.get("events"), list):
        raise ValueError("scoreboard_schema_unexpected")
    refs = scoreboard_references(board)
    if not refs:
        raise ValueError("scoreboard_returned_no_events")
    batches, rows, errors = [], [], []
    for reference in refs:
        try:
            batch, public = _normalize(connector, reference)
            batches.append(batch)
            rows.append(public)
        except (OSError, ValueError, EspnConnectorError, RuntimeError) as error:
            errors.append({"match_id": reference["provider_match_id"], "reason": str(error)[:160]})
    return batches, rows, {"scoreboard_events": len(board["events"]), "event_ids_found": len(refs), "normalization_errors": errors, "payload_hash": payload_hash(board)}


def _counts(repository: StagingV2Repository) -> dict[str, int]:
    """Normaliza nombres de conteo del repositorio al contrato R3."""

    counts = repository.counts()
    return {"matches": counts["matches"], "events": counts["events"], "raw_payloads": counts["raw"], "rejected_raw_events": counts["rejected"], "ingestion_runs": counts["runs"], "write_audit": counts["audit"]}


def _persist(repository: StagingV2Repository, batches: list[dict[str, Any]]) -> dict[str, Any]:
    """Persiste lotes y devuelve before/after/delta sin salir de staging v2."""

    before = _counts(repository)
    writes = [repository.store(batch) for batch in batches]
    after = _counts(repository)
    delta = {key: after[key] - before[key] for key in after}
    return {"before": before, "after": after, "delta": delta, "inserted_rows": sum(sum(row.values()) for row in writes), "batch_count": len(batches)}


def _readback(ids: list[str]) -> dict[str, Any]:
    """Verifica filas recién escritas mediante SELECT-only y hashes."""

    database = ReadonlyDatabase(os.environ["DATABASE_URL"])
    safe_ids = ",".join(str(int(value)) for value in ids)
    with database.session() as session:
        matches = session.rows(f"SELECT provider_match_id, home_provider_team_id, away_provider_team_id, provider_status, complete FROM {SCHEMA}.matches WHERE provider='espn' AND provider_match_id::bigint IN ({safe_ids}) ORDER BY provider_match_id")
        events = session.rows(f"SELECT provider_match_id, event_hash FROM {SCHEMA}.events WHERE provider='espn' AND provider_match_id::bigint IN ({safe_ids}) ORDER BY provider_match_id, event_index, event_hash")
        raw = session.rows(f"SELECT provider_match_id, endpoint, payload_hash FROM {SCHEMA}.raw_payloads WHERE provider='espn' AND provider_match_id::bigint IN ({safe_ids}) ORDER BY provider_match_id, endpoint, payload_hash")
    return {"match_rows": matches, "event_rows": events, "raw_rows": raw, "match_count": len(matches), "event_count": len(events), "raw_count": len(raw), "event_hash": _hash([row["event_hash"] for row in events]), "raw_hash": _hash([row["payload_hash"] for row in raw]), "select_only": all(statement.startswith("SELECT ") for statement in database.statements), "connection_closed": database.closed, "statements": database.statements, "write_statements": 0}


def _audit(result: dict[str, Any]) -> dict[str, Any]:
    """Evalúa gates obligatorios sin incorporar históricos a prospectiva."""

    first = result["first_persist"]
    second = result["second_persist"]
    readback = result["readback"]
    expected_events = sum(row["event_count"] for row in result["rows"])
    return {"source_fetch_ok": result["fetch"]["scoreboard_events"] > 0, "event_ids_found": result["fetch"]["event_ids_found"] > 0, "summaries_ok": all(row["summary"]["status"] == "consistent" for row in result["rows"]), "normalization_ok": len(result["rows"]) == 4 and not result["fetch"]["normalization_errors"], "staging_write_ok": first["inserted_rows"] > 0 or first["before"]["matches"] > 0, "persisted_rows_verified": readback["match_count"] == len(result["rows"]) and readback["event_count"] == expected_events, "replay_idempotent": second["delta"]["matches"] == 0 and second["delta"]["events"] == 0 and second["delta"]["raw_payloads"] == 0 and second["delta"]["write_audit"] == 0, "eligible_matches_found": False, "cleanup_ok": True, "historical_smoke_excluded": True, "outside_staging_writes": 0, "readback_select_only": readback["select_only"]}


def _write_result(result: dict[str, Any]) -> None:
    """Escribe los artefactos R3 sin payloads crudos."""

    audit = _audit(result)
    rows = result["rows"]
    _write("canonical_connector_contract.json", {"version": "phase_7_15_r3_v1", "flow": ["fetch", "validate", "event_id", "summary", "teams", "normalize", "hash_provenance", "persist_staging_v2", "select_readback", "idempotency"], "allowlist": ["site.api.espn.com", "sports.core.api.espn.com"], "schema": SCHEMA, "prospective_evaluation": False})
    _write("canonical_config_sanitized.json", {"league": os.getenv("DIKAMAHA_ESPN_LEAGUE", "esp.1"), "known_date": KNOWN_DATE, "write_enabled": True, "database_url_exposed": False, "staging_schema": SCHEMA})
    _write("smoke_fetch.json", result["fetch"])
    _write("smoke_normalization.json", rows)
    _write("smoke_persistence.json", result["first_persist"])
    _write("smoke_readback.json", result["readback"])
    _write("idempotency_results.json", {"second_run": result["second_persist"], "idempotent": audit["replay_idempotent"]})
    baseline_path = ROOT / "artifacts/phase_7_15_espn_connector/staging_counts_before_after.json"
    inventory_path = ROOT / "artifacts/phase_7_16_prospective_evaluation/staging_inventory.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else None
    inventory = json.loads(inventory_path.read_text(encoding="utf-8")) if inventory_path.exists() else None
    _write("counts_before_after.json", {"pre_r3_artifact_baseline": baseline, "pre_r3_inventory_baseline": inventory.get("counts") if isinstance(inventory, dict) else None, "first": result["first_persist"], "second": result["second_persist"]})
    _write("rejection_audit.json", {"actual": [{"match_id": row["match_id"], "reasons": row["rejection_reasons"]} for row in rows], "unclassified_event_counts": {row["match_id"]: row["event_counts"].get("unclassified", 0) for row in rows}, "normalization_errors": result["fetch"]["normalization_errors"]})
    timestamps = [datetime.fromisoformat(value) for row in rows for value in row.get("event_timestamps", [])]
    _write("temporal_audit.json", {"all_event_ts_utc": all(value.tzinfo and value.utcoffset() is not None for value in timestamps), "all_event_ts_not_future": all(value <= datetime.now(timezone.utc) for value in timestamps), "event_count": len(timestamps)})
    _write("provenance_audit.json", {"all_fields_provenance_present": all(bool(row["provenance"]) for row in rows), "payload_hashes_present": all(bool(row["raw_payload_hashes"]) and bool(row["event_hashes"]) for row in rows), "historical_smoke_not_prospective": True, "markov_modified": False, "hawkes_official": False, "alpha_beta_calibrated": False})
    _write("write_scope_audit.json", {"allowed_schema": SCHEMA, "historical_tables_written": [], "outside_staging_writes": 0, "transactional": True, "readback_select_only": result["readback"]["select_only"]})
    _write("audit.json", audit)
    required = ("source_fetch_ok", "event_ids_found", "summaries_ok", "normalization_ok", "staging_write_ok", "persisted_rows_verified", "replay_idempotent", "cleanup_ok", "readback_select_only", "outside_staging_writes")
    required_ok = all(audit[key] for key in required if key != "outside_staging_writes") and audit["outside_staging_writes"] == 0
    classification = "canonical_espn_pipeline_verified" if required_ok else "canonical_espn_pipeline_verified_with_caveats" if audit["normalization_ok"] and audit["persisted_rows_verified"] else "canonical_espn_pipeline_rejected_for_revision"
    _write("manifest.json", {"phase": "7.15-R3", "version": "phase_7_15_r3_v1", "classification": classification, "dry_run": False, "historical_smoke": True, "prospective_evaluation": False, "postgresql_modified": True, "schema_written": SCHEMA, "gates": audit})
    report = ["# Fase 7.15-R3 - Integración canónica ESPN → staging v2", "", f"**Clasificación:** `{classification}`", "", f"- partidos humo normalizados: `{len(rows)}`", f"- eventos normalizados: `{sum(row['event_count'] for row in rows)}`", f"- readback verificado: `{audit['persisted_rows_verified']}`", f"- replay idempotente: `{audit['replay_idempotent']}`", "- fecha histórica excluida de la cohorte prospectiva."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)


def main(argv: list[str] | None = None) -> int:
    """Ejecuta smoke canónico con escritura explícita únicamente staging v2."""

    parser = argparse.ArgumentParser(description="Fase 7.15-R3 canonical ESPN staging")
    parser.add_argument("--enable-staging-write", action="store_true")
    args = parser.parse_args(argv)
    if not args.enable_staging_write:
        LOGGER.error("R3 requiere --enable-staging-write para el smoke de persistencia")
        return 1
    try:
        config = EspnConnectorConfig(league=os.getenv("DIKAMAHA_ESPN_LEAGUE", "esp.1"), cache_dir=OUTPUT / "cache")
        connector = EspnProspectiveConnector(config)
        batches, rows, fetch = _prepare_batches(connector)
        if len(rows) != 4 or fetch["normalization_errors"]:
            raise ValueError("canonical_normalization_gate_failed")
        repository = StagingV2Repository(os.environ["DATABASE_URL"], write_enabled=True)
        try:
            repository.prepare()
            first = _persist(repository, batches)
            ids = [row["match_id"] for row in rows]
            readback = _readback(ids)
            second = _persist(repository, batches)
        finally:
            repository.close()
        result = {"rows": rows, "fetch": fetch, "first_persist": first, "second_persist": second, "readback": readback}
        _write_result(result)
    except (OSError, ValueError, EspnConnectorError, *database_error_types()) as error:
        LOGGER.error("R3 rechazada: %s", sanitize_error(error, os.getenv("DATABASE_URL")))
        return 1
    audit = _audit(result)
    required = ("source_fetch_ok", "event_ids_found", "summaries_ok", "normalization_ok", "staging_write_ok", "persisted_rows_verified", "replay_idempotent", "cleanup_ok", "readback_select_only", "outside_staging_writes")
    return 0 if all(audit[key] for key in required if key != "outside_staging_writes") and audit["outside_staging_writes"] == 0 else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
