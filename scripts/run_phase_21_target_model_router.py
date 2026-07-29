"""Ejecutor de Fase 21: selector de modelo por target."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.phase_21_target_model_router import run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    raise SystemExit(0 if result["audit"]["coverage_complete"] else 1)

# Version: 1.0.0
# Created: 2026-07-26
