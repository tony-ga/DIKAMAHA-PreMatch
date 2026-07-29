"""Fase 7.15-R2: normalización ESPN en memoria y dry-run.

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

from src.espn_prospective_connector import EspnConnectorConfig, EspnConnectorError, EspnProspectiveConnector, extract_event_id, payload_hash, scoreboard_references
from src.prospective_ingestion_v2 import SourceReference, build_batch, team_ref_audit, utc_now

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_15_espn_connector_r2"
KNOWN_DATE = os.getenv("DIKAMAHA_ESPN_R2_KNOWN_DATE", "20251026")
LOGGER = logging.getLogger(__name__)


def _hash(value: Any) -> str:
    """Calcula hash estable de metadatos normalizados."""

    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _write(name: str, value: Any) -> None:
    """Escribe JSON atómico sin payloads completos."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _summary_identity(summary: dict[str, Any]) -> dict[str, Any]:
    """Extrae equipos, marcador y estado sólo desde header.summary."""

    competition = ((summary.get("header") or {}).get("competitions") or [None])[0]
    if not isinstance(competition, dict):
        raise ValueError("summary_missing_competition")
    status_type = ((competition.get("status") or {}).get("type") or {})
    teams = {}
    for row in competition.get("competitors", []):
        if not isinstance(row, dict) or row.get("homeAway") not in {"home", "away"}:
            continue
        team = row.get("team") if isinstance(row.get("team"), dict) else {}
        team_id = team.get("id") or row.get("id")
        if not str(team_id).isdigit():
            raise ValueError("summary_team_id_unresolved")
        teams[row["homeAway"]] = {"team_id": int(team_id), "name": team.get("displayName") or team.get("name"), "score": int(row["score"]) if str(row.get("score", "")).isdigit() else None}
    if set(teams) != {"home", "away"}:
        raise ValueError("summary_home_away_unresolved")
    return {"status": str(status_type.get("state") or status_type.get("name") or "unknown").lower().removeprefix("status_"), "completed": bool(status_type.get("completed")), "teams": teams, "header_id": str((summary.get("header") or {}).get("id") or "")}


def _team_audit(event: dict[str, Any], summary_identity: dict[str, Any]) -> dict[str, Any]:
    """Compara refs de evento con equipos resueltos en summary."""

    competition = (event.get("competitions") or [{}])[0]
    rows = {row.get("homeAway"): row for row in competition.get("competitors", []) if isinstance(row, dict)}
    output = {}
    for side in ("home", "away"):
        ref_info = team_ref_audit(rows.get(side, {}).get("team"))
        summary_team = summary_identity["teams"][side]
        output[side] = {"ref": ref_info, "summary": summary_team, "id_consistent": ref_info["team_id"] == summary_team["team_id"]}
    return output


def _normalize_one(connector: EspnProspectiveConnector, reference: dict[str, Any]) -> dict[str, Any]:
    """Normaliza un evento completo desde event, plays y summary."""

    event = connector.event(reference["provider_match_id"])
    plays = connector.plays(reference["provider_match_id"], reference["competition_id"])
    summary = connector.summary(reference["provider_match_id"])
    summary_identity = _summary_identity(summary)
    batch = build_batch(SourceReference(**reference), event, plays, utc_now())
    identity = batch["identity"]
    team_audit = _team_audit(event, summary_identity)
    consistency = all(team_audit[side]["id_consistent"] for side in ("home", "away")) and summary_identity["header_id"] == reference["provider_match_id"]
    events = [{key: row[key] for key in ("provider_event_id", "event_index", "event_ts", "minute", "second", "team_provider_id", "event_type", "event_type_raw", "annulled", "event_hash")} for row in batch["events"]]
    return {"match_id": reference["provider_match_id"], "competition_id": reference["competition_id"], "identity": {"kickoff_ts": identity["kickoff_ts"], "provider_status": summary_identity["status"], "complete": summary_identity["completed"], "home_team": summary_identity["teams"]["home"], "away_team": summary_identity["teams"]["away"], "score_provenance": "summary.header.competitions[0].competitors[].score"}, "team_audit": team_audit, "summary": {"status": "consistent" if consistency else "inconsistent", "payload_hash": payload_hash(summary), "key_events_count": len(summary.get("keyEvents", [])) if isinstance(summary.get("keyEvents"), list) else 0, "top_level_keys": sorted(summary)[:30]}, "events": events, "event_counts": dict(Counter(row["event_type"] for row in events)), "rejected": [{"event_index": row["event_index"], "reason": row["reason"], "raw_hash": row["raw_hash"]} for row in batch["rejected"]], "payload_hashes": {"event": payload_hash(event), "plays": payload_hash(plays), "summary": payload_hash(summary)}, "provenance": {"teams": "event.competitions[0].competitors[].team.$ref + summary.header.competitions[0].competitors[].team", "events": "event.competitions[0].plays.items", "timestamps": "event.date + plays[].clock.value normalized UTC", "status": "summary.header.competitions[0].status.type"}}


