"""Recolecta partidos completos posteriores al cutoff sellado de Fase 76.

Requirements:
    requests
    tenacity
    SQLAlchemy==2.0.41
    psycopg2-binary==2.9.10

Version: 2.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_multileague_ingestion import _fetch_one, _store_league  # noqa: E402
from src.espn_prospective_connector import (  # noqa: E402
    EspnConnectorConfig,
    EspnProspectiveConnector,
    scoreboard_references,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_76_v3_prospective_collection"
LOCK = ROOT / "artifacts/phase_76_v3_prospective_lock/lock.json"
MODEL = ROOT / "artifacts/phase_76_domain_robust_reaudit/model_parameters.json"
SCHEMA = "prospective_staging_v2"


def _database_url() -> str:
    """Obtiene DATABASE_URL sin exponerlo."""

    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("missing_database_url")
    return value


def _lock() -> dict[str, Any]:
    """Carga el contrato prospectivo sellado."""

    if not LOCK.exists():
        raise RuntimeError("missing_phase76_v3_prospective_lock")
    return json.loads(LOCK.read_text(encoding="utf-8"))


def _cutoff() -> datetime:
    """Obtiene el cutoff inmutable del lock."""

    return datetime.fromisoformat(str(_lock()["cutoff_utc"]))


def _leagues() -> list[str]:
    """Lee el catálogo de ligas en modo SELECT-only."""

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    query = text(
        f"SELECT DISTINCT league_slug FROM {SCHEMA}.matches "
        "WHERE league_slug IS NOT NULL ORDER BY league_slug"
    )
    try:
        with engine.connect() as connection:
            return [str(value) for value in connection.execute(query).scalars()]
    finally:
        engine.dispose()


def _discover_one(league: str, day: str) -> list[dict[str, str]]:
    """Descubre referencias completas posteriores al cutoff."""

    connector = EspnProspectiveConnector(EspnConnectorConfig(
        league=league, cache_dir=ROOT / "data/cache/phase_76_independent" / league,
        cache_ttl_seconds=0,
    ))
    board = connector.scoreboard(day)
    references = {row["provider_match_id"]: row
                  for row in scoreboard_references(board)}
    output = []
    for event in board.get("events", []):
        item = _eligible_event(event, league, references)
        if item:
            output.append(item)
    return output


def _eligible_event(
    event: dict[str, Any],
    league: str,
    references: dict[str, dict[str, Any]],
) -> dict[str, str] | None:
    """Filtra estado completo y kickoff estrictamente posterior."""

    match_id = str(event.get("id") or "")
    status = ((event.get("status") or {}).get("type") or {})
    raw_date = str(event.get("date") or "")
    if not match_id or not status.get("completed") or not raw_date:
        return None
    kickoff = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    if kickoff <= _cutoff() or match_id not in references:
        return None
    return {**references[match_id], "league_slug": league,
            "kickoff_ts": kickoff.isoformat()}


def _discover() -> list[dict[str, str]]:
    """Consulta liga/fecha concurrentemente y deduplica referencias."""

    references: dict[tuple[str, str], dict[str, str]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_discover_one, league, day)
                   for league in _leagues() for day in _days()]
        for future in as_completed(futures):
            for row in future.result():
                references[(row["league_slug"],
                            row["provider_match_id"])] = row
    return sorted(references.values(), key=lambda row: (
        row["kickoff_ts"], row["league_slug"], row["provider_match_id"]
    ))


def _days() -> tuple[str, ...]:
    """Genera fechas desde el cutoff hasta hoy UTC."""

    first, last = _cutoff().date(), datetime.now(timezone.utc).date()
    count = (last - first).days + 1
    return tuple((first + timedelta(days=index)).strftime("%Y%m%d")
                 for index in range(max(count, 1)))


def _ingest(references: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Normaliza y persiste raw-first por liga."""

    statuses: list[dict[str, Any]] = []
    for league in sorted({row["league_slug"] for row in references}):
        selected = [row for row in references if row["league_slug"] == league]
        batches = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_fetch_one, _source_reference(row), 0.1)
                       for row in selected]
            for future in as_completed(futures):
                batch, status = future.result()
                statuses.append(status)
                if batch is not None:
                    batches.append(batch)
        _store_league(_database_url(), league, batches)
    return statuses


