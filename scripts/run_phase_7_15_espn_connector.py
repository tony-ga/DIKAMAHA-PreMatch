"""Ejecuta la Fase 7.15 con red y escritura staging explícitas.

Por defecto no usa red ni PostgreSQL. La fuente ESPN se habilita con
``--enable-source-fetch`` y la persistencia con ``--enable-staging-write``.

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependencia opcional del runner
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if load_dotenv:
    load_dotenv(ROOT / ".env")

from src.espn_prospective_connector import (
    EspnConnectorError, EspnConnectorConfig, EspnProspectiveConnector,
    sanitized_endpoint_config, scoreboard_references,
)
from src.prospective_ingestion_v2 import (
    IngestionV2Config, ProspectiveIngestionV2Error, SourceReference,
    StagingV2Repository, build_batch, canonical_hash, utc_now,
)

OUTPUT = ROOT / "artifacts/phase_7_15_espn_connector"
PHASE_711 = ROOT / "artifacts/phase_7_11_prospective_collection"
LOGGER = logging.getLogger(__name__)


def write_json(path: Path, payload: Any) -> None:
    """Escribe JSON atómico y ordenado."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    """Define los modos de consulta documentados sin manifiesto manual."""

    parser = argparse.ArgumentParser(description="Fase 7.15 ESPN prospective connector")
    parser.add_argument("--enable-source-fetch", action="store_true")
    parser.add_argument("--enable-staging-write", action="store_true")
    parser.add_argument("--date", help="YYYYMMDD para scoreboard ESPN")
    parser.add_argument("--start-date", help="inicio inclusivo YYYYMMDD")
    parser.add_argument("--end-date", help="fin inclusivo YYYYMMDD")
    parser.add_argument("--refresh-incomplete", action="store_true")
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--sleep-between-requests", type=float, default=0.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--calendar", action="store_true")
    parser.add_argument("--match-id")
    parser.add_argument("--league", default=None, help="league ESPN explícita, por defecto DIKAMAHA_ESPN_LEAGUE o esp.1")
    parser.add_argument("--prospective-cutoff-date", help="corte de cohorte YYYY-MM-DD; no usa la fecha del sistema")
    return parser.parse_args()


def excluded_ids() -> set[str]:
    """Mantiene partidos históricos y 704766 fuera de la nueva captura."""

    path = PHASE_711 / "excluded_match_ids.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"match_ids": []}
    return {str(value) for value in payload.get("match_ids", [])} | {"704766"}


def source_contract() -> dict[str, Any]:
    """Documenta los endpoints ESPN autorizados y exclusiones del conector."""

    return {"version": "phase_7_15_espn_connector_v1", "provider": "espn_unofficial",
            "authorized_endpoints": ["site/{league}/scoreboard?dates=YYYYMMDD", "core/leagues/{league}/calendar",
                                     "core/leagues/{league}/events/{event}", "core/leagues/{league}/events/{event}/competitions/{competition}/plays?limit=300",
                                     "site/{league}/summary?event={event}"],
            "forbidden": ["odds", "probabilities", "historical_table_writes", "redis", "external_domains"],
            "writes": "only prospective_staging_v2 with --enable-staging-write", "hawkes_enabled_default": False}


def empty_result(reason: str, config: EspnConnectorConfig) -> dict[str, Any]:
    """Construye evidencia consistente cuando la fuente no está habilitada."""

    return {"classification": "espn_source_unavailable", "reason": reason, "requests": [], "matches": [], "events": [],
            "rejected": [], "counts": {"before": None, "after": None, "status": "not_executed"},
            "deduplication": {"duplicates": 0}, "network_calls": 0, "staging_writes": 0,
            "source_fetch_ok": False, "normalization_ok": False, "staging_write_ok": False,
            "eligible_matches_found": False, "source_reference_count": 0, "excluded_reference_count": 0,
            "endpoint_config": sanitized_endpoint_config(config)}


def references(connector: EspnProspectiveConnector, args: argparse.Namespace) -> tuple[list[SourceReference], list[dict[str, Any]]]:
    """Selecciona eventos por fecha, calendario o match id usando sólo ESPN."""

    requests, refs = [], []
    if args.calendar:
        calendar = connector.calendar()
        requests.append({"operation": "calendar", "payload_hash": canonical_hash(calendar)})
    if args.date:
        board = connector.scoreboard(args.date)
        requests.append({"operation": "scoreboard", "date": args.date, "payload_hash": canonical_hash(board)})
        refs.extend(SourceReference(**row) for row in scoreboard_references(board))
    if args.match_id:
        event = connector.event(args.match_id)
        competitions = event.get("competitions") if isinstance(event, dict) else []
        if not isinstance(competitions, list) or not competitions or not competitions[0].get("id"):
            raise EspnConnectorError("missing_competition_id")
        refs.append(SourceReference(str(args.match_id), str(competitions[0]["id"])))
        requests.append({"operation": "event", "match_id": str(args.match_id), "payload_hash": canonical_hash(event)})
    unique = {(ref.provider_match_id, ref.competition_id): ref for ref in refs}
    return [unique[key] for key in sorted(unique)], requests


def collect(connector: EspnProspectiveConnector, refs: list[SourceReference], requests: list[dict[str, Any]]) -> dict[str, Any]:
    """Actualiza eventos incrementalmente en memoria, sin evaluar modelos."""

    batches, excluded, excluded_refs = [], excluded_ids(), []
    for reference in refs:
        if reference.provider_match_id in excluded:
            requests.append({"operation": "excluded", "match_id": reference.provider_match_id})
            excluded_refs.append(reference.provider_match_id)
            continue
        event, plays = connector.event(reference.provider_match_id), connector.plays(reference.provider_match_id, reference.competition_id)
        batches.append(build_batch(reference, event, plays, utc_now()))
        requests.append({"operation": "event_and_plays", "match_id": reference.provider_match_id,
                         "event_hash": canonical_hash(event), "plays_hash": canonical_hash(plays)})
    return {"batches": batches, "requests": requests, "network_calls": len(requests), "source_reference_count": len(refs), "excluded_reference_count": len(excluded_refs), "excluded_match_ids": excluded_refs}


def persist(batches: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    """Persiste sólo staging v2 mediante las transacciones del repositorio aislado."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ProspectiveIngestionV2Error("missing_database_url_for_staging_write")
    repository = StagingV2Repository(database_url, write_enabled=True)
    try:
        repository.prepare()
        before = repository.counts()
        writes = [repository.store(batch) for batch in batches]
        after = repository.counts()
        delta = {key: after.get(key, 0) - before.get(key, 0) for key in after}
        inserted = sum(sum(row.values()) for row in writes)
        return {"before": before, "after": after, "delta": delta, "status": "staging_write_enabled", "inserted_rows": inserted, "batches": len(batches)}, inserted
    finally:
        repository.close()


