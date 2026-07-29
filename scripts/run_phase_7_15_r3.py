"""Ejecuta Fase 7.15-R3 con bandera explícita de escritura staging.

Version: 1.0.0
Created: 2026-07-16
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if load_dotenv:
    load_dotenv(ROOT / ".env")

from src.espn_phase_7_15_r3 import main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main(["--enable-staging-write"]))

# Version: 1.0.0
# Created: 2026-07-16
