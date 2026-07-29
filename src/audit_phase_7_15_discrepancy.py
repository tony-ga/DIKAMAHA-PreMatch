"""Reconcilia la evidencia de Fase 7.15 con staging v2.

Todas las consultas PostgreSQL son SELECT. No repite la escritura de ingesta.

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
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

from src.audit_staging_inventory import _inventory
from src.postgres_readonly_staging import ReadonlyDatabase, database_error_types, detect_capabilities, sanitize_error

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_15_espn_connector/ingestion_discrepancy_audit.json"
LOGGER = logging.getLogger(__name__)
if load_dotenv:
    load_dotenv(ROOT / ".env")


def _load(path: Path) -> Any:
    """Carga artefactos existentes sin registrar contenido sensible."""

    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _hash(value: Any) -> str:
    """Calcula hash estable del diagnóstico."""

    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _alternative_relations(database_url: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Busca relaciones con nombres de staging en otros esquemas, sólo SELECT."""

    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        identity = session.rows("SELECT current_database() AS database_name, current_schema() AS current_schema")
        rows = session.rows("SELECT table_schema, table_name FROM information_schema.tables WHERE table_name IN ('matches','events','raw_payloads','rejected_raw_events','ingestion_runs','write_audit') AND table_schema <> 'prospective_staging_v2' ORDER BY table_schema, table_name")
    audit = {"statements": database.statements, "select_only": all(item.startswith("SELECT ") for item in database.statements), "connection_closed": database.closed, "write_statements": 0}
    return [{"schema": str(row["table_schema"]), "table": str(row["table_name"])} for row in rows], {**audit, "identity": identity[0] if identity else {}}


def _classification(inventory: dict[str, Any], phase: dict[str, Any]) -> str:
    """Clasifica la discrepancia según fuente, normalización y persistencia."""

    if not inventory.get("database_connection_verified") or not inventory.get("postgres_readonly_audit", {}).get("select_only"):
        return "ingestion_runner_rejected_for_revision"
    if phase.get("source_fetch_ok") and not phase.get("eligible_matches_found"):
        return "source_returned_no_eligible_matches"
    if phase.get("source_fetch_ok") and phase.get("eligible_matches_found") and not phase.get("staging_write_ok"):
        return "ingestion_runner_rejected_for_revision"
    if phase.get("staging_write_ok"):
        return "staging_write_verified"
    return "source_data_rejected"


def _run(database_url: str) -> dict[str, Any]:
    """Construye la reconciliación usando inventario y artefactos saneados."""

    inventory = _inventory(database_url)
    alternatives, db_identity = _alternative_relations(database_url)
    manifest = _load(ROOT / "artifacts/phase_7_15_espn_connector/manifest.json")
    requests = _load(ROOT / "artifacts/phase_7_15_espn_connector/ingestion_requests.json")
    normalized_matches = _load(ROOT / "artifacts/phase_7_15_espn_connector/normalized_matches.json")
    normalized_events = _load(ROOT / "artifacts/phase_7_15_espn_connector/normalized_events.json")
    rejected = _load(ROOT / "artifacts/phase_7_15_espn_connector/rejected_raw_events.json")
    result = {"database_connection_verified": True, "database_identity": db_identity, "staging": inventory["staging_tables"], "current_counts": inventory["counts"], "ingestion_runs": inventory["ingestion_runs"], "phase_7_15": {"manifest": manifest, "request_count": len(requests), "normalized_matches_artifact_count": len(normalized_matches), "normalized_events_artifact_count": len(normalized_events), "rejected_artifact_count": len(rejected), "http_response_evidence": bool(requests), "persisted_row_evidence": inventory["counts"].get("matches", 0) + inventory["counts"].get("events", 0) + inventory["counts"].get("raw_payloads", 0) + inventory["counts"].get("rejected_raw_events", 0) > 0}, "phase_7_14": {"manifest": _load(ROOT / "artifacts/phase_7_14_prospective_ingestion/manifest.json"), "source_config": _load(ROOT / "artifacts/phase_7_14_prospective_ingestion/source_config_sanitized.json")}, "execution_dates": {"ingestion_20260714_registered": inventory["diagnosis"].get("ingestion_20260714_inserted_rows"), "data_20260717_present": inventory["diagnosis"].get("data_20260717_present")}, "alternative_relations": alternatives, "eligibility": {"all_current_matches_excluded": not inventory["eligible_matches"]["match_ids"] and bool(inventory["excluded_matches"]["excluded_present_match_ids"]), "excluded_match_ids": inventory["excluded_matches"], "eligible_matches": inventory["eligible_matches"]}, "normalization_reconciliation": {"payloads_received": inventory["diagnosis"].get("payload_received_rows"), "matches_normalized": inventory["diagnosis"].get("normalized_match_rows"), "events_normalized": inventory["diagnosis"].get("normalized_event_rows"), "rejected_raw_events": inventory["diagnosis"].get("rejected_rows"), "rejection_reasons": inventory["rejected_records"].get("by_reason", {}), "difference_payload_to_normalized": inventory["diagnosis"].get("payload_to_normalized_difference"), "difference_normalized_to_rejected": inventory["diagnosis"].get("normalization_to_rejection_difference")}, "endpoint_diagnosis": {"requested_operations": requests, "source_returned_http_evidence_without_persistence": bool(requests) and inventory["counts"].get("matches", 0) == 0, "evaluables_absent_after_normalization": not normalized_matches and not normalized_events, "competition_or_endpoint_explanation": "scoreboard returned no references or all references were filtered before build_batch"}, "runner_database_schema": {"database_url_exposed": False, "configured_schema": "prospective_staging_v2", "write_requires_flag": "--enable-staging-write", "runner_confirmed_persistence": bool(manifest.get("staging_write_ok", False))}, "postgres_readonly_audit": inventory["postgres_readonly_audit"]}
    result["classification"] = _classification(inventory, manifest)
    result["hashes"] = {key: _hash(result[key]) for key in result if key != "hashes"}
    return result


def _write(result: dict[str, Any]) -> None:
    """Escribe el informe de discrepancia sin payloads completos."""

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(OUTPUT)


def main() -> int:
    """Ejecuta reconciliación read-only y conserva evidencia auditable."""

    capabilities = detect_capabilities()
    if not capabilities.ready:
        result = {"classification": "ingestion_runner_rejected_for_revision", "database_connection_verified": False, "reason": f"missing:{','.join(capabilities.missing())}", "postgres_readonly_audit": {"select_only": True, "write_statements": 0}, "hashes": {}}
    else:
        try:
            result = _run(os.environ["DATABASE_URL"])
        except (OSError, ValueError, *database_error_types()) as error:
            result = {"classification": "ingestion_runner_rejected_for_revision", "database_connection_verified": False, "reason": sanitize_error(error, os.environ.get("DATABASE_URL")), "postgres_readonly_audit": {"select_only": True, "write_statements": 0}, "hashes": {}}
    _write(result)
    LOGGER.info("Discrepancia 7.15: %s", result["classification"])
    return 1 if result["classification"] == "ingestion_runner_rejected_for_revision" else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
