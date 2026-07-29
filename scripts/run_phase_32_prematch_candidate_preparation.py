"""Ejecutor de preparación causal pre-match."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase_32_prematch_candidate_preparation import run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()

# Version: 1.0.0
# Created: 2026-07-26
