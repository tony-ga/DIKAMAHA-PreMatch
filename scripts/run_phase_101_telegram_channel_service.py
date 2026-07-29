"""Arranca el servicio permanente y autocontenido del canal Telegram.

# Requirements:
#   python-dotenv>=1
#   requests>=2.31
#   uvicorn>=0.23

Version: 1.0.0
Created: 2026-07-29
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.telegram_channel_service import (  # noqa: E402
    ManagedDikamahaApi,
    TelegramChannelService,
)
from src.runtime_logging import configure_runtime_logging  # noqa: E402

LOGGER = logging.getLogger(__name__)


def _termination_signal(_: int, __: object) -> None:
    """Convierte SIGTERM del orquestador en cierre controlado."""

    raise KeyboardInterrupt


def main() -> int:
    """Carga configuración y mantiene API y publicador bajo un proceso."""

    load_dotenv(ROOT / ".env")
    configure_runtime_logging()
    signal.signal(signal.SIGTERM, _termination_signal)
    port = os.getenv("PORT", os.getenv("DIKAMAHA_PORT", "8000"))
    base_url = os.getenv(
        "DIKAMAHA_BOT_API_URL", f"http://127.0.0.1:{port}")
    try:
        with ManagedDikamahaApi(ROOT, base_url):
            return TelegramChannelService(ROOT).run()
    except (OSError, RuntimeError, TimeoutError) as error:
        LOGGER.exception("telegram_channel_service_failed error=%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-29
