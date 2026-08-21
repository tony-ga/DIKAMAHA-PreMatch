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
from datetime import datetime, timezone
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
    freeze_from_leg,
    prospective_delivery,
    reference_parlays,
    resolve_leg,
    settle_ready_parlays,
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


def _freeze(
    gateway: Any, view: ParlayEligibilityView, repository: ParlayRepository,
    date: str | None, limit: int, now: datetime,
) -> dict[str, int]:
    """Congela piernas elegibles y sus parlays de referencia."""

    response = gateway.upcoming(date=date, limit=limit)
    predictions = response.get("predictions") or response.get("fixtures") or []
    menu = view.menu(predictions)
    if menu["status"] != "available":
        LOGGER.warning("gate no disponible: %s", menu.get("reason"))
        return {"frozen_legs": 0, "frozen_parlays": 0, "skipped_started": 0,
                "skipped_invalid": 0, "candidates": 0}
    sha = str(menu["criteria_sha256"])
    frozen_legs, skipped_started, skipped_invalid = [], 0, 0
    for match in menu["matches"]:
        for leg in match["legs"]:
            try:
                record = freeze_from_leg(leg, match, sha, now)
            except (KeyError, TypeError, ValueError):
                skipped_invalid += 1
                continue
            if record.kickoff_ts <= now:
                skipped_started += 1
                continue
            if repository.freeze_leg_if_absent(record):
                frozen_legs.append(record)
    day = now.date().isoformat()
    pool = repository.legs_frozen_today(day)
    delivery = _delivery_table(view)
    parlays = reference_parlays(pool, delivery, sha, now) if pool else []
    frozen_parlays = sum(
        1 for parlay in parlays if repository.freeze_parlay_if_absent(parlay))
    return {
        "frozen_legs": len(frozen_legs), "frozen_parlays": frozen_parlays,
        "skipped_started": skipped_started, "skipped_invalid": skipped_invalid,
        "candidates": menu["legs"],
    }


def _delivery_table(view: ParlayEligibilityView) -> dict[str, float]:
    """Ratio de entrega histórico por número de piernas, para congelarlo."""

    try:
        config = view._load()  # noqa: SLF001 - lectura del gate ya validado
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return {legs: value["ratio"] for legs, value in config["delivery"].items()}


def _settle(
    gateway: Any, repository: ParlayRepository, settlements: Any,
    now: datetime,
) -> dict[str, int]:
    """Liquida piernas ya reconciliadas y cierra los parlays completos."""

    settled = still_pending = failed = 0
    if settlements is not None:
        for leg in repository.unsettled_legs(now):
            try:
                settlement = settlements.get(leg.fixture_key)
                if settlement is None:
                    still_pending += 1
                    continue
                statistics = gateway.explorer_statistics(
                    leg.league_slug, str(leg.match_id),
                    settlement.competition_id)
                record = resolve_leg(leg, statistics, settlement.settled_at)
                if record is None:
                    failed += 1
                    LOGGER.warning(
                        "phase136_settle_failed leg_key=%s reason=unresolved "
                        "metric=%s team_side=%s period=%s",
                        leg.leg_key, leg.metric, leg.team_side, leg.period)
                    continue
                if repository.settle_leg_if_absent(record):
                    settled += 1
            except PredictionGatewayError:
                failed += 1
                LOGGER.warning(
                    "phase136_settle_failed leg_key=%s reason=gateway_error",
                    leg.leg_key)
            except Exception:  # noqa: BLE001 - una pierna rota no bloquea al resto
                failed += 1
                LOGGER.warning(
                    "phase136_settle_row_failed leg_key=%s", leg.leg_key,
                    exc_info=True)
    counts = settle_ready_parlays(repository, now)
    return {
        "settled_legs": settled, "legs_still_pending": still_pending,
        "failed_legs": failed, "settled_parlays": counts["settled"],
        "parlays_still_pending": counts["still_pending"],
    }


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
    now = datetime.now(timezone.utc)
    gateway = DikamahaHttpGateway(telegram_config_from_env())
    try:
        freeze_counts = _freeze(
            gateway, view, repository, args.date, args.limit, now)
    except PredictionGatewayError:
        LOGGER.error("no se pudo leer el catálogo del día", exc_info=True)
        return 1
    settle_counts = _settle(gateway, repository, _settlements(args.dry_run), now)
    LOGGER.info("congelación: %s", json.dumps(freeze_counts))
    LOGGER.info("liquidación: %s", json.dumps(settle_counts))
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
