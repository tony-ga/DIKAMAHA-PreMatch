"""Reingiere los timelines inconsistentes del holdout sellado de Fase 76.

Requirements:
    requests
    tenacity
    SQLAlchemy==2.0.41
    psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_multileague_ingestion import _store_league  # noqa: E402
from src.espn_phase_7_15_r3 import _normalize  # noqa: E402
from src.espn_prospective_connector import (  # noqa: E402
    EspnConnectorConfig,
    EspnProspectiveConnector,
)

EVALUATION = ROOT / "artifacts/phase_76_independent_cohort_evaluation"
OUTPUT = ROOT / "artifacts/phase_76_sealed_holdout"
SCHEMA = "prospective_staging_v2"
LOGGER = logging.getLogger(__name__)


def _database_url() -> str:
    """Obtiene DATABASE_URL sin exponerla."""

    value = os.getenv("DATABASE_URL", "").strip().strip("\"'")
    if not value:
        raise RuntimeError("missing_database_url")
    return value


def _rejected_ids() -> list[str]:
    """Lee las identidades cuya secuencia no reconcilió."""

    audit = json.loads((EVALUATION / "audit.json").read_text())
    return [str(value) for value in audit["score_mismatch_ids"]]


def _references(ids: list[str]) -> list[dict[str, str]]:
    """Recupera referencias fuente de los partidos dañados."""

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    query = text(
        f"SELECT provider_match_id::text provider_match_id, league_slug, "
        f"competition_id FROM {SCHEMA}.matches WHERE provider='espn' "
        "AND provider_match_id = ANY(:ids)"
    )
    try:
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(
                query, {"ids": ids}).mappings()]
    finally:
        engine.dispose()


def _fetch(
    references: list[dict[str, str]],
) -> tuple[dict[str, list[Any]], list[dict[str, Any]]]:
    """Descarga en paralelo y agrupa lotes normalizados por liga."""

    batches: dict[str, list[Any]] = {}
    statuses: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(_fetch_one_fresh, row): row
            for row in references
        }
        for future in as_completed(futures):
            batch, status = future.result()
            statuses.append(status)
            if batch is not None:
                league = str(futures[future]["league_slug"])
                batches.setdefault(league, []).append(batch)
    return batches, statuses


def _fetch_one_fresh(
    reference: dict[str, str],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Descarga sin reutilizar la caché truncada de Fase 37."""

    league = reference["league_slug"]
    connector = EspnProspectiveConnector(EspnConnectorConfig(
        league=league,
        cache_dir=ROOT / "data/cache/phase_76_repair" / league,
        cache_ttl_seconds=0,
    ))
    try:
        batch, public = _normalize(connector, reference)
        status = {"league_slug": league,
                  "match_id": reference["provider_match_id"],
                  "status": "normalized",
                  "event_count": public["event_count"]}
        return batch, status
    except (OSError, ValueError, RuntimeError) as error:
        LOGGER.error("Reparación fallida league=%s match=%s type=%s",
                     league, reference["provider_match_id"],
                     type(error).__name__)
        return None, {"league_slug": league,
                      "match_id": reference["provider_match_id"],
                      "status": "failed",
                      "error_type": type(error).__name__}


def _persist(batches: dict[str, list[Any]]) -> None:
    """Persiste raw-first mediante el repositorio oficial de staging."""

    for league, rows in sorted(batches.items()):
        _store_league(_database_url(), league, rows)


def _write(statuses: list[dict[str, Any]]) -> None:
    """Publica el resultado sin incluir payloads ni secretos."""

    value = {
        "requested": len(statuses),
        "normalized": sum(row["status"] == "normalized" for row in statuses),
        "failed": [row for row in statuses if row["status"] != "normalized"],
    }
    (OUTPUT / "repair_status.json").write_text(
        json.dumps(value, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def run() -> dict[str, int]:
    """Repara todas las secuencias identificadas por la evaluación."""

    references = _references(_rejected_ids())
    batches, statuses = _fetch(references)
    _persist(batches)
    _write(statuses)
    result = {
        "requested": len(references),
        "normalized": sum(row["status"] == "normalized" for row in statuses),
    }
    LOGGER.info("Reparación Fase 76: %s", result)
    return result


def main() -> int:
    """Ejecuta reparación y exige normalización completa."""

    result = run()
    return 0 if result["requested"] == result["normalized"] else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0 - 2026-07-28
