"""Ejecuta la difusión programada del canal Telegram DIKAMAHA.

# Requirements:
#   python-dotenv>=1
#   requests>=2.31
#   sqlalchemy>=2
#   tenacity>=8.2

Version: 1.3.0
Created: 2026-07-29
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
from src.telegram_channel_publisher import (  # noqa: E402
    ChannelBroadcastBase,
    ChannelPublisherError,
    ChannelTransport,
    SqlAlchemyChannelRepository,
    TelegramChannelPublisher,
    TelegramChannelTransport,
)
from src.settlement_store import (  # noqa: E402
    SettlementRepository,
    build_repository as build_settlement_repository,
)
from src.runtime_logging import configure_runtime_logging  # noqa: E402

LOGGER = logging.getLogger(__name__)
DATABASE = ROOT / "data" / "phase_101" / "telegram_channel.sqlite"


class DryRunTransport(ChannelTransport):
    """Transporte de validación que no llama Telegram."""

    def __init__(self) -> None:
        """Inicializa un contador local."""

        self._messages = 0

    def send_message(self, text: str) -> str:
        """Confirma localmente tamaño y número sin registrar el cuerpo."""

        if len(text) > 3900:
            raise ChannelPublisherError("telegram_message_too_long")
        self._messages += 1
        return f"dry-{self._messages}"


def _parser() -> argparse.ArgumentParser:
    """Construye flags operativos acotados."""

    parser = argparse.ArgumentParser(description="Fase 101: canal Telegram")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mode", choices=("full", "lite"))
    parser.add_argument("--ledger-path", type=Path)
    return parser


def _repository(
    dry_run: bool, ledger_path: Path | None = None,
) -> SqlAlchemyChannelRepository:
    """Crea ledger persistente real o aislado en memoria para dry-run."""

    if dry_run:
        engine = create_engine(
            "sqlite+pysqlite://", future=True,
            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        configured = os.getenv("TELEGRAM_CHANNEL_LEDGER_PATH")
        target = ledger_path or (Path(configured) if configured else DATABASE)
        target.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite+pysqlite:///{target}", future=True)
    ChannelBroadcastBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlAlchemyChannelRepository(factory)


def _transport(dry_run: bool) -> ChannelTransport:
    """Selecciona transporte real o de auditoría."""

    if dry_run:
        return DryRunTransport()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    channel = os.getenv("TELEGRAM_CHANNEL_ID", "@viewtofuture")
    return TelegramChannelTransport(token, channel)


def _settlements(dry_run: bool) -> SettlementRepository | None:
    """Conecta el historial verificado sólo si hay base de datos configurada.

    Ausencia de `DATABASE_URL` degrada a `None` (Fase 118 no persiste
    veredictos, pero el resto del canal sigue publicando) en vez de impedir
    que arranque el worker.
    """

    if dry_run:
        return None
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        LOGGER.warning("phase118_settlement_store_unconfigured")
        return None
    try:
        return build_settlement_repository(database_url)
    except Exception as error:  # noqa: BLE001 - el canal no debe caer por esto
        LOGGER.warning(
            "phase118_settlement_store_unavailable error=%s",
            type(error).__name__)
        return None


def _publisher(
    dry_run: bool, mode: str | None, ledger_path: Path | None,
) -> TelegramChannelPublisher:
    """Compone dependencias sin duplicar la lógica de inferencia."""

    config = telegram_config_from_env()
    gateway = DikamahaHttpGateway(config)
    return TelegramChannelPublisher(
        gateway, _repository(dry_run, ledger_path), _transport(dry_run),
        mode or os.getenv("TELEGRAM_CHANNEL_MODE", "full"),
        settlements=_settlements(dry_run))


def _cycle(publisher: TelegramChannelPublisher) -> dict[str, int]:
    """Ejecuta un ciclo con instante UTC explícito."""

    result = publisher.run_cycle(datetime.now(timezone.utc))
    LOGGER.info("channel_cycle_completed counts=%s", result)
    return result


def _run(args: argparse.Namespace) -> int:
    """Mantiene el worker vivo o ejecuta una sola iteración."""

    publisher = _publisher(args.dry_run, args.mode, args.ledger_path)
    interval = max(60, int(os.getenv("TELEGRAM_CHANNEL_POLL_SECONDS", "300")))
    while True:
        try:
            _cycle(publisher)
        except (
            ChannelPublisherError, PredictionGatewayError, SQLAlchemyError,
            ValueError, OSError,
        ) as error:
            LOGGER.exception("channel_cycle_failed error=%s", error)
        if args.once:
            return 0
        time.sleep(interval)


def main() -> int:
    """Carga entorno local y arranca el worker."""

    load_dotenv(ROOT / ".env")
    configure_runtime_logging()
    try:
        return _run(_parser().parse_args())
    except KeyboardInterrupt:
        LOGGER.info("telegram_channel_worker_stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 1.3.0
# Created: 2026-07-29
