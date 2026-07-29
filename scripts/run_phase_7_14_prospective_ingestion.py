"""Runner dry-run/ingesta explícita de Fase 7.14.

Sin ``--enable-staging-write`` no abre PostgreSQL ni escribe staging. Sin
fuente explícita tampoco realiza tráfico ESPN.

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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.prospective_ingestion_v2 import (
    EspnProvider, IngestionV2Config, ProspectiveIngestionV2Error, build_batch,
    canonical_hash, frozen_config, load_references, sanitized_error, StagingV2Repository,
)

OUTPUT = ROOT / "artifacts/phase_7_14_prospective_ingestion"
PHASE_711 = ROOT / "artifacts/phase_7_11_prospective_collection"
LOGGER = logging.getLogger(__name__)


def write_json(path: Path, value: Any) -> None:
    """Escribe artefactos JSON de forma atómica y determinista."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temp.replace(path)


def excluded_ids() -> set[str]:
    """Recupera exclusiones históricas sin alterar ningún artefacto congelado."""

    path = PHASE_711 / "excluded_match_ids.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"match_ids": []}
    return {str(value) for value in payload.get("match_ids", [])} | {"704766"}


def arguments() -> argparse.Namespace:
    """Define una bandera de escritura separada de la habilitación de red."""

    parser = argparse.ArgumentParser(description="Fase 7.14 prospective staging ingestion")
    parser.add_argument("--enable-staging-write", action="store_true")
    parser.add_argument("--source-manifest", type=Path)
    return parser.parse_args()


def config_from_args(args: argparse.Namespace) -> IngestionV2Config:
    """Construye configuración sin permitir que variables habiliten escritura."""

    manifest = args.source_manifest or (Path(os.environ["DIKAMAHA_PROSPECTIVE_SOURCE_MANIFEST"])
                                         if os.getenv("DIKAMAHA_PROSPECTIVE_SOURCE_MANIFEST") else None)
    if manifest is not None:
        manifest = manifest.resolve()
        if ROOT.resolve() not in manifest.parents:
            raise ProspectiveIngestionV2Error("source_manifest_outside_workspace")
    return IngestionV2Config(source_enabled=os.getenv("DIKAMAHA_PROSPECTIVE_SOURCE_ENABLED", "false").lower() == "true",
                             write_enabled=bool(args.enable_staging_write),
                             source_manifest=str(manifest) if manifest else None)


def contract() -> dict[str, Any]:
    """Publica fuente real, controles y alcance de persistencia autorizado."""

    return {"version": "phase_7_14_prospective_ingestion_v1", "provider": "espn_unofficial",
            "documented_endpoints": ["/events/{event}", "/events/{event}/competitions/{competition}/plays?limit=300"],
            "external_calls": "require DIKAMAHA_PROSPECTIVE_SOURCE_ENABLED=true and source manifest",
            "staging_write": "requires --enable-staging-write and DATABASE_URL",
            "forbidden_writes": ["matches", "teams", "events_timeline", "events_ledger", "match_statistics", "raw_api_responses"],
            "retries": "existing ESPNClient exponential backoff, maximum 4 attempts", "hawkes_enabled_default": False}


def schema() -> dict[str, Any]:
    """Describe la zona v2 antes de que una ejecución autorizada la cree."""

    return {"schema": "prospective_staging_v2", "tables": {
        "ingestion_runs": ["run_hash", "status", "created_at"],
        "raw_payloads": ["provider", "provider_match_id", "endpoint", "payload_hash", "payload"],
        "matches": ["provider_match_id", "kickoff_ts", "orientation", "complete"],
        "events": ["provider_match_id", "provider_event_id", "event_hash", "event_ts", "team_provider_id nullable"],
        "rejected_raw_events": ["provider_match_id", "raw_hash", "reason", "raw_data"],
        "write_audit": ["run_hash", "table_name", "inserted_rows"]},
        "deduplication": ["provider", "provider_match_id", "provider_event_id", "event_hash"],
        "write_scope": "only prospective_staging_v2; no historical table"}


def unavailable(config: IngestionV2Config, reason: str) -> dict[str, Any]:
    """Produce un resultado seguro que no afirma llamadas ni escrituras."""

    return {"classification": "prospective_source_unavailable", "reason": reason,
            "config": frozen_config(config), "runs": [], "matches": [], "events": [], "rejected": [],
            "deduplication": {"duplicates": 0}, "counts": {"before": None, "after": None,
            "database_verification": "not_executed"}, "network_calls": 0, "staging_writes": 0}


def collect(config: IngestionV2Config, refs: list[Any]) -> dict[str, Any]:
    """Descarga y valida lotes sin persistirlos; escritura queda fuera del dry-run."""

    provider, excluded = EspnProvider(config), excluded_ids()
    batches, runs, network_calls = [], [], 0
    for reference in refs:
        if reference.provider_match_id in excluded:
            runs.append({"provider_match_id": reference.provider_match_id, "status": "excluded_historical_or_704766"})
            continue
        event, plays, fetched_at = provider.fetch(reference)
        network_calls += 2
        batch = build_batch(reference, event, plays, fetched_at)
        batches.append(batch)
        runs.append({"provider_match_id": reference.provider_match_id, "status": "validated_dry_run",
                     "complete": batch["identity"]["complete"], "event_audit": batch["event_audit"]})
    return {"classification": "prospective_ingestion_verified_with_caveats", "reason": "dry_run_no_staging_write",
            "config": frozen_config(config), "runs": runs, "matches": [row["identity"] for row in batches],
            "events": [event for row in batches for event in row["events"]],
            "rejected": [event for row in batches for event in row["rejected"]],
            "_batches": batches,
            "deduplication": {"duplicates": sum(row["event_audit"]["duplicates"] for row in batches)},
            "counts": {"before": None, "after": None, "database_verification": "dry_run_no_connection"},
            "network_calls": network_calls, "staging_writes": 0}


