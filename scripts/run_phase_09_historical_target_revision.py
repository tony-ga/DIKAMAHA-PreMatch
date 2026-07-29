"""Ejecutor de Fase 09: extensión histórica y targets temporales v2.

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

from src.phase_09_historical_target_revision import run


def main() -> int:
    """Carga configuración local y ejecuta la auditoría read-only."""

    load_dotenv(ROOT / ".env")
    result = run(os.environ.get("DATABASE_URL", ""))
    return 0 if result["audit"]["classification"] == "validated_for_target_revision" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-26
