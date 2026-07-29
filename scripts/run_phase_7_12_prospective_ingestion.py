"""Ejecuta la ingesta prospectiva staging de Fase 7.12.

No realiza tráfico externo ni escritura staging sin las banderas explícitas
``DIKAMAHA_PROSPECTIVE_SOURCE_ENABLED=true``,
``DIKAMAHA_PROSPECTIVE_STAGING_WRITE_ENABLED=true`` y
``DIKAMAHA_PROSPECTIVE_DRY_RUN=false``.

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

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

from src.collect_prospective_signals import _prior_match_ids
from src.prospective_staging_ingestion import (
    EspnProspectiveProvider,
    IngestionConfig,
    ProspectiveIngestionError,
    SourceMatchRef,
    SqlAlchemyStagingRepository,
    build_batch,
    frozen_config_payload,
    source_config_from_env,
)

OUTPUT = ROOT / "artifacts/phase_7_12_prospective_ingestion"
PHASE_711 = ROOT / "artifacts/phase_7_11_prospective_collection"
LOGGER = logging.getLogger(__name__)


def _write_json(path: Path, payload: Any) -> None:
    """Escribe JSON atómico, ordenado y sin valores no serializables."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _hash_file(path: Path) -> str:
    """Calcula SHA-256 de un archivo existente."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_manifest_path() -> Path | None:
    """Resuelve el manifiesto explícito sin permitir rutas fuera del workspace."""

    value = os.getenv("DIKAMAHA_PROSPECTIVE_SOURCE_MANIFEST")
    if not value:
        return None
    path = Path(value).resolve()
    if ROOT.resolve() not in path.parents:
        raise ProspectiveIngestionError("source_manifest_outside_workspace")
    return path


def _references() -> list[SourceMatchRef]:
    """Carga referencias del proveedor sin inventar fixtures ni endpoints."""

    path = _source_manifest_path()
    if path is None or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("matches", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ProspectiveIngestionError("malformed_source_manifest")
    return [SourceMatchRef(str(row["provider_match_id"]), str(row["competition_id"])) for row in rows]


def _excluded_ids() -> set[int]:
    """Conserva todas las exclusiones históricas y el identificador bloqueado."""

    historical, _ = _prior_match_ids()
    historical.add(704766)
    return historical


def _contract(config: IngestionConfig) -> dict[str, Any]:
    """Publica contrato de fuente, límites y política de errores versionada."""

    return {
        "version": config.version, "provider": "espn", "authorization": "explicit_opt_in_required",
        "documented_endpoints": ["/events/{event}", "/events/{event}/competitions/{competition}/plays?limit=300"],
        "authentication": "not_required_per_local_documentation; API_KEY_optional_and_never_logged",
        "rate_limit_policy": {"retry": "exponential_backoff_existing_espn_client", "max_attempts": 4, "page_limit": config.page_limit},
        "timeouts": {"connect_and_read_seconds": config.timeout_seconds},
        "payload_limit_bytes": config.payload_max_bytes, "cutoff_ts": config.cutoff_ts,
        "forbidden": ["historical_table_writes", "external_calls_without_opt_in", "evaluation", "markov_calibration", "hawkes_calibration"],
    }


def _schema() -> dict[str, Any]:
    """Describe el esquema staging aislado, llaves y alcance de escritura."""

    return {"schema": "prospective_staging", "tables": {
        "ingestion_runs": ["run_hash unique", "status", "created_at"],
        "raw_payloads": ["provider", "provider_match_id", "endpoint", "payload_hash", "payload", "fetched_at"],
        "matches": ["provider", "provider_match_id", "competition_id", "kickoff_ts", "home_provider_team_id", "away_provider_team_id", "complete"],
        "events": ["provider_match_id", "event_index", "event_hash", "event_ts", "team_provider_id nullable", "annulled", "raw_data"],
        "write_audit": ["run_hash", "table_name", "inserted_rows", "created_at"],
    }, "unique_keys": {
        "raw_payloads": ["provider", "provider_match_id", "endpoint", "payload_hash"],
        "matches": ["provider", "provider_match_id"],
        "events": ["provider", "provider_match_id", "event_index", "event_hash"],
    }, "historical_tables_writable": []}


def _inactive_result(config: IngestionConfig, reason: str) -> dict[str, Any]:
    """Construye un resultado seguro sin red ni escritura de base de datos."""

    return {"classification": "prospective_source_unavailable", "reason": reason, "config": frozen_config_payload(config),
            "run": {"network_calls": 0, "staging_writes": 0, "evaluation_performed": False, "calibration_performed": False},
            "counts": {"before": None, "after": None, "database_verification": "not_executed"}, "batches": []}


def _active_result(config: IngestionConfig, refs: list[SourceMatchRef]) -> dict[str, Any]:
    """Obtiene y persiste referencias explícitas mediante transacciones staging."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return _inactive_result(config, "missing_database_url")
    provider, repository = EspnProspectiveProvider(config), SqlAlchemyStagingRepository(database_url)
    try:
        repository.prepare()
        before, batches, excluded = repository.counts(), [], _excluded_ids()
        for reference in refs:
            if reference.provider_match_id.isdigit() and int(reference.provider_match_id) in excluded:
                batches.append({"provider_match_id": reference.provider_match_id, "status": "excluded_historical_or_704766"})
                continue
            batch = build_batch(provider.fetch(reference))
            batches.append({"provider_match_id": reference.provider_match_id, "status": "stored", "writes": repository.store(batch),
                            "payload_hashes": [row["payload_hash"] for row in batch["raw_payloads"]]})
        return {"classification": "prospective_ingestion_ready", "reason": None, "config": frozen_config_payload(config),
                "run": {"network_calls": len(refs), "staging_writes": sum(row.get("writes", {}).get("matches", 0) for row in batches), "evaluation_performed": False, "calibration_performed": False},
                "counts": {"before": before, "after": repository.counts(), "database_verification": "staging_write_enabled"}, "batches": batches}
    finally:
        repository.close()