def result_from_collection(collection: dict[str, Any], write: bool) -> dict[str, Any]:
    """Proyecta los lotes a artefactos sin payloads crudos."""

    batches = collection["batches"]
    source_ok = bool(collection["requests"])
    normalized_ok = bool(batches or collection["source_reference_count"] == collection["excluded_reference_count"])
    reason = "source_returned_no_eligible_matches" if not batches else ("dry_run_no_staging_write" if not write else "staging_write_pending")
    return {"classification": "source_returned_no_eligible_matches" if not batches else "source_fetch_ok", "reason": reason,
            "requests": collection["requests"], "matches": [batch["identity"] for batch in batches],
            "events": [row for batch in batches for row in batch["events"]], "rejected": [row for batch in batches for row in batch["rejected"]],
            "counts": {"before": None, "after": None, "status": "dry_run"},
            "deduplication": {"duplicates": sum(batch["event_audit"]["duplicates"] for batch in batches)},
            "network_calls": collection["network_calls"], "staging_writes": 0,
            "source_fetch_ok": source_ok, "normalization_ok": normalized_ok, "staging_write_ok": False,
            "eligible_matches_found": bool(batches), "source_reference_count": collection["source_reference_count"],
            "excluded_reference_count": collection["excluded_reference_count"], "excluded_match_ids": collection["excluded_match_ids"]}


def artifacts(result: dict[str, Any], config: EspnConnectorConfig) -> dict[str, Any]:
    """Genera artefactos auditables sin raws, secretos o resultados de modelos."""

    events = [{key: value for key, value in row.items() if key != "raw_data"} for row in result["events"]]
    rejected = [{key: value for key, value in row.items() if key != "raw_data"} for row in result["rejected"]]
    return {"espn_source_contract.json": source_contract(), "endpoint_config_sanitized.json": sanitized_endpoint_config(config),
            "cache_policy.json": {"local_cache": True, "ttl_seconds": config.cache_ttl_seconds, "payloads_not_in_artifacts": True},
            "ingestion_requests.json": result["requests"], "normalized_matches.json": result["matches"], "normalized_events.json": events,
            "rejected_raw_events.json": rejected, "staging_counts_before_after.json": result["counts"], "deduplication_audit.json": result["deduplication"],
            "temporal_audit.json": {"utc_required": True, "event_ts_before_ingestion_checked": True, "valid_events": len(events), "rejected_events": len(rejected)},
            "provenance_audit.json": {"provider": "espn", "markov_official_modified": False, "hawkes_enabled_default": False, "phase_7_11_staging_v2_allowlisted": True},
            "security_audit.json": {"secrets_exposed": False, "redis_used": False, "external_domains": ["site.api.espn.com", "sports.core.api.espn.com"] if result["network_calls"] else []},
            "write_scope_audit.json": {"allowed_schema": "prospective_staging_v2", "historical_tables_written": [], "outside_staging_writes": 0, "writes": result["staging_writes"]}}


