"""Ejecutor del gate read-only de la cohorte prospectiva."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependencia fijada en el entorno
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if load_dotenv:
    load_dotenv(ROOT / ".env")

from src.phase_31_prospective_cohort_gate import main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-26
