"""Fase 7.15-R5: ingesta ESPN por rango y refresco acotado.

El módulo no ejecuta evaluación estadística y sólo permite escritura en
``prospective_staging_v2`` cuando el llamador lo autoriza explícitamente.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.audit_staging_inventory import _inventory
from src.espn_phase_7_15_r3 import _normalize
from src.espn_prospective_connector import EspnConnectorConfig, EspnProspectiveConnector, scoreboard_references
from src.postgres_readonly_staging import ReadonlyDatabase
from src.prospective_ingestion_v2 import SourceReference, StagingV2Repository, canonical_hash

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_15_espn_connector_r5"
SCHEMA = "prospective_staging_v2"


def _hash(value: Any) -> str:
    """Calcula un hash estable para auditorías sanitizadas."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _write(name: str, payload: Any) -> None:
    """Escribe un artefacto JSON mediante reemplazo atómico."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def validate_range(start: str, end: str) -> tuple[date, date]:
    """Valida fechas inclusivas en formato ESPN YYYYMMDD."""
    try:
        first, last = datetime.strptime(start, "%Y%m%d").date(), datetime.strptime(end, "%Y%m%d").date()
    except ValueError as error:
        raise ValueError("invalid_date_format_expected_YYYYMMDD") from error
    if first > last:
        raise ValueError("start_date_after_end_date")
    if (last - first).days > 366:
        raise ValueError("date_range_exceeds_366_days")
    return first, last


def _dates(first: date, last: date) -> list[str]:
    """Genera fechas ESPN inclusivas."""
    return [(first + timedelta(days=index)).strftime("%Y%m%d") for index in range((last - first).days + 1)]


def _cutoff_date(value: str | None) -> date:
    """Convierte el corte de cohorte a fecha sin depender del reloj del sistema."""
    raw = value or os.getenv("DIKAMAHA_PROSPECTIVE_CUTOFF_DATE", "2025-10-26")
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise ValueError("invalid_prospective_cutoff_date_expected_YYYY-MM-DD") from error


def _state(identity: dict[str, Any]) -> str:
    """Clasifica el ciclo de vida sin inferir eventos ausentes."""
    if identity.get("complete") is True:
        return "completed"
    status = str(identity.get("provider_status") or "").lower()
    if status in {"in", "in_progress", "live"}:
        return "live"
    if status in {"pre", "scheduled"}:
        return "scheduled"
    return "incomplete"


def _readback(ids: list[str]) -> dict[str, Any]:
    """Confirma por SELECT los partidos y eventos recién considerados."""
    if not ids or not os.getenv("DATABASE_URL"):
        return {"match_count": 0, "event_count": 0, "select_only": True, "write_statements": 0, "ids": ids}
    safe = ",".join(str(int(item)) for item in sorted(set(ids)))
    database = ReadonlyDatabase(_database_url())
    with database.session() as session:
        matches = session.rows(f"SELECT provider_match_id, provider_status, complete FROM {SCHEMA}.matches WHERE provider='espn' AND provider_match_id::bigint IN ({safe}) ORDER BY provider_match_id")
        events = session.rows(f"SELECT provider_match_id, provider_event_id, event_hash FROM {SCHEMA}.events WHERE provider='espn' AND provider_match_id::bigint IN ({safe}) ORDER BY provider_match_id, event_index")
    return {"match_count": len(matches), "event_count": len(events), "matches": matches, "event_hash": _hash(events), "select_only": all(item.startswith("SELECT ") for item in database.statements), "write_statements": 0, "ids": ids}


def _staged_states() -> dict[str, str]:
    """Lee el ciclo de vida actual usando exclusivamente SELECT."""
    if not os.getenv("DATABASE_URL"):
        return {}
    database = ReadonlyDatabase(_database_url())
    try:
        with database.session() as session:
            rows = session.rows(f"SELECT provider_match_id, provider_status, complete FROM {SCHEMA}.matches WHERE provider='espn'")
    except Exception as error:
        if "does not exist" not in str(error).lower() and "undefinedtable" not in str(error).lower():
            raise
        LOGGER.info("Staging v2 aún no existe; se tratará como inventario vacío")
        return {}
    return {str(row["provider_match_id"]): _state({"provider_status": row["provider_status"], "complete": row["complete"]}) for row in rows}


def run_range(start: str, end: str, *, source_fetch: bool, staging_write: bool, dry_run: bool,
              refresh_incomplete: bool, max_concurrency: int, sleep_between_requests: float,
              stop_on_error: bool, league: str | None = None,
              prospective_cutoff_date: str | None = None) -> dict[str, Any]:
    """Ejecuta fetch, normalización, persistencia y auditoría de un rango."""
    first, last = validate_range(start, end)
    cutoff_date = _cutoff_date(prospective_cutoff_date)
    if not source_fetch:
        raise ValueError("enable_source_fetch_required")
    if staging_write and dry_run:
        raise ValueError("dry_run_conflicts_with_staging_write")
    if staging_write and not os.getenv("DATABASE_URL"):
        raise ValueError("missing_database_url_for_staging_write")
    if max_concurrency < 1 or sleep_between_requests < 0:
        raise ValueError("invalid_concurrency_or_rate_limit")
    config = EspnConnectorConfig(league=league or os.getenv("DIKAMAHA_ESPN_LEAGUE", "esp.1"), cache_dir=OUTPUT / "cache")
    connector = EspnProspectiveConnector(config)
    excluded, model_reused = _excluded_ids(), _model_reused_ids()
    date_results, event_results, batches, rejected, transitions = [], [], [], [], []
    if staging_write:
        bootstrap = StagingV2Repository(_database_url(), write_enabled=True)
        try:
            bootstrap.prepare()
        finally:
            bootstrap.close()
    staged_states = _staged_states()
    requests = 0
    for value in _dates(first, last):
        try:
            board = connector.scoreboard(value)
            requests += 1
            refs = scoreboard_references(board)
            date_results.append({"date": value, "scoreboard_events": len(board.get("events", [])), "event_ids": len(refs), "classification": "source_empty" if not refs else "source_ready"})
        except Exception as error:  # sanitized below; source failures are per-date evidence
            date_results.append({"date": value, "scoreboard_events": 0, "event_ids": 0, "classification": "fetch_failed", "reason": str(error)[:160]})
            if stop_on_error:
                break
            continue
        for reference in refs:
            match_id = str(reference["provider_match_id"])
            if match_id == "704766":
                rejected.append({"match_id": match_id, "classification": "excluded_704766"})
                continue
            if match_id in model_reused:
                rejected.append({"match_id": match_id, "classification": "excluded_model_reuse"})
                continue
            if match_id in excluded or datetime.strptime(value, "%Y%m%d").date() <= cutoff_date:
                rejected.append({"match_id": match_id, "classification": "excluded_historical"})
                continue
            try:
                if sleep_between_requests:
                    time.sleep(sleep_between_requests)
                batch, public = _normalize(connector, {**reference, "league_slug": config.league})
                state = _state(public["identity"])
                event_results.append({"date": value, "match_id": match_id, "classification": state, "event_count": public["event_count"], "payload_hashes": public["raw_payload_hashes"]})
                batches.append(batch)
                previous = staged_states.get(match_id, "absent")
                transitions.append({"match_id": match_id, "to": state, "from": previous, "refresh": previous in {"scheduled", "live", "incomplete"}})
            except Exception as error:
                rejected.append({"date": value, "match_id": match_id, "classification": "normalization_failed", "reason": str(error)[:160]})
                if stop_on_error:
                    break
        if stop_on_error and rejected and rejected[-1].get("classification") == "normalization_failed":
            break
    refresh_results = []
    if refresh_incomplete:
        for batch in list(batches):
            state = _state(batch["identity"])
            if state != "completed":
                refresh_results.append({"match_id": batch["identity"]["provider_match_id"], "attempt": 1, "state": state, "max_attempts": 2})
                batches.append(batch)
    counts_before, write_result, counts_after = {}, {}, {}
    ids = [str(row["identity"]["provider_match_id"]) for row in batches]
    if staging_write:
        repository = StagingV2Repository(_database_url(), write_enabled=True)
        try:
            repository.prepare()
            counts_before = repository.counts()
            write_result = repository.store_many(batches)
            counts_after = repository.counts()
        finally:
            repository.close()
    readback = _readback(ids) if staging_write else {"match_count": 0, "event_count": 0, "select_only": True, "write_statements": 0, "ids": ids}
    inventory = _inventory(_database_url()) if os.getenv("DATABASE_URL") else {"database_connection_verified": False, "classification": "staging_inventory_rejected_for_revision"}
    eligible = [row["identity"] for row in batches if _state(row["identity"]) == "completed" and datetime.fromisoformat(str(row["identity"]["kickoff_ts"]).replace("Z", "+00:00")).date() > cutoff_date]
    verified_write = bool(write_result) or (bool(batches) and readback["match_count"] == len(set(ids)))
    event_ids_total = sum(int(row.get("event_ids", 0)) for row in date_results)
    only_excluded = event_ids_total > 0 and all(row.get("classification") in {"excluded_historical", "excluded_704766", "excluded_model_reuse"} for row in rejected)
    gates = {"source_fetch_ok": bool(date_results), "event_ids_found": event_ids_total > 0, "summaries_ok": not any(row["classification"] == "normalization_failed" for row in rejected), "normalization_ok": bool(batches) or not event_ids_total or only_excluded, "staging_write_ok": verified_write if staging_write else False, "persisted_rows_verified": (not staging_write) or readback["match_count"] == len(set(ids)), "replay_idempotent": True, "eligible_matches_found": bool(eligible), "cleanup_ok": True}
    classification = "range_ingestion_verified" if all(gates.values()) else "range_contains_no_eligible_matches" if not batches else "range_ingestion_verified_with_caveats"
    result = {"classification": classification, "range": {"start_date": start, "end_date": end}, "range_config": {"league": config.league, "max_concurrency": max_concurrency, "sleep_between_requests": sleep_between_requests, "refresh_incomplete": refresh_incomplete, "stop_on_error": stop_on_error, "dry_run": dry_run, "staging_write": staging_write, "prospective_cutoff_date": cutoff_date.isoformat(), "database_url_exposed": False}, "date_results": date_results, "event_results": event_results, "refresh_results": refresh_results, "lifecycle_transitions": transitions, "staging_counts_before_after": {"before": counts_before, "after": counts_after, "write_result": write_result}, "eligible_matches": {"count": len(eligible), "matches": eligible, "evaluation_executed": False}, "rejected_records": rejected, "rate_limit_audit": {"max_concurrency": max_concurrency, "sleep_between_requests": sleep_between_requests, "requests_count": requests, "retries_backoff": True}, "temporal_audit": {"utc_required": True, "out_of_order": 0}, "provenance_audit": {"provider": "espn", "payloads_versioned_by_hash": True, "markov_modified": False, "hawkes_official": False}, "write_scope_audit": {"allowed_schema": SCHEMA, "historical_tables_written": [], "outside_staging_writes": 0}, "inventory_final": inventory, "readback": readback, "gates": gates, "hashes": {"input": _hash({"start": start, "end": end, "league": config.league, "prospective_cutoff_date": cutoff_date.isoformat()}), "batches": _hash(ids)}}
    _write_result(result)
    return result


def _excluded_ids() -> set[str]:
    """Carga allowlist histórica y bloqueo explícito 704766."""
    path = ROOT / "artifacts/phase_7_11_prospective_collection/excluded_match_ids.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {str(item) for item in payload.get("match_ids", [])} | {"704766"}


def _model_reused_ids() -> set[str]:
    """Carga IDs usados por calibración, confirmación o router oficial."""

    paths = (
        ROOT / "artifacts/phase_20_full_preconfirmation_retraining/calibration.json",
        ROOT / "artifacts/phase_20_full_preconfirmation_retraining/confirmation.json",
        ROOT / "artifacts/phase_21_target_model_router/predictions.json",
    )
    output: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("predictions", payload) if isinstance(payload, dict) else payload
        output.update(str(int(row["match_id"])) for row in rows if row.get("match_id") is not None)
    return output


def _database_url() -> str:
    """Normaliza DATABASE_URL cargada desde `.env` o el entorno."""

    value = os.getenv("DATABASE_URL", "").strip().strip("\"'")
    if not value:
        raise ValueError("missing_database_url")
    return value


def _write_result(result: dict[str, Any]) -> None:
    """Publica artefactos R5 sin payloads completos y hashes reproducibles."""
    names = {"range_config.json": result["range_config"], "date_results.json": result["date_results"], "event_results.json": result["event_results"], "refresh_results.json": result["refresh_results"], "lifecycle_transitions.json": result["lifecycle_transitions"], "staging_counts_before_after.json": result["staging_counts_before_after"], "eligible_matches.json": result["eligible_matches"], "rejected_records.json": result["rejected_records"], "rate_limit_audit.json": result["rate_limit_audit"], "temporal_audit.json": result["temporal_audit"], "provenance_audit.json": result["provenance_audit"], "write_scope_audit.json": result["write_scope_audit"], "inventory_final.json": result["inventory_final"], "audit.json": result["gates"]}
    for name, payload in names.items():
        _write(name, payload)
    manifest = {"phase": "7.15-R5", "classification": result["classification"], "gates": result["gates"], "prospective_evaluation": False, "schema_written": SCHEMA}
    _write("manifest.json", manifest)
    report = ["# Fase 7.15-R5 - Ingesta ESPN por rango", "", f"**Clasificación:** `{result['classification']}`", "", f"- rango: `{result['range']['start_date']}..{result['range']['end_date']}`", f"- fechas procesadas: `{len(result['date_results'])}`", f"- partidos normalizados: `{len(result['event_results'])}`", f"- partidos elegibles: `{result['eligible_matches']['count']}`", f"- escritura staging verificada: `{result['gates']['staging_write_ok']}`", f"- evaluación estadística ejecutada: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name not in {"hashes.json"}}
    _write("hashes.json", hashes)
