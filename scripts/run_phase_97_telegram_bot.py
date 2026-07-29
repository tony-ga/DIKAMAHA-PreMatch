"""Inicia el bot Telegram shadow de DIKAMAHA.

# Requirements:
# requests>=2.31
# tenacity>=8.2

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.telegram_bot import (  # noqa: E402
    DikamahaHttpGateway,
    LongPollingRunner,
    TelegramHttpTransport,
    TelegramPredictionBot,
    telegram_config_from_env,
)


def main() -> None:
    """Carga entorno y ejecuta long polling privado."""

    load_dotenv(ROOT / ".env")
    config = telegram_config_from_env()
    transport = TelegramHttpTransport(config)
    gateway = DikamahaHttpGateway(config)
    bot = TelegramPredictionBot(config, transport, gateway)
    LongPollingRunner(
        bot, transport, config.poll_timeout_seconds).run_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()


# Version: 1.0.0
# Created: 2026-07-29