def _write_artifacts(result: dict[str, Any]) -> None:
    """Emite sólo hashes y metadata; nunca payloads crudos ni secretos."""

    config, contract, schema = result["config"], _contract(IngestionConfig()), _schema()
    raw = [{"provider_match_id": row["provider_match_id"], "payload_hashes": row.get("payload_hashes", [])} for row in result["batches"]]
    payloads = {"ingestion_contract.json": contract, "source_config_sanitized.json": config, "staging_schema.json": schema,
                "ingestion_run.json": result["run"] | {"classification": result["classification"], "reason": result["reason"]},
                "raw_payload_manifest.json": raw, "staging_counts_before_after.json": result["counts"],
                "deduplication_audit.json": {"idempotent_keys": schema["unique_keys"], "batches": result["batches"]},
                "temporal_audit.json": {"event_ts_utc_required": True, "events_validated": 0, "status": result["classification"]},
                "provenance_audit.json": {"provider": "espn", "raw_payloads_persisted_only_in_staging": True, "historical_artifacts_modified": False},
                "security_audit.json": {"secrets_logged": 0, "external_calls": result["run"]["network_calls"], "network_opt_in": config["network_calls_permitted"]},
                "postgres_write_scope_audit.json": {"allowed_schema": "prospective_staging", "historical_tables_written": [], "staging_writes": result["run"]["staging_writes"]}}
    for name, payload in payloads.items():
        _write_json(OUTPUT / name, payload)
    manifest = {"phase": "7.12", "classification": result["classification"], "version": config["version"],
                "historical_tables_modified": False, "evaluation_performed": False, "hawkes_enabled_default": False,
                "markov_official_modified": False, "source_configured": config["network_calls_permitted"]}
    _write_json(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text(_report(result), encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write_json(OUTPUT / "hashes.json", hashes)


def _report(result: dict[str, Any]) -> str:
    """Resume la corrida sin declarar datos o evaluación inexistentes."""

    return "\n".join(["# Fase 7.12 - Ingesta prospectiva staging", "", f"**Clasificación:** `{result['classification']}`", "",
        f"- razón: `{result['reason'] or 'configured'}`", f"- llamadas externas: `{result['run']['network_calls']}`", f"- escrituras staging: `{result['run']['staging_writes']}`",
        "- tablas históricas modificadas: `False`", "- evaluación/calibración: `False`", "- Markov oficial intacto; Hawkes permanece desactivado por defecto."])


def main() -> int:
    """Ejecuta el dry-run seguro o la ingesta staging activada explícitamente."""

    config = source_config_from_env()
    try:
        refs = _references()
        enabled = config.source_enabled and config.staging_write_enabled and not config.dry_run
        result = _active_result(config, refs) if enabled and refs else _inactive_result(config, "source_not_explicitly_configured" if not enabled else "missing_source_manifest")
    except (ESPNClientError, ProspectiveIngestionError, OSError, ValueError) as error:
        result = _inactive_result(config, sanitize_error(error, os.getenv("DATABASE_URL")))
        result["classification"] = "prospective_ingestion_rejected_for_revision"
    _write_artifacts(result)
    LOGGER.info("Fase 7.12: %s", result["classification"])
    return 1 if result["classification"] == "prospective_ingestion_rejected_for_revision" else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
