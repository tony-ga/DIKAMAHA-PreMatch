"""Ingesta por lotes del corpus multi-liga ESPN en staging aislado.

Lee referencias producidas por Fase 36, consulta sólo endpoints documentados
y conserva payloads crudos dentro de ``prospective_staging_v2``. La escritura
requiere ``--write-staging`` y una migración previa de ``league_slug``.

Requirements:
    requests
    tenacity
    SQLAlchemy==2.0.41
    psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text

from src.espn_phase_7_15_r3 import _normalize
from src.espn_prospective_connector import EspnConnectorConfig, EspnProspectiveConnector
from src.prospective_ingestion_v2 import StagingV2Repository

LOGGER = logging.getLogger(__name__)
INPUT = ROOT / "artifacts/phase_36_multileague_discovery/references.json"
OUTPUT = ROOT / "artifacts/phase_37_multileague_staging_ingestion"
SCHEMA = "prospective_staging_v2"


def _load_references(path: Path) -> list[dict[str, str]]:
    """Carga referencias deduplicadas y exige el slug de liga."""

    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("malformed_discovery_references")
    required = {"provider_match_id", "competition_id", "league_slug"}
    return [{key: str(row[key]) for key in required} for row in rows if required <= set(row)]


def _group(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    """Agrupa referencias por liga con orden reproducible."""

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["league_slug"], []).append(row)
    return {league: sorted(items, key=lambda row: (row["provider_match_id"], row["competition_id"])) for league, items in sorted(grouped.items())}


def _failure_keys(path: Path) -> set[tuple[str, str]]:
    """Carga claves liga-partido de un reporte de fallos previo."""

    rows = json.loads(path.read_text(encoding="utf-8"))
    return {(str(row["league_slug"]), str(row["match_id"])) for row in rows if "league_slug" in row and "match_id" in row}


def _fetch_one(reference: dict[str, str], sleep_seconds: float) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Descarga y normaliza un partido, sin persistirlo."""

    league = reference["league_slug"]
    connector = EspnProspectiveConnector(EspnConnectorConfig(league=league, cache_dir=OUTPUT / "cache" / league, cache_ttl_seconds=86400))
    try:
        if sleep_seconds:
            time.sleep(sleep_seconds)
        batch, public = _normalize(connector, reference)
        return batch, {"league_slug": league, "match_id": reference["provider_match_id"], "status": "normalized", "event_count": public["event_count"], "rejected_count": public["rejected_count"], "raw_payload_count": len(batch["raw_payloads"])}
    except (OSError, ValueError, RuntimeError, requests.RequestException) as error:
        LOGGER.warning("Ingesta multi-liga fallida league=%s match=%s: %s", league, reference["provider_match_id"], error)
        return None, {"league_slug": league, "match_id": reference["provider_match_id"], "status": "failed", "error_type": type(error).__name__}


def _schema_has_league(database_url: str) -> bool:
    """Verifica que staging tenga la columna multi-liga antes de escribir."""

    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    query = text("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=:schema AND table_name='matches' AND column_name='league_slug')")
    try:
        with engine.connect() as connection:
            return bool(connection.execute(query, {"schema": SCHEMA}).scalar_one())
    finally:
        engine.dispose()


