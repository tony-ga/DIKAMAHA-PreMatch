"""Inicia la interfaz Discord shadow de DIKAMAHA.

# Requirements:
# pip install -r requirements.discord.txt

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

from src.discord_bot import discord_config_from_env, run_discord_bot  # noqa: E402


def main() -> None:
    """Carga `.env` y ejecuta Discord."""

    load_dotenv(ROOT / ".env")
    run_discord_bot(discord_config_from_env())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()


# Version: 1.0.0
# Created: 2026-07-29
