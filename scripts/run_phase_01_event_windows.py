"""Ejecutor de la Fase 01 de ventanas históricas DIKAMAHA.

Requirements:
    - python-dotenv

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.event_windows_v1 import run


def main() -> int:
    """Carga configuración local y ejecuta la materialización read-only."""
    load_dotenv(ROOT / ".env")
    result = run(os.environ["DATABASE_URL"])
    return 0 if result["classification"] == "ready_for_next_phase" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-26
