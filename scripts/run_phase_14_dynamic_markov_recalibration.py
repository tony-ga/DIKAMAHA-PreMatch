"""Ejecutor de Fase 14: recalibración dinámica de Markov."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.phase_14_dynamic_markov_recalibration import run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    raise SystemExit(0 if result["audit"]["classification"] in {"promising_unconfirmed", "rejected_for_revision"} else 1)

# Version: 1.0.0
# Created: 2026-07-26
