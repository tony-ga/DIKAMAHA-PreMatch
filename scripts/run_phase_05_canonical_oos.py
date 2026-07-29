"""Ejecutor de la suite canónica OOS de DIKAMAHA.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.canonical_oos_predictions_v1 import run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(0 if run()["classification"] == "ready_for_evaluation" else 1)

# Version: 1.0.0
# Created: 2026-07-26
