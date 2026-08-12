"""Congela y liquida prospectivamente el menú de mayor probabilidad.

Fase 123. Convierte la evidencia histórica post-hoc de Fase 122 en
confirmación prospectiva: cada ciclo (a) congela los picks vigentes de
`GET /v1/high-probability` para fixtures que todavía no arrancan, y (b)
intenta liquidar los picks ya congelados cuyo fixture ya tiene una fila en
`prediction_settlements` (Fase 118) -esa fila certifica estado final, marcador
reconciliado y `kickoff + 3h`, así que este script no repite esa espera-.

# Requirements:
#   python-dotenv>=1
#   requests>=2.31
#   sqlalchemy>=2

Version: 1.0.0
Created: 2026-08-12
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.telegram_bot import (  # noqa: E402
    DikamahaHttpGateway,
    PredictionGatewayError,
    telegram_config_from_env,
)
from src.settlement_store import (  # noqa: E402
    build_repository as build_settlement_repository,
)
from src.high_probability_settlement import (  # noqa: E402
    HighProbabilityPickRepository,
    PickSettlementBase,
    SqlAlchemyHighProbabilityPickRepository,
    prospective_reliability,
    run_prospective_cycle,
)
from src.runtime_logging import configure_runtime_logging  # noqa: E402

LOGGER = logging.getLogger(__name__)
DATABASE = ROOT / "data" / "phase_123" / "high_probability_prospective.sqlite"


def _parser() -> argparse.ArgumentParser:
    """Construye flags operativos acotados, igual que Fase 101."""

    parser = argparse.ArgumentParser(
        description="Fase 123: validación prospectiva del menú de mayor probabilidad")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", type=str, default=None,
                        help="Fecha YYYYMMDD a congelar; por defecto hoy")
    parser.add_argument("--report", action="store_true",
                        help="Imprime la fiabilidad prospectiva acumulada")
    parser.add_argument("--interval-seconds", type=int, default=None)
    return parser


def _database_engine() -> Any:
    """Elige PostgreSQL cuando existe, igual que el resto de Fase 118/101."""

    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        LOGGER.info("phase123_pick_store_backend backend=postgresql")
        return create_engine(database_url, future=True, pool_pre_ping=True)
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.warning(
        "phase123_pick_store_backend backend=sqlite_local path=%s "
        "reason=DATABASE_URL_missing", DATABASE)
    return create_engine(f"sqlite+pysqlite:///{DATABASE}", future=True)


def _repository(dry_run: bool) -> HighProbabilityPickRepository:
    """Crea el repositorio de picks, aislado en memoria para dry-run."""

    if dry_run:
        engine = create_engine(
            "sqlite+pysqlite://", future=True,
            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        engine = _database_engine()
    PickSettlementBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlAlchemyHighProbabilityPickRepository(factory)


def _report(repository: HighProbabilityPickRepository) -> None:
    """Imprime la fiabilidad prospectiva acumulada, sin decidir promoción."""

    frozen = repository.frozen_on_date(
        datetime.now(timezone.utc).date(), timezone.utc)
    settled = repository.settled_recent(500)
    summary = prospective_reliability(frozen, settled)
    LOGGER.info("phase123_prospective_reliability summary=%s", summary)


def _cycle(
    gateway: DikamahaHttpGateway, repository: HighProbabilityPickRepository,
    settlements: Any, date: str | None,
) -> dict[str, Any]:
    """Ejecuta un ciclo completo y registra el resultado."""

    result = run_prospective_cycle(gateway, repository, settlements, date)
    LOGGER.info("phase123_cycle_completed counts=%s", result)
    return result


def _settlements(dry_run: bool) -> Any:
    """Conecta el historial de Fase 118 sólo si hay base de datos configurada.

    Sin `DATABASE_URL` no hay forma de encontrar el veredicto ya sellado por
    `TelegramChannelPublisher`, así que liquidar degrada a `None` -este ciclo
    sigue congelando picks, sólo deja de intentar liquidarlos- en vez de
    apuntar a un almacén en memoria que nunca podría contener nada.
    """

    if dry_run:
        return None
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        LOGGER.warning("phase123_settlement_store_unconfigured")
        return None
    try:
        return build_settlement_repository(database_url)
    except Exception as error:  # noqa: BLE001 - el runner no debe caer por esto
        LOGGER.warning(
            "phase123_settlement_store_unavailable error=%s",
            type(error).__name__)
        return None


def _run(args: argparse.Namespace) -> int:
    """Mantiene el runner vivo o ejecuta una sola iteración."""

    interval = max(
        60, args.interval_seconds
        or int(os.getenv("HIGH_PROBABILITY_PROSPECTIVE_POLL_SECONDS", "1800")))
    config = telegram_config_from_env()
    gateway = DikamahaHttpGateway(config)
    repository = _repository(args.dry_run)
    settlements = _settlements(args.dry_run)
    while True:
        try:
            _cycle(gateway, repository, settlements, args.date)
            if args.report:
                _report(repository)
        except (PredictionGatewayError, SQLAlchemyError, ValueError, OSError) as error:
            LOGGER.exception("phase123_cycle_failed error=%s", error)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(interval)


def main() -> int:
    """Carga entorno local y arranca el runner."""

    load_dotenv(ROOT / ".env")
    configure_runtime_logging()
    try:
        return _run(_parser().parse_args())
    except KeyboardInterrupt:
        LOGGER.info("phase123_prospective_runner_stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-08-12
