"""Inicia el bot premium de Telegram como servicio Railway independiente.

# Requirements:
# requests>=2.31
# tenacity>=8.2

Version: 2.1.0
Created: 2026-07-29
"""
from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.telegram_bot import (  # noqa: E402
    DikamahaHttpGateway,
    LongPollingRunner,
    TelegramBotConfig,
    TelegramHttpTransport,
    TelegramPredictionBot,
    telegram_config_from_env,
)
from src.runtime_logging import configure_runtime_logging  # noqa: E402


def _validate_premium_config(config: TelegramBotConfig) -> None:
    """Valida que el acceso elegido falle cerrado."""

    if config.access_mode == "private" and not config.allowed_user_ids:
        raise ValueError("telegram_premium_allowlist_missing")
    if not config.dikamaha_api_key:
        raise ValueError("dikamaha_api_key_missing")
    if not config.dikamaha_base_url.startswith("https://"):
        raise ValueError("dikamaha_bot_api_url_https_required")


def _stop_on_signal(signum: int, _frame: object) -> None:
    """Convierte la terminación del orquestador en un cierre limpio."""

    logging.getLogger(__name__).info("telegram_premium_stop signal=%s", signum)
    raise KeyboardInterrupt


def main() -> int:
    """Valida dependencias y ejecuta long polling premium."""

    load_dotenv(ROOT / ".env")
    configure_runtime_logging()
    config = telegram_config_from_env()
    _validate_premium_config(config)
    transport = TelegramHttpTransport(config)
    gateway = DikamahaHttpGateway(config)
    readiness = gateway.readiness()
    if readiness.get("ready") is not True:
        raise RuntimeError("dikamaha_api_not_ready")
    bot = TelegramPredictionBot(config, transport, gateway)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, _stop_on_signal)
    logging.getLogger(__name__).info(
        "telegram_premium_started access_mode=%s allowed_users=%s",
        config.access_mode, len(config.allowed_user_ids))
    try:
        LongPollingRunner(
            bot, transport, config.poll_timeout_seconds).run_forever()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("telegram_premium_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 2.1.0
# Created: 2026-07-29