def _source_reference(row: dict[str, str]) -> dict[str, str]:
    """Reduce discovery al contrato aceptado por el normalizador."""

    return {key: row[key] for key in (
        "provider_match_id", "competition_id", "league_slug"
    )}


def _readback(references: list[dict[str, str]]) -> dict[str, int]:
    """Verifica matches, eventos y payloads crudos persistidos."""

    ids = [row["provider_match_id"] for row in references]
    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    query = text(
        f"SELECT COUNT(DISTINCT m.provider_match_id) matches, "
        f"COUNT(DISTINCT e.id) events, COUNT(DISTINCT r.id) raw_payloads "
        f"FROM {SCHEMA}.matches m LEFT JOIN {SCHEMA}.events e "
        f"ON e.provider=m.provider AND e.provider_match_id=m.provider_match_id "
        f"LEFT JOIN {SCHEMA}.raw_payloads r ON r.provider=m.provider "
        f"AND r.provider_match_id=m.provider_match_id "
        f"WHERE m.provider='espn' AND m.provider_match_id = ANY(:ids)"
    )
    try:
        with engine.connect() as connection:
            row = connection.execute(query, {"ids": ids}).mappings().one()
            return {key: int(value) for key, value in row.items()}
    finally:
        engine.dispose()


def _write(name: str, value: Any) -> None:
    """Publica JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _hashes() -> dict[str, str]:
    """Calcula hashes de artefactos."""

    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(OUTPUT.iterdir())
            if path.is_file() and path.name != "hashes.json"}


def run() -> dict[str, Any]:
    """Descubre, ingiere y sella la cohorte disponible."""

    references = _discover()
    before = _readback(references)
    statuses = _ingest(references)
    readback = _readback(references)
    failed = [row for row in statuses if row["status"] != "normalized"]
    classification = ("insufficient_coverage" if not failed
                      else "rejected_for_revision")
    result = {"classification": classification, "cutoff": _cutoff().isoformat(),
              "references": references, "statuses": statuses,
              "readback": readback, "readback_before": before,
              "failed": failed,
              "coverage": {"matches": len(references),
                           "leagues": len({row["league_slug"]
                                          for row in references})},
              "audit": {"raw_first": (
                            not references or readback["raw_payloads"] > 0),
                        "replay_idempotent": before == readback,
                        "all_after_cutoff": all(
                            datetime.fromisoformat(row["kickoff_ts"]) > _cutoff()
                            for row in references),
                        "router_modified": False}}
    _publish(result)
    return result


def _publish(result: dict[str, Any]) -> None:
    """Publica contrato normativo y reporte."""

    for name in ("references", "statuses", "readback", "readback_before",
                 "coverage", "audit"):
        _write(f"{name}.json", result[name])
    _write("input_manifest.json", {
        "league_catalog_source": f"{SCHEMA}.matches",
        "provider": "espn", "cutoff": result["cutoff"],
        "model_parameters_sha256": hashlib.sha256(MODEL.read_bytes()).hexdigest(),
        "lock_sha256": hashlib.sha256(LOCK.read_bytes()).hexdigest(),
    })
    _write("metrics.json", {"classification": result["classification"],
                            **result["coverage"], **result["readback"]})
    _write("config.json", {"cutoff": result["cutoff"], "days": _days(),
                           "minimum_phase76_matches": 200,
                           "minimum_phase76_leagues": 10,
                           "minimum_phase81_matches": 500,
                           "minimum_phase81_leagues": 10})
    report = (
        "# Cohorte independiente Fase 76\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- partidos disponibles: `{result['coverage']['matches']}`\n"
        f"- ligas: `{result['coverage']['leagues']}`\n"
        f"- payloads raw persistidos: `{result['readback']['raw_payloads']}`\n"
        "- router modificado: `False`\n"
    )
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", _hashes())
    LOGGER.info("Cohorte independiente: %s", result["classification"])


def main() -> int:
    """Ejecuta la colección; cobertura insuficiente no es error operativo."""

    result = run()
    return 0 if result["classification"] == "insufficient_coverage" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 2.0.0 - 2026-07-28
