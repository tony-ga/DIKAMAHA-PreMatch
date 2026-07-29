"""Ejecutor reproducible de Fase 7.16.

Version: 1.0.0
Created: 2026-07-16
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - el entorno de producción ya fija variables
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if load_dotenv:
    load_dotenv(ROOT / ".env")
from src.evaluate_prospective_espn import main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
