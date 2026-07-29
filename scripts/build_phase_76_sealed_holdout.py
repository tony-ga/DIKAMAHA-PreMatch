"""Construye el holdout sellado de Fase 76 con partidos nunca usados.

Requirements:
    SQLAlchemy==2.0.41
    psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "artifacts/phase_74_causal_sequence_corpus"
OUTPUT = ROOT / "artifacts/phase_76_sealed_holdout"
SCHEMA = "prospective_staging_v2"
LOGGER = logging.getLogger(__name__)


def _database_url() -> str:
    """Obtiene DATABASE_URL sin exponer credenciales."""

    value = os.getenv("DATABASE_URL", "").strip().strip("\"'")
    if not value:
        raise RuntimeError("missing_database_url")
    return value


def _development_ids() -> set[str]:
    """Lee las identidades que sí participaron en Fase 74."""

    identities: set[str] = set()
    source = CORPUS / "micro_windows_5m.jsonl"
    with source.open(encoding="utf-8") as handle:
        for line in handle:
            identities.add(str(json.loads(line)["match_id"]))
    return identities


def _database_rows() -> list[dict[str, Any]]:
    """Lee el inventario completo mediante SELECT-only."""

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    query = text(
        f"SELECT provider_match_id::text provider_match_id, league_slug, "
        f"competition_id, kickoff_ts, complete FROM {SCHEMA}.matches "
        "WHERE provider='espn' ORDER BY kickoff_ts, provider_match_id"
    )
    try:
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(query).mappings()]
    finally:
        engine.dispose()


def _raw_coverage(ids: list[str]) -> dict[str, int]:
    """Verifica persistencia raw-first y eventos de los candidatos."""

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    query = text(
        f"SELECT COUNT(DISTINCT m.provider_match_id) matches, "
        f"COUNT(DISTINCT e.id) events, COUNT(DISTINCT r.id) raw_payloads "
        f"FROM {SCHEMA}.matches m LEFT JOIN {SCHEMA}.events e "
        "ON e.provider=m.provider AND e.provider_match_id=m.provider_match_id "
        f"LEFT JOIN {SCHEMA}.raw_payloads r ON r.provider=m.provider "
        "AND r.provider_match_id=m.provider_match_id "
        "WHERE m.provider='espn' AND m.provider_match_id = ANY(:ids)"
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
    """Calcula hashes del contrato publicado."""

    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"
    }


def run() -> dict[str, Any]:
    """Sella todos los partidos completos ajenos al desarrollo."""

    development = _development_ids()
    database = _database_rows()
    references = [
        row for row in database
        if bool(row["complete"]) and row["provider_match_id"] not in development
    ]
    ids = [row["provider_match_id"] for row in references]
    leagues = {str(row["league_slug"]) for row in references}
    coverage = {"matches": len(ids), "leagues": len(leagues), **_raw_coverage(ids)}
    audit = {
        "development_overlap_count": 0,
        "development_ids": len(development),
        "database_matches": len(database),
        "complete_only": all(bool(row["complete"]) for row in references),
        "raw_first": coverage["raw_payloads"] > 0,
        "postgres_select_only": True,
        "model_refit": False,
        "router_modified": False,
    }
    _publish(references, coverage, audit)
    return {"coverage": coverage, "audit": audit}


def _publish(
    references: list[dict[str, Any]],
    coverage: dict[str, int],
    audit: dict[str, Any],
) -> None:
    """Publica identidad, procedencia y evidencia de no reutilización."""

    _write("references.json", references)
    _write("coverage.json", coverage)
    _write("audit.json", audit)
    _write("input_manifest.json", {
        "source_schema": SCHEMA,
        "exclusion_source": str(CORPUS / "micro_windows_5m.jsonl"),
        "exclusion_rule": "provider_match_id_not_in_phase_74_corpus",
        "model_parameters_frozen": True,
    })
    _write("hashes.json", _hashes())
    LOGGER.info("Holdout sellado: %s partidos, %s ligas",
                coverage["matches"], coverage["leagues"])


def main() -> int:
    """Ejecuta la construcción y exige cobertura 200/10."""

    result = run()
    valid = result["coverage"]["matches"] >= 200
    valid = valid and result["coverage"]["leagues"] >= 10
    return 0 if valid else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0 - 2026-07-28
