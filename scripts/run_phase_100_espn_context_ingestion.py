"""Ejecuta ingesta ESPN raw-first incremental de contexto para Fase 100.

Requirements:
    requests>=2.31
    sqlalchemy>=2
    tenacity>=8.2

Version: 1.0.0
Created: 2026-07-29
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.espn_context_ingestion import (  # noqa: E402
    EspnContextIngestionService,
    IngestionConfig,
)
from src.espn_prospective_connector import (  # noqa: E402
    EspnConnectorConfig,
    EspnProspectiveConnector,
)
from src.espn_raw_first_provider import EspnRawFirstProvider  # noqa: E402
from src.espn_user_explorer import LEAGUES  # noqa: E402
from src.prematch_raw_store import (  # noqa: E402
    PrematchRawBase,
    SqlAlchemyRawResponseRepository,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts" / "phase_100_espn_context_enrichment"
DEFAULT_STORE = ROOT / "data" / "phase_100" / "raw_responses.sqlite"
CACHE = ROOT / "data" / "cache" / "espn_phase100"


def _parser() -> argparse.ArgumentParser:
    """Construye la interfaz de ejecución incremental y explícita."""

    parser = argparse.ArgumentParser(description="Fase 100: ingesta ESPN raw-first")
    parser.add_argument("--start-date", default=date.today().isoformat())
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-leagues", type=int, default=len(LEAGUES))
    parser.add_argument("--max-teams-per-league", type=int)
    parser.add_argument("--max-athletes-per-league", type=int)
    parser.add_argument("--athlete-profiles", action="store_true")
    parser.add_argument("--include-financial", action="store_true")
    parser.add_argument("--include-live", action="store_true")
    parser.add_argument("--include-settlement", action="store_true")
    parser.add_argument("--database-url", default=os.getenv("DIKAMAHA_CONTEXT_DATABASE_URL"))
    return parser


def _dates(start: str, days: int) -> list[str]:
    """Convierte un rango ISO a las fechas compactas requeridas por ESPN."""

    try:
        first = date.fromisoformat(start)
    except ValueError as error:
        raise ValueError("start_date_must_be_ISO") from error
    if days < 1 or days > 31:
        raise ValueError("days_must_be_between_1_and_31")
    return [(first + timedelta(days=index)).strftime("%Y%m%d") for index in range(days)]


def _database_url(value: str | None) -> str:
    """Elige PostgreSQL explícito o un ledger SQLite aislado y persistente."""

    if value:
        return value
    DEFAULT_STORE.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{DEFAULT_STORE}"


def _repository(database_url: str) -> SqlAlchemyRawResponseRepository:
    """Crea el ledger raw-first mediante SQLAlchemy y transacciones del puerto."""

    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    PrematchRawBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlAlchemyRawResponseRepository(factory)


def _config(args: argparse.Namespace) -> IngestionConfig:
    """Traduce flags sin habilitar clases sensibles por defecto."""

    return IngestionConfig(
        include_financial=args.include_financial, include_live=args.include_live,
        include_settlement=args.include_settlement,
        include_athlete_profiles=args.athlete_profiles,
        max_teams_per_league=args.max_teams_per_league,
        max_athletes_per_league=args.max_athletes_per_league,
    )


def _leagues(maximum: int) -> list[str]:
    """Selecciona el catálogo navegable sin inventar ligas no soportadas."""

    if maximum < 1 or maximum > len(LEAGUES):
        raise ValueError("max_leagues_out_of_range")
    return [slug for slug, _ in LEAGUES[:maximum]]


def _provider(league: str, repository: SqlAlchemyRawResponseRepository) -> EspnRawFirstProvider:
    """Compone transporte con retry/caché y persistencia raw-first por liga."""

    connector = EspnProspectiveConnector(EspnConnectorConfig(
        league=league, cache_dir=CACHE / league, cache_ttl_seconds=3600))
    return EspnRawFirstProvider(connector, repository)


def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta la captura multi-liga y conserva errores por recurso."""

    repository, dates, config = _repository(_database_url(args.database_url)), _dates(args.start_date, args.days), _config(args)
    reports = []
    for league in _leagues(args.max_leagues):
        service = EspnContextIngestionService(_provider(league, repository), config)
        reports.append(service.ingest_league(league, dates))
    return {"phase": 100, "dates": dates, "reports": reports,
            "raw_first": True, "router_modified": False,
            "model_features_created": False}


def _write_artifact(result: dict[str, Any]) -> Path:
    """Escribe evidencia reproducible sin incluir payloads ni secretos."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / "latest_ingestion_report.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return path


def main() -> int:
    """Ejecuta el colector y devuelve código de proceso verificable."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        result = _run(_parser().parse_args())
    except (OSError, ValueError) as error:
        LOGGER.error("phase100_ingestion_failed error=%s", error)
        return 2
    artifact = _write_artifact(result)
    LOGGER.info("phase100_ingestion_completed artifact=%s leagues=%s", artifact, len(result["reports"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-29