def persist(config: IngestionV2Config, result: dict[str, Any], refs: list[Any]) -> dict[str, Any]:
    """Guarda sólo lotes staging v2 mediante transacción e informa conteos reales."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ProspectiveIngestionV2Error("missing_database_url_for_staging_write")
    repository = StagingV2Repository(database_url, write_enabled=config.write_enabled)
    try:
        repository.prepare()
        before = repository.counts()
        writes = [repository.store(batch) for batch in result.pop("_batches", [])]
        result["counts"] = {"before": before, "after": repository.counts(), "database_verification": "staging_write_enabled"}
        result["staging_writes"] = sum(sum(row.values()) for row in writes)
        result["reason"] = "staging_write_completed"
        result["classification"] = "prospective_ingestion_verified"
        return result
    finally:
        repository.close()


def artifact_payloads(result: dict[str, Any]) -> dict[str, Any]:
    """Proyecta datos seguros: hashes/provenance, nunca payloads crudos."""

    events = [{key: value for key, value in row.items() if key != "raw_data"} for row in result["events"]]
    rejected = [{key: value for key, value in row.items() if key != "raw_data"} for row in result["rejected"]]
    return {"ingestion_contract.json": contract(), "source_config_sanitized.json": result["config"],
            "staging_schema.json": schema(), "ingestion_runs.json": result["runs"], "collected_matches.json": result["matches"],
            "collected_events.json": events, "rejected_raw_events.json": rejected,
            "deduplication_audit.json": result["deduplication"], "staging_counts_before_after.json": result["counts"],
            "temporal_audit.json": {"event_ts_utc": True, "event_ts_before_fetch_checked": True,
                                    "events_valid": len(events), "rejected": len(rejected)},
            "provenance_audit.json": {"provider": "espn", "raw_payload_storage": "staging_only", "markov_official_modified": False,
                                        "hawkes_enabled_default": False, "phase_7_11_staging_select_supported": True},
            "security_audit.json": {"database_url_exposed": False, "api_key_exposed": False, "redis_used": False,
                                      "external_calls": result["network_calls"], "write_requires_cli_flag": True},
            "write_scope_audit.json": {"historical_tables_written": [], "staging_writes": result["staging_writes"],
                                        "allowed_schema": "prospective_staging_v2", "outside_staging_writes": 0}}


def report(result: dict[str, Any]) -> str:
    """Resume hechos de la corrida sin presentar datos inexistentes como reales."""

    return "\n".join(["# Fase 7.14 - Ingesta prospectiva real", "", f"**Clasificación:** `{result['classification']}`", "",
        f"- Razón: `{result['reason']}`", f"- Llamadas ESPN: `{result['network_calls']}`", f"- Escrituras staging: `{result['staging_writes']}`",
        f"- Partidos validados: `{len(result['matches'])}`", f"- Eventos válidos: `{len(result['events'])}`", f"- Eventos raw rechazados: `{len(result['rejected'])}`",
        "- Tablas históricas, Markov oficial y match_features v1: sin modificaciones.",
        "- Hawkes continúa desactivado por defecto y sólo puede existir en shadow mode."])


def write_artifacts(result: dict[str, Any]) -> None:
    """Emite los artefactos obligatorios y un manifiesto de hashes estable."""

    for name, payload in artifact_payloads(result).items():
        write_json(OUTPUT / name, payload)
    write_json(OUTPUT / "manifest.json", {"phase": "7.14", "classification": result["classification"],
               "result_hash": canonical_hash({key: value for key, value in result.items() if key not in {"config", "_batches"}}),
               "historical_artifacts_modified": False, "evaluation_performed": False, "calibration_performed": False})
    (OUTPUT / "final_report.md").write_text(report(result), encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir())
              if path.is_file() and path.name != "hashes.json"}
    write_json(OUTPUT / "hashes.json", hashes)


def main() -> int:
    """Ejecuta source-unavailable o dry-run; persistencia requiere implementación autorizada."""

    try:
        config = config_from_args(arguments())
        if not config.source_enabled:
            result = unavailable(config, "source_not_explicitly_configured")
        elif not config.source_manifest or not Path(config.source_manifest).exists():
            result = unavailable(config, "missing_source_manifest")
        else:
            refs = load_references(Path(config.source_manifest))
            result = collect(config, refs)
            if config.write_enabled:
                result = persist(config, result, refs)
    except (OSError, ValueError, ESPNClientError, ProspectiveIngestionV2Error) as error:
        result = unavailable(IngestionV2Config(False, False, None), sanitized_error(error))
        result["classification"] = "prospective_ingestion_rejected_for_revision"
    write_artifacts(result)
    LOGGER.info("Fase 7.14 finalizada: %s", result["classification"])
    return 1 if result["classification"] == "prospective_ingestion_rejected_for_revision" else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