def write_artifacts(result: dict[str, Any], config: EspnConnectorConfig) -> None:
    """Escribe el set obligatorio y hashes reproducibles de la corrida."""

    for name, payload in artifacts(result, config).items():
        write_json(OUTPUT / name, payload)
    manifest = {"phase": "7.15", "classification": result["classification"], "reason": result["reason"], "network_calls": result["network_calls"],
                "staging_writes": result["staging_writes"], "source_fetch_ok": result["source_fetch_ok"],
                "normalization_ok": result["normalization_ok"], "staging_write_ok": result["staging_write_ok"],
                "eligible_matches_found": result["eligible_matches_found"], "source_reference_count": result.get("source_reference_count", 0),
                "excluded_reference_count": result.get("excluded_reference_count", 0), "historical_tables_modified": False, "evaluation_or_calibration": False}
    write_json(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text("# Fase 7.15 - Conector ESPN\n\n" + f"**Clasificación:** `{result['classification']}`\n\n- Razón: `{result['reason']}`\n- source_fetch_ok: `{result['source_fetch_ok']}`\n- normalization_ok: `{result['normalization_ok']}`\n- staging_write_ok: `{result['staging_write_ok']}`\n- eligible_matches_found: `{result['eligible_matches_found']}`\n- Referencias ESPN: `{result.get('source_reference_count', 0)}`; excluidas: `{result.get('excluded_reference_count', 0)}`\n- Red: `{result['network_calls']}`\n- Filas staging insertadas: `{result['staging_writes']}`\n- Sin modificaciones históricas, Markov oficial o Hawkes.\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir())
              if path.is_file() and path.name not in {"hashes.json", "replay_hashes.json"}}
    write_json(OUTPUT / "hashes.json", hashes)
    write_json(OUTPUT / "replay_hashes.json", {"artifact_hashes": hashes, "replay_required": "same inputs and cache state"})


def main() -> int:
    """Ejecuta una captura autorizada o un dry-run explícitamente inactivo."""

    config = EspnConnectorConfig(league=os.getenv("DIKAMAHA_ESPN_LEAGUE", "esp.1"))
    try:
        args = parse_args()
        if args.start_date or args.end_date or (args.date and not args.calendar and not args.match_id):
            from src.espn_phase_7_15_r5 import run_range
            start = args.start_date or args.date
            end = args.end_date or args.date
            if not start or not end:
                raise ValueError("start_and_end_date_required")
            result = run_range(start, end, source_fetch=args.enable_source_fetch,
                               staging_write=args.enable_staging_write, dry_run=args.dry_run,
                               refresh_incomplete=args.refresh_incomplete,
                               max_concurrency=args.max_concurrency,
                               sleep_between_requests=args.sleep_between_requests,
                               stop_on_error=args.stop_on_error, league=args.league,
                               prospective_cutoff_date=args.prospective_cutoff_date)
            LOGGER.info("Fase 7.15-R5 finalizada: %s", result["classification"])
            return 0 if result["classification"] != "range_ingestion_rejected_for_revision" else 1
        if args.league:
            config = EspnConnectorConfig(league=args.league)
        if not args.enable_source_fetch:
            result = empty_result("source_fetch_not_explicitly_enabled", config)
        elif not any((args.date, args.calendar, args.match_id)):
            result = empty_result("missing_query_selector", config)
        else:
            connector = EspnProspectiveConnector(config)
            refs, requests = references(connector, args)
            collection = collect(connector, refs, requests)
            result = result_from_collection(collection, args.enable_staging_write)
            if args.enable_staging_write and collection["batches"]:
                result["counts"], result["staging_writes"] = persist(collection["batches"])
                result["staging_write_ok"] = result["staging_writes"] > 0 and any(value > 0 for value in result["counts"].get("delta", {}).values())
                if not result["staging_write_ok"]:
                    raise ProspectiveIngestionV2Error("expected_staging_write_inserted_zero_rows")
                result["classification"] = "staging_write_verified"
                result["reason"] = "staging_rows_persisted_and_verified"
            elif args.enable_staging_write:
                result["reason"] = "no_eligible_batches_to_write"
                result["classification"] = "source_returned_no_eligible_matches"
    except (OSError, ValueError, EspnConnectorError, ProspectiveIngestionV2Error) as error:
        result = empty_result("sanitized_error:" + str(error)[:160], config)
        result["classification"] = "ingestion_runner_rejected_for_revision"
        result["source_fetch_ok"] = isinstance(error, ProspectiveIngestionV2Error)
    write_artifacts(result, config)
    LOGGER.info("Fase 7.15 finalizada: %s", result["classification"])
    return 1 if result["classification"] == "ingestion_runner_rejected_for_revision" else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
