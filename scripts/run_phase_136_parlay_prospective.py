"""Congela y liquida prospectivamente el Constructor de Parlays (Fase 136).

Convierte el gate histórico de Fase 135 en confirmación prospectiva. Cada ciclo:

1. Pide las predicciones del día, aplica el gate sellado y **congela** las
   piernas elegibles cuyo partido todavía no arranca.
2. Materializa combinaciones de referencia deterministas sobre esas piernas,
   también antes de cualquier kickoff.
3. Liquida las piernas cuyo fixture ya tiene fila en `prediction_settlements`
   -esa fila certifica estado final, marcador reconciliado y `kickoff + 3h`-.
4. Cierra los parlays cuyas piernas están todas resueltas.

Por qué las combinaciones se fijan antes: el número que Fase 135 promete es el
ratio de entrega del conjunto. Si la combinación se eligiera después de conocer
resultados, ese ratio no mediría nada. Es el mismo principio con el que Fase 86
materializa su baseline antes del kickoff.

# Requirements:
#   python-dotenv>=1
#   requests>=2.31
#   sqlalchemy>=2

Version: 1.0.0
Created: 2026-08-21
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.parlay_eligibility_v1 import ParlayEligibilityView  # noqa: E402
from src.parlay_settlement import (  # noqa: E402
    ParlayBase,
    ParlayRepository,
    SqlAlchemyParlayRepository,
    prospective_delivery,
    run_prospective_cycle,
)
from src.settlement_store import (  # noqa: E402
    build_repository as build_settlement_repository,
)
from src.telegram_bot import (  # noqa: E402
    DikamahaHttpGateway,
    PredictionGatewayError,
    telegram_config_from_env,
)

LOGGER = logging.getLogger(__name__)
DATABASE = ROOT / "data" / "phase_136" / "parlay_prospective.sqlite"


def _parser() -> argparse.ArgumentParser:
    """Define la interfaz del runner."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="Fecha YYYYMMDD; por omisión, hoy en UTC.")
    parser.add_argument("--limit", type=int, default=30,
                        help="Máximo de fixtures a evaluar por ciclo.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Usa SQLite en memoria y no persiste nada.")
    parser.add_argument("--report-only", action="store_true",
                        help="No congela ni liquida: sólo publica el reporte.")
    return parser


def _repository(dry_run: bool) -> ParlayRepository:
    """Construye el store, en memoria si es una corrida en seco."""

    if dry_run:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool, future=True)
    else:
        DATABASE.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{DATABASE}", future=True)
    ParlayBase.metadata.create_all(engine)
    return SqlAlchemyParlayRepository(
        sessionmaker(bind=engine, expire_on_commit=False, class_=Session))


def _settlements(dry_run: bool) -> Any:
    """Abre el store de Fase 118, o `None` si no hay base configurada."""

    if dry_run:
        return None
    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        LOGGER.warning("DATABASE_URL ausente: se congela sin liquidar")
        return None
    try:
        return build_settlement_repository(database_url)
    except Exception:  # noqa: BLE001 - degradar a sólo congelar es preferible
        LOGGER.warning("store de liquidación no disponible", exc_info=True)
        return None


def _report(repository: ParlayRepository) -> dict[str, Any]:
    """Publica el contraste prospectivo entre declarado y entregado."""

    report = prospective_delivery(
        repository.frozen_parlays(), repository.parlay_settlements())
    LOGGER.info("Reporte prospectivo: %s", json.dumps(report, default=str))
    return report


def _run(args: argparse.Namespace) -> int:
    """Ejecuta un ciclo completo."""

    view = ParlayEligibilityView()
    if not view.available():
        LOGGER.error("gate de Fase 135 no disponible: nada que congelar")
        return 1
    repository = _repository(args.dry_run)
    if args.report_only:
        _report(repository)
        return 0
    gateway = DikamahaHttpGateway(telegram_config_from_env())
    try:
        counts = run_prospective_cycle(
            gateway, view, repository, _settlements(args.dry_run),
            args.date, args.limit)
    except PredictionGatewayError:
        LOGGER.error("no se pudo leer el menú del día", exc_info=True)
        return 1
    LOGGER.info("phase136_cycle_completed counts=%s", json.dumps(counts))
    _report(repository)
    return 0


def main() -> int:
    """Punto de entrada."""

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return _run(_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-08-21