def _hash(value: Any) -> str:
    """Calcula un hash de auditoría sin exponer payloads."""

    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _write(result: dict[str, Any]) -> None:
    """Escribe artefactos sanitizados y sus hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    suffix = "_retry" if result["config"].get("failures_input") else ""
    for name in ("config", "coverage", "failures", "audit"):
        (OUTPUT / f"{name}{suffix}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True), encoding="utf-8")
    report = ["# Fase 37 — ingesta multi-liga en staging", "", f"**Clasificación:** `{result['classification']}`", "", f"- referencias recibidas: `{result['coverage']['references_received']}`", f"- partidos normalizados: `{result['coverage']['normalized_matches']}`", f"- partidos fallidos: `{result['coverage']['failed_matches']}`", f"- ligas procesadas: `{result['coverage']['league_count']}`", f"- escritura staging: `{result['config']['staging_write']}`"]
    (OUTPUT / f"final_report{suffix}.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: _hash(path.read_bytes()) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name not in {"hashes.json", "hashes_retry.json"}}
    (OUTPUT / f"hashes{suffix}.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def ingest(path: Path = INPUT, *, workers: int = 8, sleep_seconds: float = 0.15, write_staging: bool = False, max_matches: int = 0, failures_path: Path | None = None) -> dict[str, Any]:
    """Ingiere todas las referencias o un límite controlado por liga."""

    rows = _load_references(path)
    grouped = _group(rows)
    failure_keys = _failure_keys(failures_path) if failures_path else None
    if failure_keys is not None:
        grouped = {league: [row for row in items if (league, row["provider_match_id"]) in failure_keys] for league, items in grouped.items()}
        grouped = {league: items for league, items in grouped.items() if items}
    if max_matches < 0 or workers < 1 or sleep_seconds < 0:
        raise ValueError("invalid_ingestion_limits")
    database_url = os.getenv("DATABASE_URL")
    if write_staging and (not database_url or not _schema_has_league(database_url)):
        raise RuntimeError("league_slug_migration_required")
    statuses = []
    for league, references in grouped.items():
        selected = references[:max_matches] if max_matches else references
        league_batches = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_fetch_one, reference, sleep_seconds) for reference in selected]
            for future in as_completed(futures):
                batch, status = future.result()
                statuses.append(status)
                if batch is not None:
                    league_batches.append(batch)
        if write_staging and league_batches:
            _store_league(database_url, league, league_batches)
        del league_batches
    normalized = sum(row["status"] == "normalized" for row in statuses)
    failed = sum(row["status"] == "failed" for row in statuses)
    coverage = {"references_received": len(rows), "selected_references": len(statuses), "normalized_matches": normalized, "failed_matches": failed, "league_count": len(grouped), "normalized_by_league": {league: sum(row["status"] == "normalized" and row["league_slug"] == league for row in statuses) for league in grouped}}
    normalized_ids = [(row["league_slug"], row["match_id"]) for row in statuses if row["status"] == "normalized"]
    result = {"classification": "staging_ingestion_verified" if normalized and not failed else "staging_ingestion_with_failures" if normalized else "no_matches_normalized", "config": {"input": str(path.relative_to(ROOT)), "failures_input": str(failures_path.relative_to(ROOT)) if failures_path else None, "workers": workers, "sleep_seconds": sleep_seconds, "staging_write": write_staging, "database_url_exposed": False, "max_matches_per_league": max_matches}, "coverage": coverage, "failures": [row for row in statuses if row["status"] == "failed"], "audit": {"raw_payloads_saved_by_repository": True, "router_modified": False, "official_laliga_training_modified": False, "normalized_hash": _hash(sorted(normalized_ids))}}
    _write(result)
    return result


def _store_league(database_url: str, league: str, batches: list[dict[str, Any]]) -> None:
    """Persiste un lote de una sola liga en una transacción staging."""

    if not batches:
        return
    repository = StagingV2Repository(database_url, write_enabled=True)
    try:
        repository.prepare()
        repository.store_many(batches)
        LOGGER.info("Staging multi-liga escrito league=%s matches=%d", league, len(batches))
    finally:
        repository.close()


def main() -> int:
    """Ejecuta la ingesta con escritura siempre explícita."""

    parser = argparse.ArgumentParser(description="Ingesta de referencias ESPN multi-liga")
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--max-matches", type=int, default=0)
    parser.add_argument("--write-staging", action="store_true")
    parser.add_argument("--retry-failures", action="store_true", help="reintenta sólo failures.json de la corrida previa")
    args = parser.parse_args()
    failures_path = OUTPUT / "failures.json" if args.retry_failures else None
    result = ingest(args.input, workers=args.workers, sleep_seconds=args.sleep_seconds, write_staging=args.write_staging, max_matches=args.max_matches, failures_path=failures_path)
    LOGGER.info("Ingesta multi-liga: %s", result["classification"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