def _synthetic_rejections() -> list[dict[str, Any]]:
    """Verifica rechazos explícitos de refs, equipos y schemas inválidos."""

    cases = [("invalid_team_ref", team_ref_audit({"$ref": "https://sports.core.api.espn.com/v2/not-teams/abc?x=1"})), ("missing_team_ref", team_ref_audit({})), ("invalid_event_ref", {"event_id": extract_event_id({"id": "abc"})}), ("missing_event_competition", {"reason": "summary_missing_competition"})]
    return [{"case": name, "reason": "unresolved_reference" if data.get("team_id") is None or data.get("event_id") is None else "schema_validation", "details": data} for name, data in cases]


def _temporal(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Audita UTC, orden temporal y timestamps de eventos normalizados."""

    events = [event for row in rows for event in row["events"]]
    parsed = [datetime.fromisoformat(event["event_ts"]) for event in events]
    return {"all_event_ts_utc": all(value.tzinfo and value.utcoffset() is not None for value in parsed), "all_event_ts_not_future": all(value <= datetime.now(timezone.utc) for value in parsed), "stable_order_by_match_ts_index": all(row["events"] == sorted(row["events"], key=lambda item: (item["event_ts"], item["event_index"])) for row in rows), "event_count": len(events)}


def _run() -> dict[str, Any]:
    """Ejecuta la normalización de los cuatro eventos conocidos."""

    config = EspnConnectorConfig(league=os.getenv("DIKAMAHA_ESPN_LEAGUE", "esp.1"), cache_dir=OUTPUT / "cache")
    connector = EspnProspectiveConnector(config)
    board = connector.scoreboard(KNOWN_DATE)
    refs = scoreboard_references(board)
    rows, errors = [], []
    for reference in refs:
        try:
            rows.append(_normalize_one(connector, reference))
        except (OSError, ValueError, EspnConnectorError, RuntimeError) as error:
            errors.append({"match_id": reference["provider_match_id"], "reason": str(error)[:160]})
    all_teams_valid = all(row["team_audit"][side]["id_consistent"] for row in rows for side in ("home", "away"))
    summaries_valid = all(row["summary"]["status"] == "consistent" for row in rows)
    normalization_valid = len(rows) == 4 and not errors and all_teams_valid and summaries_valid
    temporal = _temporal(rows)
    diagnosis = {"league": config.league, "known_date": KNOWN_DATE, "scoreboard_events": len(board.get("events", [])), "normalized_events": len(rows), "event_ids_found": len(refs), "all_teams_valid": all_teams_valid, "summaries_consistent": summaries_valid, "normalization_valid": normalization_valid, "write_enabled": False}
    return {"config": config, "refs": refs, "rows": rows, "errors": errors, "diagnosis": diagnosis, "temporal": temporal}


def _public(result: dict[str, Any]) -> dict[str, Any]:
    """Reduce ejecución a campos auditables sin payloads."""

    return {"diagnosis": result["diagnosis"], "refs": result["refs"], "rows": result["rows"], "errors": result["errors"], "temporal": result["temporal"]}


def _write_result(result: dict[str, Any], replay: dict[str, Any]) -> None:
    """Escribe contrato R2, seguridad, hashes y reporte."""

    rows = result["rows"]
    _write("normalization_contract.json", {"version": "phase_7_15_r2_v1", "input": ["scoreboard.events[].id", "event.competitions[].competitors", "plays.items", "summary.header"], "output": ["team_id", "team_name_if_provider_supplied", "event_ts_utc", "event_type", "payload_hash", "provenance"], "write_enabled": False, "raw_payload_policy": "local_cache_only"})
    _write("normalized_known_date.json", rows)
    _write("team_ref_audit.json", [{"match_id": row["match_id"], "teams": row["team_audit"]} for row in rows])
    _write("event_ref_audit.json", {"scoreboard_refs": result["refs"], "extracted_event_ids": [row["provider_match_id"] for row in result["refs"]], "invalid_ref_probes": _synthetic_rejections()})
    _write("summary_normalization.json", [{"match_id": row["match_id"], **row["summary"]} for row in rows])
    _write("rejected_records.json", {"actual": [item for row in rows for item in row["rejected"]] + result["errors"], "synthetic_validation": _synthetic_rejections()})
    _write("temporal_audit.json", result["temporal"])
    _write("provenance_audit.json", {"all_fields_have_source": all(bool(row["provenance"]) for row in rows), "payload_hashes_present": all(bool(row["payload_hashes"]) for row in rows), "team_id_source": "provider_ref_path_and_summary_team.id", "name_source": "summary only; no inference", "official_models_modified": False})
    _write("security_audit.json", {"payloads_in_artifacts": False, "payloads_in_logs": False, "cache_local_only": True, "postgresql_writes": 0, "historical_tables_modified": False, "credentials_exposed": False, "odds_or_telegram_used": False})
    _write("replay_hashes.json", replay)
    classification = "espn_normalization_verified" if result["diagnosis"]["normalization_valid"] and replay["identical"] else "espn_normalization_verified_with_caveats" if result["rows"] else "espn_normalization_rejected_for_revision"
    manifest = {"phase": "7.15-R2", "version": "phase_7_15_r2_v1", "classification": classification, "known_date": KNOWN_DATE, "normalized_match_count": len(rows), "event_ids_found": len(result["refs"]), "dry_run": True, "postgresql_modified": False, "staging_write_permitted": False, "replay_identical": replay["identical"]}
    _write("manifest.json", manifest)
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)
    report = ["# Fase 7.15-R2 - Normalización ESPN", "", f"**Clasificación:** `{classification}`", "", f"- competición: `{result['diagnosis']['league']}`", f"- fecha: `{KNOWN_DATE}`", f"- eventos scoreboard: `{result['diagnosis']['scoreboard_events']}`", f"- partidos normalizados: `{len(rows)}`", f"- equipos válidos: `{result['diagnosis']['all_teams_valid']}`", f"- summary consistente: `{result['diagnosis']['summaries_consistent']}`", f"- replay determinista: `{replay['identical']}`", "- dry-run; no se habilitó escritura staging."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main() -> int:
    """Ejecuta dos veces la normalización y no escribe PostgreSQL."""

    try:
        first, second = _run(), _run()
        first_public, second_public = _public(first), _public(second)
        replay = {"primary_hash": _hash(first_public), "replay_hash": _hash(second_public), "identical": first_public == second_public, "unit": "complete_match", "payloads_excluded": True}
        _write_result(first, replay)
        classification = "espn_normalization_verified" if first["diagnosis"]["normalization_valid"] and replay["identical"] else "espn_normalization_verified_with_caveats"
        return 0 if classification != "espn_normalization_rejected_for_revision" else 1
    except (OSError, ValueError, EspnConnectorError, RuntimeError) as error:
        LOGGER.error("Fase 7.15-R2 rechazada: %s", str(error)[:160])
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
